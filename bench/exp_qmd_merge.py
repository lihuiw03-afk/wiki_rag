# -*- coding: utf-8 -*-
"""Bench-only QMD-call reduction experiment: merge graph + source expansion passes.

Both structural expansions stay enabled. The only change is that the two restricted
QMD calls (company-core graph pass + company-evidence source pass) are issued as one
query with both collections and summed candidate budgets.

No qwen3.8 calls: retrieval metrics only.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

import rag_gateway.search as search_mod  # noqa: E402
from rag_gateway.search import CompanyRAG, RetrievalHit  # noqa: E402
from run_experiments import metrics_from  # noqa: E402


class MergedRAG(CompanyRAG):
    """Graph + source restricted passes batched into one QMD call."""

    source_relevance_mode = "qmd"  # qmd | fixed | localmax

    def _restricted_batch(self, *, searches, requests, rerank, intent):
        allowed = set()
        collections = []
        labels = []
        for req in requests:
            allowed |= req["allowed"]
            if req["collection"] not in collections:
                collections.append(req["collection"])
            labels.extend(req["labels"])
        if not allowed:
            return {}
        scfg = self.config["structure"]
        boosted = self._boost_searches(searches, labels, int(scfg.get("max_restricted_query_labels", 8)))
        # Preserve each pass's candidate budget by summing them.
        limit = int(scfg.get("restricted_limit", 36)) * len(requests)
        candidate = int(scfg.get("restricted_candidate_limit", 72)) * len(requests)
        try:
            qhits = self.qmd.query(
                searches=boosted, collections=collections,
                limit=limit, candidate_limit=candidate,
                min_score=float(scfg.get("restricted_min_score", 0.05)),
                rerank=rerank, intent=intent,
            )
        except Exception:
            return {}
        scores = {}
        for qh in qhits:
            nid = self._node_from_qmd(qh)
            if nid and nid in allowed:
                scores[nid] = max(scores.get(nid, float("-inf")), float(qh.score))
        return scores

    def _augment(self, hits: list[RetrievalHit], mode: str, query: str, searches: list[dict]):
        if not self.config["retrieval"]["modes"][mode].get("use_graph"):
            return hits
        scfg = self.config["structure"]
        by_id = {h.node_id: h for h in hits}
        additions: list[RetrievalHit] = []

        def add(node_id, score, reason):
            if not node_id or node_id in by_id or node_id not in self.manifest["nodes"]:
                return False
            h = self._to_hit(node_id, score, reason)
            by_id[node_id] = h
            additions.append(h)
            return True

        # 1) hierarchical parent/child (no QMD)
        for hit in list(hits):
            n = self.manifest["nodes"].get(hit.node_id, {})
            if n.get("collection") != "company-core":
                continue
            if hit.score >= float(scfg["parent_score_threshold"]):
                add(n.get("parent_id"), hit.score * float(scfg["parent_score_discount"]), "parent-context")
            if hit.score >= float(scfg["child_score_threshold"]):
                for cid in (n.get("children_ids") or [])[: int(scfg["max_children_per_hit"])]:
                    add(cid, hit.score * float(scfg["child_score_discount"]), "child-context")

        # 2) graph neighbor candidates
        represented_pages = {
            self.manifest["nodes"].get(h.node_id, {}).get("page_relpath")
            for h in hits if h.collection == "company-core"
        }
        page_seed_scores = {}
        for hit in hits:
            n = self.manifest["nodes"].get(hit.node_id, {})
            page = n.get("page_relpath")
            if n.get("collection") == "company-core" and page and hit.score >= float(scfg["graph_score_threshold"]):
                page_seed_scores[page] = max(page_seed_scores.get(page, 0.0), hit.score)
        target_seed_scores = {}
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

        graph_req = None
        if target_seed_scores:
            allowed_ids = {nid for page in target_seed_scores
                           for nid in (self.manifest.get("page_nodes", {}).get(page) or [])}
            labels = []
            for page in target_seed_scores:
                root_id = self.manifest.get("page_roots", {}).get(page)
                if root_id:
                    root = self.manifest["nodes"].get(root_id, {})
                    labels.extend([root.get("page_title", ""), root.get("title", "")])
            graph_req = {"collection": "company-core", "allowed": allowed_ids, "labels": labels, "kind": "graph"}

        # 3) source support candidates
        existing_raws = {
            self.manifest["nodes"].get(h.node_id, {}).get("raw_path")
            for h in hits if h.collection == "company-evidence"
        }
        raw_seed_scores = {}
        raw_labels = {}
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
        source_req = None
        if candidate_raws:
            allowed_ids = {nid for raw in candidate_raws
                           for nid in (self.manifest.get("raw_chunks", {}).get(raw) or [])}
            source_req = {"collection": "company-evidence", "allowed": allowed_ids,
                          "labels": [raw_labels.get(raw, Path(raw).stem) for raw in candidate_raws], "kind": "source"}

        # ONE restricted QMD call for both graph and source passes
        reqs = [r for r in (graph_req, source_req) if r]
        batch_scores = self._restricted_batch(
            searches=searches, requests=reqs, rerank=False,
            intent="合并：图邻居章节 + 关联原始资料证据",
        ) if reqs else {}

        if graph_req:
            for page, seed_score in sorted(target_seed_scores.items(), key=lambda x: x[1], reverse=True):
                nid, relevance = self._best_node(query, self.manifest["page_nodes"].get(page, []), batch_scores)
                if nid:
                    score = seed_score * float(scfg["graph_score_discount"]) * (0.85 + 0.15 * relevance)
                    add(nid, score, "wiki-graph-section")

        if source_req:
            per_raw = int(scfg.get("source_support_per_raw", 1))
            remaining = support_budget
            for raw, seed_score in candidate_raws.items():
                if remaining <= 0:
                    break
                ids = self.manifest["raw_chunks"].get(raw) or []
                ranked_ids = sorted(ids, key=lambda nid: batch_scores.get(nid, self._local_relevance(query, nid)),
                                    reverse=True)[:per_raw]
                for nid in ranked_ids:
                    qmd_rel = self._clip01(batch_scores.get(nid, self._local_relevance(query, nid)))
                    if self.source_relevance_mode == "fixed":
                        relevance = 1.0
                    elif self.source_relevance_mode == "localmax":
                        relevance = max(qmd_rel, self._local_relevance(query, nid))
                    else:
                        relevance = qmd_rel
                    score = seed_score * float(scfg["evidence_score_discount"]) * (0.85 + 0.15 * relevance)
                    if add(nid, score, "source-evidence-restricted"):
                        remaining -= 1
                    if remaining <= 0:
                        break

        # 4) multi-entity coverage (unchanged; rare extra restricted calls)
        mentions = search_mod.entity_mentions(query, self.entity_names)
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
                    searches=searches, collection="company-core", allowed_node_ids=set(node_ids),
                    labels=[name], rerank=False,
                    intent="多实体问句实体页章节",
                )
                nid, relevance = self._best_node(query, node_ids, restricted)
                if nid:
                    add(nid, seed * float(scfg.get("entity_score_discount", 0.95)) * (0.85 + 0.15 * relevance),
                        "entity-context")

        additions = sorted(additions, key=lambda h: h.score, reverse=True)[: int(scfg["max_augmented_hits"])]
        return sorted(hits + additions, key=lambda h: h.score, reverse=True)


VARIANTS = ["baseline", "merged"]


class MergedFixed(MergedRAG):
    source_relevance_mode = "fixed"


class MergedLocalMax(MergedRAG):
    source_relevance_mode = "localmax"


def make_rag(variant):
    if variant == "merged":
        return MergedRAG(ROOT)
    if variant == "merged_fixed":
        return MergedFixed(ROOT)
    if variant == "merged_localmax":
        return MergedLocalMax(ROOT)
    return CompanyRAG(ROOT)


def run(set_name, path, variants):
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    for variant in variants:
        rag = make_rag(variant)
        calls = []
        rr = []
        orig_qmd = rag.qmd.query
        orig_rr = rag.rerank_api.rerank

        def qmd_wrap(**kwargs):
            t = time.perf_counter()
            out = orig_qmd(**kwargs)
            calls.append((time.perf_counter() - t) * 1000)
            return out

        def rr_wrap(query, docs, top_n=None):
            t = time.perf_counter()
            out = orig_rr(query, docs, top_n)
            rr.append((time.perf_counter() - t) * 1000)
            return out

        rag.qmd.query = qmd_wrap
        rag.rerank_api.rerank = rr_wrap
        per = []
        for r in rows:
            for q in r["queries"]:
                calls.clear(); rr.clear()
                t = time.perf_counter()
                res = rag.search(q["text"], mode="auto", profile="wu01")
                total = (time.perf_counter() - t) * 1000
                m = metrics_from(res, set(r["target_pages"]), set(r["target_raws"]), rag.manifest["nodes"])
                m.update({"id": r["id"], "style": q["style"], "mode": res.get("mode"),
                          "total_ms": total, "qmd_calls": len(calls), "qmd_ms": sum(calls),
                          "rr_calls": len(rr), "rr_ms": sum(rr)})
                per.append(m)
        keys = ["page_cov@5", "page_cov@10", "gold_cov@5", "gold_cov@10", "sel_ratio", "mrr",
                "chars", "blocks", "dropped", "total_ms", "qmd_calls", "qmd_ms", "rr_calls", "rr_ms"]
        summary = {k: round(statistics.mean([x.get(k, 0) for x in per]), 2) for k in keys}
        summary["n"] = len(per)
        out = ROOT / f"bench/results/exp_qmd_merge_{set_name}_{variant}.json"
        out.write_text(json.dumps({"summary": summary, "per_query": per}, ensure_ascii=False, indent=1),
                       encoding="utf-8", newline="\n")
        print(f"{set_name:3s} {variant:8s} qmd={summary['qmd_calls']:.2f} "
              f"qmd_ms={summary['qmd_ms']:.0f} @5={summary['page_cov@5']:.4f} "
              f"@10={summary['page_cov@10']:.4f} sel={summary['sel_ratio']:.4f} "
              f"mrr={summary['mrr']:.4f} chars={summary['chars']:.0f} "
              f"total={summary['total_ms']:.0f}ms", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["58", "h50"], required=True)
    ap.add_argument("--variants", default="baseline,merged")
    args = ap.parse_args()
    path = ROOT / ("bench/dataset/queries.jsonl" if args.set == "58" else "bench/dataset/queries_hard50.jsonl")
    run(args.set, path, [v for v in args.variants.split(",") if v])
