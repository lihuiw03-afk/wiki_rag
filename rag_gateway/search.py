from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import json
import re
import time
from .config import load_config
from .qmd_client import QMDClient, QMDHit
from .rerank_api import ApiReranker, RerankApiError
from .router import choose_mode, choose_context_policy, build_typed_searches, entity_mentions


@dataclass
class RetrievalHit:
    node_id: str
    score: float
    reason: str
    collection: str
    projection: str
    page_title: str
    section_title: str
    source_path: str
    content: str = ""

    def to_dict(self, include_content: bool = True):
        d = asdict(self)
        if not include_content:
            d.pop("content", None)
        return d


class CompanyRAG:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.config = load_config(self.root)
        pcfg = self.config["project"]
        self.manifest = json.loads((self.root / pcfg["manifest"]).read_text(encoding="utf-8"))
        self.graph = json.loads((self.root / pcfg["graph"]).read_text(encoding="utf-8"))
        self.lexicon = json.loads((self.root / pcfg["lexicon"]).read_text(encoding="utf-8"))
        self.entity_pages = {
            str(n.get("page_title")): str(n.get("page_relpath"))
            for n in self.manifest.get("nodes", {}).values()
            if str(n.get("page_relpath") or "").startswith("wiki/entities/") and n.get("page_title")}
        self.entity_names = sorted(self.entity_pages.keys(), key=len, reverse=True)
        qcfg = self.config["qmd"]
        self.qmd = QMDClient(
            qcfg["base_url"],
            int(qcfg.get("timeout_seconds", 30)),
            bool(qcfg.get("cli_fallback", True)),
        )
        self.rerank_api = ApiReranker(self.config.get("rerank_api") or {})
        self._last_rerank_error: str | None = None

    @staticmethod
    def _projection_from_uri(file: str) -> tuple[str, str]:
        if file.startswith("qmd://"):
            rest = file[6:]
            collection, _, rel = rest.partition("/")
            return collection, rel
        return "", file

    def _node_from_qmd(self, hit: QMDHit) -> str | None:
        collection, rel = self._projection_from_uri(hit.file)
        if collection == "company-core":
            return self.manifest["projection_to_node"].get(rel)
        if collection == "company-evidence":
            return self.manifest["projection_to_node"].get("evidence/" + rel)
        # Full-path fallback for CLI/dev output.
        p = Path(rel)
        try:
            core = (self.root / "rag/qmd/core").resolve()
            evid = (self.root / "rag/qmd/evidence").resolve()
            rp = p.resolve()
            if rp.is_relative_to(core):
                return self.manifest["projection_to_node"].get(rp.relative_to(core).as_posix())
            if rp.is_relative_to(evid):
                return self.manifest["projection_to_node"].get("evidence/" + rp.relative_to(evid).as_posix())
        except Exception:
            pass
        return None

    def _local_content(self, node: dict) -> str:
        base = self.root / "rag/qmd/core" if node["collection"] == "company-core" else self.root / "rag/qmd/evidence"
        p = base / node["projection"]
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8", errors="replace")
        # Remove YAML frontmatter from generation context. Search-time metadata is
        # already indexed by QMD; the final answer needs the readable evidence.
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end >= 0:
                text = text[end + 5:]
        return text.strip()

    def _to_hit(self, node_id: str, score: float, reason: str) -> RetrievalHit:
        n = self.manifest["nodes"][node_id]
        return RetrievalHit(
            node_id=node_id,
            score=float(score),
            reason=reason,
            collection=n["collection"],
            projection=n["projection"],
            page_title=str(n.get("page_title") or ""),
            section_title=str(n.get("title") or " > ".join(n.get("breadcrumb") or []) or ""),
            source_path=str(n.get("page_relpath") or n.get("logical_raw_path") or n.get("raw_path") or ""),
            content=self._local_content(n),
        )

    @staticmethod
    def _clip01(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _search_terms(text: str) -> set[str]:
        """Small dependency-free CJK/Latin token set for deterministic fallback ranking.

        This is not a replacement for QMD. It is only used when a target page/source
        falls outside QMD's returned candidate window during restricted expansion.
        """
        s = text.lower()
        terms = set(re.findall(r"[a-z0-9][a-z0-9_.-]{1,}", s))
        for seq in re.findall(r"[\u4e00-\u9fff]{2,}", s):
            terms.add(seq)
            if len(seq) <= 8:
                for n in (2, 3):
                    if len(seq) >= n:
                        terms.update(seq[i:i+n] for i in range(len(seq) - n + 1))
        return terms

    def _local_relevance(self, query: str, node_id: str) -> float:
        n = self.manifest["nodes"][node_id]
        breadcrumb = " > ".join(n.get("breadcrumb") or [])
        text = "\n".join([
            str(n.get("page_title") or ""),
            str(n.get("title") or ""),
            breadcrumb,
            str(n.get("content") or "")[:5000],
        ])
        qterms = self._search_terms(query)
        if not qterms:
            return 0.0
        tterms = self._search_terms(text)
        overlap = len(qterms & tterms) / max(1, len(qterms))
        title = (str(n.get("page_title") or "") + " " + str(n.get("title") or "")).lower()
        q = query.lower().strip()
        title_bonus = 0.18 if q and (q in title or any(x in title for x in qterms if len(x) >= 2)) else 0.0
        return self._clip01(overlap * 0.82 + title_bonus)

    @staticmethod
    def _boost_searches(searches: list[dict], labels: list[str], max_labels: int) -> list[dict]:
        """Keep the original semantic query and add one lexically scoped search.

        QMD currently exposes collection scoping but not a stable path-prefix search
        parameter. Adding target titles/source names gives the lexical channel a strong
        hint, after which results are filtered to the allowed node set.
        """
        clean = []
        for x in labels:
            x = str(x).strip()
            if x and x not in clean:
                clean.append(x)
            if len(clean) >= max_labels:
                break
        out = [dict(x) for x in searches]
        if clean:
            base_lex = next((x.get("query", "") for x in searches if x.get("type") == "lex"), "")
            out.append({"type": "lex", "query": (base_lex + " " + " ".join(clean)).strip()})
        return out

    def _restricted_qmd_scores(
        self,
        *,
        searches: list[dict],
        collection: str,
        allowed_node_ids: set[str],
        labels: list[str],
        rerank: bool,
        intent: str,
    ) -> dict[str, float]:
        if not allowed_node_ids:
            return {}
        scfg = self.config["structure"]
        boosted = self._boost_searches(searches, labels, int(scfg.get("max_restricted_query_labels", 8)))
        try:
            qhits = self.qmd.query(
                searches=boosted,
                collections=[collection],
                limit=int(scfg.get("restricted_limit", 36)),
                candidate_limit=int(scfg.get("restricted_candidate_limit", 72)),
                min_score=float(scfg.get("restricted_min_score", 0.05)),
                rerank=rerank,
                intent=intent,
            )
        except Exception:
            # Primary retrieval already succeeded. Structural augmentation should fail
            # soft rather than taking down the whole Agent query.
            return {}
        scores: dict[str, float] = {}
        for qh in qhits:
            nid = self._node_from_qmd(qh)
            if nid and nid in allowed_node_ids:
                scores[nid] = max(scores.get(nid, float("-inf")), float(qh.score))
        return scores

    def _best_node(self, query: str, node_ids: list[str], qmd_scores: dict[str, float]) -> tuple[str | None, float]:
        if not node_ids:
            return None, 0.0
        ranked = [(nid, qmd_scores[nid]) for nid in node_ids if nid in qmd_scores]
        if ranked:
            nid, score = max(ranked, key=lambda x: x[1])
            return nid, self._clip01(score)
        # Deterministic fallback keeps Graph/source expansion useful even if QMD's
        # global candidate window did not include the restricted target.
        nid = max(node_ids, key=lambda x: self._local_relevance(query, x))
        return nid, self._local_relevance(query, nid)

    def _augment(self, hits: list[RetrievalHit], mode: str, query: str, searches: list[dict]) -> list[RetrievalHit]:
        if not self.config["retrieval"]["modes"][mode].get("use_graph"):
            return hits
        scfg = self.config["structure"]
        by_id = {h.node_id: h for h in hits}
        additions: list[RetrievalHit] = []

        def add(node_id: str | None, score: float, reason: str) -> bool:
            if not node_id or node_id in by_id or node_id not in self.manifest["nodes"]:
                return False
            h = self._to_hit(node_id, score, reason)
            by_id[node_id] = h
            additions.append(h)
            return True

        # 1) Hierarchical parent-child injection after QMD has ranked the hit.
        for hit in list(hits):
            n = self.manifest["nodes"].get(hit.node_id, {})
            if n.get("collection") != "company-core":
                continue
            if hit.score >= float(scfg["parent_score_threshold"]):
                add(n.get("parent_id"), hit.score * float(scfg["parent_score_discount"]), "parent-context")
            if hit.score >= float(scfg["child_score_threshold"]):
                for cid in (n.get("children_ids") or [])[: int(scfg["max_children_per_hit"])]:
                    add(cid, hit.score * float(scfg["child_score_discount"]), "child-context")

        # 2) Graph neighbor -> within-page retrieval -> best section.
        #    We no longer inject the neighbor page's root node blindly.
        represented_pages = {
            self.manifest["nodes"].get(h.node_id, {}).get("page_relpath")
            for h in hits
            if h.collection == "company-core"
        }
        page_seed_scores: dict[str, float] = {}
        for hit in hits:
            n = self.manifest["nodes"].get(hit.node_id, {})
            page = n.get("page_relpath")
            if n.get("collection") == "company-core" and page and hit.score >= float(scfg["graph_score_threshold"]):
                page_seed_scores[page] = max(page_seed_scores.get(page, 0.0), hit.score)

        target_seed_scores: dict[str, float] = {}
        target_budget = int(scfg["max_graph_neighbors"])
        for page, seed_score in sorted(page_seed_scores.items(), key=lambda x: x[1], reverse=True)[:3]:
            neighbors = self.graph.get("edges", {}).get(page, []) + self.graph.get("incoming", {}).get(page, [])
            for target in neighbors:
                if target in represented_pages or target not in self.manifest.get("page_nodes", {}):
                    continue
                target_seed_scores[target] = max(target_seed_scores.get(target, 0.0), seed_score)
                if len(target_seed_scores) >= target_budget:
                    break
            if len(target_seed_scores) >= target_budget:
                break

        if target_seed_scores:
            allowed_ids = {
                nid
                for page in target_seed_scores
                for nid in (self.manifest.get("page_nodes", {}).get(page) or [])
            }
            labels = []
            for page in target_seed_scores:
                root_id = self.manifest.get("page_roots", {}).get(page)
                if root_id:
                    root = self.manifest["nodes"].get(root_id, {})
                    labels.extend([root.get("page_title", ""), root.get("title", "")])
            restricted_scores = self._restricted_qmd_scores(
                searches=searches,
                collection="company-core",
                allowed_node_ids=allowed_ids,
                labels=labels,
                rerank=bool(scfg.get("graph_restricted_rerank", True)),
                intent="在已确认相关的 Wiki 图邻居页面中，选择最能回答当前问题的章节",
            )
            for page, seed_score in sorted(target_seed_scores.items(), key=lambda x: x[1], reverse=True):
                nid, relevance = self._best_node(query, self.manifest["page_nodes"].get(page, []), restricted_scores)
                if nid:
                    score = seed_score * float(scfg["graph_score_discount"]) * (0.85 + 0.15 * relevance)
                    add(nid, score, "wiki-graph-section")

        # 3) Source evidence support -> restricted retrieval inside raw sources.
        #    Direct company-evidence QMD hits are reused; only missing linked sources
        #    require the auxiliary restricted pass.
        existing_raws = {
            self.manifest["nodes"].get(h.node_id, {}).get("raw_path")
            for h in hits
            if h.collection == "company-evidence"
        }
        raw_seed_scores: dict[str, float] = {}
        raw_labels: dict[str, str] = {}
        for hit in hits:
            n = self.manifest["nodes"].get(hit.node_id, {})
            if n.get("collection") != "company-core" or hit.score < float(scfg["graph_score_threshold"]):
                continue
            for logical_raw in n.get("raw_sources") or []:
                actual_raw = self.manifest.get("raw_alias_to_actual", {}).get(logical_raw, logical_raw)
                if actual_raw in existing_raws or actual_raw not in self.manifest.get("raw_chunks", {}):
                    continue
                raw_seed_scores[actual_raw] = max(raw_seed_scores.get(actual_raw, 0.0), hit.score)
                raw_labels[actual_raw] = Path(logical_raw).stem

        support_budget = int(scfg["max_source_support"])
        candidate_raws = dict(sorted(raw_seed_scores.items(), key=lambda x: x[1], reverse=True)[:support_budget])
        if candidate_raws:
            allowed_ids = {
                nid
                for raw in candidate_raws
                for nid in (self.manifest.get("raw_chunks", {}).get(raw) or [])
            }
            restricted_scores = self._restricted_qmd_scores(
                searches=searches,
                collection="company-evidence",
                allowed_node_ids=allowed_ids,
                labels=[raw_labels.get(raw, Path(raw).stem) for raw in candidate_raws],
                rerank=bool(scfg.get("source_restricted_rerank", True)),
                intent="只在与高置信 Wiki 命中关联的原始资料中，选择最能支持当前问题的证据片段",
            )
            per_raw = int(scfg.get("source_support_per_raw", 1))
            remaining = support_budget
            for raw, seed_score in candidate_raws.items():
                if remaining <= 0:
                    break
                ids = self.manifest["raw_chunks"].get(raw) or []
                # Normally one best chunk per linked source is enough here; adjacent
                # chunks are handled later by the adaptive Context Builder.
                ranked_ids = sorted(
                    ids,
                    key=lambda nid: restricted_scores.get(nid, self._local_relevance(query, nid)),
                    reverse=True,
                )[:per_raw]
                for nid in ranked_ids:
                    relevance = self._clip01(restricted_scores.get(nid, self._local_relevance(query, nid)))
                    score = seed_score * float(scfg["evidence_score_discount"]) * (0.85 + 0.15 * relevance)
                    if add(nid, score, "source-evidence-restricted"):
                        remaining -= 1
                    if remaining <= 0:
                        break

        # 4) 多实体覆盖：对照/比较类问句中，确保每个被提及实体的页面
        #    至少有一个候选；对在重排后缺席的实体页，做受限检索挑最佳章节注入。
        mentions = entity_mentions(query, self.entity_names)
        if len(mentions) >= 2:
            represented = {self.manifest["nodes"].get(h.node_id, {}).get("page_relpath")
                           for h in hits + additions}
            seed = max((h.score for h in hits), default=0.0)
            for name in mentions[:3]:
                page = self.entity_pages.get(name)
                if not page or page in represented or seed <= 0:
                    continue
                node_ids = self.manifest.get("page_nodes", {}).get(page) or []
                if not node_ids:
                    continue
                restricted = self._restricted_qmd_scores(
                    searches=searches,
                    collection="company-core",
                    allowed_node_ids=set(node_ids),
                    labels=[name],
                    rerank=bool(scfg.get("entity_restricted_rerank", False)),
                    intent="在多实体问句涉及的目标实体页面内，选取与该问题最相关的章节",
                )
                nid, relevance = self._best_node(query, node_ids, restricted)
                if nid:
                    score = seed * float(scfg.get("entity_score_discount", 0.95)) * (0.85 + 0.15 * relevance)
                    add(nid, score, "entity-context")

        additions = sorted(additions, key=lambda h: h.score, reverse=True)[: int(scfg["max_augmented_hits"])]
        return sorted(hits + additions, key=lambda h: h.score, reverse=True)

    def _add_evidence_neighbors(self, hits: list[RetrievalHit], context_policy: str) -> list[RetrievalHit]:
        pcfg = self.config["context"]["policies"][context_policy]
        radius = int(pcfg.get("evidence_neighbor_radius", 0))
        seed_limit = int(pcfg.get("evidence_neighbor_seed_hits", 0))
        max_additions = int(pcfg.get("max_evidence_neighbor_hits", 0))
        if radius <= 0 or seed_limit <= 0 or max_additions <= 0:
            return hits

        discount = float(self.config["structure"].get("evidence_neighbor_score_discount", 0.76))
        by_id = {h.node_id: h for h in hits}
        additions: list[RetrievalHit] = []
        seeds = [
            h for h in hits
            if h.collection == "company-evidence" and h.reason in {"qmd", "source-evidence-restricted"}
        ][:seed_limit]
        for hit in seeds:
            n = self.manifest["nodes"].get(hit.node_id, {})
            raw = n.get("raw_path")
            ids = self.manifest.get("raw_chunks", {}).get(raw) or []
            if not ids:
                continue
            try:
                pos = ids.index(hit.node_id)
            except ValueError:
                continue
            for distance in range(1, radius + 1):
                for p in (pos - distance, pos + distance):
                    if p < 0 or p >= len(ids):
                        continue
                    nid = ids[p]
                    if nid in by_id:
                        continue
                    score = hit.score * (discount ** distance)
                    h = self._to_hit(nid, score, "evidence-neighbor")
                    by_id[nid] = h
                    additions.append(h)
                    if len(additions) >= max_additions:
                        return sorted(hits + additions, key=lambda x: x.score, reverse=True)
        return sorted(hits + additions, key=lambda x: x.score, reverse=True)

    def _compose_context(self, hits: list[RetrievalHit], context_policy: str) -> tuple[str, list[RetrievalHit], dict]:
        ccfg = self.config["context"]
        pcfg = ccfg["policies"][context_policy]
        hits = self._add_evidence_neighbors(hits, context_policy)

        max_chars = int(pcfg.get("max_chars", ccfg["max_chars"]))
        max_hits = int(pcfg.get("max_hits", ccfg["max_hits"]))
        per_wiki = int(pcfg["max_hits_per_wiki_page"])
        per_raw = int(pcfg["max_hits_per_raw_source"])
        reserve_core = int(pcfg["reserve_core_hits"])
        reserve_evidence = int(pcfg["reserve_evidence_hits"])
        wiki_penalty = float(pcfg.get("wiki_repeat_penalty", 1.0))
        raw_penalty = float(pcfg.get("raw_repeat_penalty", 1.0))

        selected: list[RetrievalHit] = []
        selected_ids: set[str] = set()
        wiki_counts: dict[str, int] = {}
        raw_counts: dict[str, int] = {}

        def key_for(h: RetrievalHit) -> tuple[str, str]:
            n = self.manifest["nodes"][h.node_id]
            if h.collection == "company-core":
                return "wiki", str(n.get("page_relpath") or h.source_path)
            return "raw", str(n.get("raw_path") or h.source_path)

        def count_for(h: RetrievalHit) -> int:
            kind, key = key_for(h)
            return wiki_counts.get(key, 0) if kind == "wiki" else raw_counts.get(key, 0)

        def cap_for(h: RetrievalHit) -> int:
            return per_wiki if h.collection == "company-core" else per_raw

        def can_add(h: RetrievalHit) -> bool:
            return h.node_id not in selected_ids and count_for(h) < cap_for(h)

        def utility(h: RetrievalHit) -> float:
            count = count_for(h)
            penalty = wiki_penalty if h.collection == "company-core" else raw_penalty
            return float(h.score) * (penalty ** count)

        def mark(h: RetrievalHit):
            selected.append(h)
            selected_ids.add(h.node_id)
            kind, key = key_for(h)
            if kind == "wiki":
                wiki_counts[key] = wiki_counts.get(key, 0) + 1
            else:
                raw_counts[key] = raw_counts.get(key, 0) + 1

        def fill_reserve(collection: str, reserve: int):
            while len([x for x in selected if x.collection == collection]) < reserve and len(selected) < max_hits:
                candidates = [h for h in hits if h.collection == collection and can_add(h)]
                if not candidates:
                    break
                mark(max(candidates, key=utility))

        # Dynamic reserve: source-focused queries intentionally leave most budget to
        # evidence; diversity queries reserve both knowledge and raw evidence.
        fill_reserve("company-core", reserve_core)
        fill_reserve("company-evidence", reserve_evidence)

        # Greedy relevance + novelty selection. Repeated sources are a soft penalty;
        # hard caps are policy-specific rather than globally fixed at 2.
        while len(selected) < max_hits:
            candidates = [h for h in hits if can_add(h)]
            if not candidates:
                break
            mark(max(candidates, key=utility))

        # Present strongest evidence first; the selection decision has already enforced
        # diversity/coverage. Citation IDs are local to this query.
        def make_block(h: RetrievalHit, citation: int) -> str:
            return (
                f"[S{citation}] score={h.score:.3f} reason={h.reason} type={h.collection}\n"
                f"source={h.source_path}\n"
                f"title={h.page_title} > {h.section_title}\n\n{h.content}\n"
            )

        budget_policy = str(self.config.get("context", {}).get("budget_policy", "score"))
        if budget_policy == "density":
            # Density ordering: order by score / block length (higher value per character). Selection
            # itself is unchanged; "score" (default) keeps the original behavior.
            selected = sorted(selected, key=lambda h: h.score / max(1, len(make_block(h, 0))), reverse=True)
        else:
            selected = sorted(selected, key=lambda h: h.score, reverse=True)

        blocks: list[str] = []
        final: list[RetrievalHit] = []
        used = 0
        for h in selected:
            citation = len(final) + 1
            block = make_block(h, citation)
            remaining = max_chars - used
            if remaining <= 0:
                break
            if len(block) > remaining:
                # Do not add a tiny unusable tail. For the first/only source we still
                # return a useful truncated block rather than an empty context.
                if remaining < 700 and final:
                    continue
                block = block[:remaining] + "\n[截断]"
            blocks.append(block)
            final.append(h)
            used += len(block)
            if used >= max_chars:
                break

        raw_sources = {
            self.manifest["nodes"][h.node_id].get("raw_path")
            for h in final if h.collection == "company-evidence"
        }
        wiki_pages = {
            self.manifest["nodes"][h.node_id].get("page_relpath")
            for h in final if h.collection == "company-core"
        }
        dropped_hits = len(selected) - len(final)
        # The budget-edge cut appends the truncation marker (+5 chars) beyond the
        # remaining budget, so used > max_chars iff the edge block was cut.
        # truncated = any content lost to the char budget (edge cut or dropped blocks).
        edge_cut = used > max_chars
        stats = {
            "policy": context_policy,
            "budget_policy": budget_policy,
            "selected_hits": len(final),
            "core_hits": sum(1 for h in final if h.collection == "company-core"),
            "evidence_hits": sum(1 for h in final if h.collection == "company-evidence"),
            "distinct_wiki_pages": len({x for x in wiki_pages if x}),
            "distinct_raw_sources": len({x for x in raw_sources if x}),
            "chars": used,
            "max_chars": max_chars,
            "max_hits_per_raw_source": per_raw,
            "truncated": bool(edge_cut or dropped_hits),
            "dropped_hits": dropped_hits,
        }
        return "\n\n---\n\n".join(blocks), final, stats

    def _api_rerank(
        self,
        query: str,
        hits: list[RetrievalHit],
        *,
        keep: int,
        searches: list[dict],
        collections: list[str],
        mcfg: dict,
    ) -> tuple[list[RetrievalHit], str]:
        """One-call cloud rerank over the fused candidate pool (config `rerank_api`).

        qmd only fuses candidates (rerank=False, fast); a single API call scores them all.
        On failure the fallback follows `rerank_api.fallback`: `local` re-runs the QMD query
        with the local reranker; `rrf` keeps the fusion order. Fail-soft: retrieval already
        succeeded once and must never crash the ask.
        """
        api = self.rerank_api
        docs = [
            (h.content or self._local_content(self.manifest["nodes"][h.node_id]))[: api.max_doc_chars]
            for h in hits
        ]
        ranked: list[dict] = []
        error: str | None = None
        try:
            if hits:
                ranked = api.rerank(query, docs, top_n=max(keep, 1))
            if not ranked and hits:
                error = "empty api rerank results"
        except RerankApiError as exc:
            error = str(exc)
        if error is None:
            out: list[RetrievalHit] = []
            for item in ranked:
                if len(out) >= keep:
                    break
                i = item.get("index")
                if isinstance(i, int) and 0 <= i < len(hits):
                    h = hits[i]
                    h.score = float(item.get("relevance_score") or 0.0)
                    out.append(h)
            return out, "api"
        self._last_rerank_error = error
        if api.fallback == "local":
            try:
                qhits = self.qmd.query(
                    searches=searches,
                    collections=collections,
                    limit=keep,
                    candidate_limit=int(mcfg["candidate_limit"]),
                    min_score=float(mcfg["min_score"]),
                    rerank=True,
                    intent="企业内部知识检索；优先可追溯事实、相关实体/概念和原始证据",
                )
            except Exception:
                return hits[:keep], "rrf-fallback"
            out, seen = [], set()
            for qh in qhits:
                nid = self._node_from_qmd(qh)
                if not nid or nid in seen:
                    continue
                seen.add(nid)
                out.append(self._to_hit(nid, qh.score, "qmd"))
            return out, "local-fallback"
        return hits[:keep], "rrf-fallback"

    def _fetch_hits(self, query: str, mode: str, hyde: str | None):
        """单次"取候选 + 重排"：返回 (hits, rerank_mode, searches, collections, mcfg)。

        云端重排路径：QMD 只负责融合取候选（快），重排交给一次 API 批量打分；
        失败按 rerank_api.fallback 回退（见 _api_rerank）。
        """
        mcfg = self.config["retrieval"]["modes"][mode]
        collections = list(mcfg["collections"])
        lex_leg = self.config.get("retrieval", {}).get("lex_leg", "on")
        if isinstance(lex_leg, bool):  # PyYAML 1.1 parses bare on/off as booleans
            lex_leg = "on" if lex_leg else "off"
        searches = build_typed_searches(query, bool(mcfg.get("use_vector")), self.lexicon.get("terms", []),
                                        hyde=hyde, entity_names=self.entity_names,
                                        lex_leg=str(lex_leg))
        keep = int(mcfg["limit"])
        use_api_rerank = bool(mcfg.get("rerank")) and self.rerank_api.available()
        rerank_mode = "local" if bool(mcfg.get("rerank")) else "none"
        if use_api_rerank:
            qhits = self.qmd.query(
                searches=searches,
                collections=collections,
                limit=int(mcfg["candidate_limit"]),
                candidate_limit=int(mcfg["candidate_limit"]),
                min_score=float(self.rerank_api.fetch_min_score),
                rerank=False,
                intent="企业内部知识检索；优先可追溯事实、相关实体/概念和原始证据",
            )
        else:
            qhits = self.qmd.query(
                searches=searches,
                collections=collections,
                limit=keep,
                candidate_limit=int(mcfg["candidate_limit"]),
                min_score=float(mcfg["min_score"]),
                rerank=bool(mcfg["rerank"]),
                intent="企业内部知识检索；优先可追溯事实、相关实体/概念和原始证据",
            )
        hits: list[RetrievalHit] = []
        seen = set()
        for qh in qhits:
            nid = self._node_from_qmd(qh)
            if not nid or nid in seen:
                continue
            seen.add(nid)
            hits.append(self._to_hit(nid, qh.score, "qmd"))
        if use_api_rerank:
            hits, rerank_mode = self._api_rerank(
                query, hits, keep=keep, searches=searches, collections=collections, mcfg=mcfg
            )
        return hits, rerank_mode, searches, collections, mcfg

    def search(
        self,
        query: str,
        mode: str = "auto",
        profile: str | None = None,
        hyde: str | None = None,
        context_policy: str = "auto",
    ) -> dict:
        started = time.perf_counter()
        self._last_rerank_error = None
        mode = choose_mode(query, mode)
        context_policy = choose_context_policy(query, context_policy)
        mcfg = self.config["retrieval"]["modes"][mode]

        # All employees have full access in this deployment. `profile` is retained only
        # as caller identity for traces/Hermes integration; it never filters retrieval.
        hits, rerank_mode, searches, collections, mcfg = self._fetch_hits(query, mode, hyde)
        escalated_from = None
        if mode == "fast" and not hits:
            # 快速路径零命中（词表未覆盖的间接问句——lex 仅词表词）：自动升级到 balanced。
            # Regression cases: fast can return empty for indirect natural-language queries.
            escalated_from = "fast"
            mode = "balanced"
            hits, rerank_mode, searches, collections, mcfg = self._fetch_hits(query, mode, hyde)

        hits = self._augment(hits, mode, query, searches)
        context, selected, context_stats = self._compose_context(hits, context_policy)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        result = {
            "query": query,
            "mode": mode,
            "entity_mentions": entity_mentions(query, self.entity_names),
            "context_policy": context_policy,
            "profile": profile,
            "access": "all-employees-full-access",
            "searches": searches,
            "collections": collections,
            "rerank_mode": rerank_mode,
            "rerank_error": self._last_rerank_error,
            "escalated_from": escalated_from,
            "elapsed_ms": elapsed_ms,
            "context_stats": context_stats,
            "retrieved": [h.to_dict(include_content=False) for h in hits],
            "selected": [h.to_dict(include_content=False) | {"citation": f"S{i+1}"} for i, h in enumerate(selected)],
            "context": context,
            "answer_policy": "仅依据提供的 [S#] 证据回答；关键事实引用 [S#]；证据不足时明确说明知识库没有足够证据。",
        }
        self._trace(result)
        return result

    def _trace(self, result: dict):
        tcfg = self.config.get("trace", {})
        if not tcfg.get("enabled", True):
            return
        trace_dir = self.root / self.config["project"].get("traces_dir", "rag/traces")
        trace_dir.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y%m%d")
        payload = dict(result)
        payload.pop("context", None)
        if not tcfg.get("include_query_text", True):
            payload["query"] = "[redacted]"
        with (trace_dir / f"{day}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
