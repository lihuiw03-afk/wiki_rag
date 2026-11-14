# -*- coding: utf-8 -*-
"""bench/run_experiments.py — OFAT 实验执行器（E0 方差基线 / E6 预算分配）

纪律（见 docs/EVALUATION.md）：
- 不修改生产代码：E6 变体以子类覆盖 `_compose_context` 的 bench 侧原型实现；
- 离线固定测试集（bench/dataset/queries.jsonl）；每次只变一个变量；
- 记录全部指标（含 Selection Hit Rate）+ 延迟 + 逐条明细 → bench/results/

用法：
  python bench/run_experiments.py run e0 control --repeat 3          # E0 方差基线
  python bench/run_experiments.py run e6 A --repeat 2                # E6 变体 A
  python bench/run_experiments.py run e6 B --only C15,E06 --repeat 1 # 冒烟/定向
  python bench/run_experiments.py summary e6                         # 汇总对比
"""
from __future__ import annotations
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os
os.chdir(ROOT)

from rag_gateway.search import CompanyRAG, RetrievalHit  # noqa: E402


# ---------------------------------------------------------------- E6 变体实现
class PolicyRAG(CompanyRAG):
    """E6 预算分配原型：control=原逻辑；A=密度序；B=保护+回收；C=两阶段。"""

    budget_policy = "control"
    budget_params: dict = {}

    def _compose_context(self, hits, context_policy):
        if self.budget_policy == "control":
            return super()._compose_context(hits, context_policy)
        return self._compose_with_policy(hits, context_policy)

    # 复刻原实现的选择阶段（reserves+greedy），仅替换"装入（emission）"阶段
    def _compose_with_policy(self, hits, context_policy):
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

        def key_for(h):
            n = self.manifest["nodes"][h.node_id]
            if h.collection == "company-core":
                return "wiki", str(n.get("page_relpath") or h.source_path)
            return "raw", str(n.get("raw_path") or h.source_path)

        def count_for(h):
            kind, key = key_for(h)
            return wiki_counts.get(key, 0) if kind == "wiki" else raw_counts.get(key, 0)

        def cap_for(h):
            return per_wiki if h.collection == "company-core" else per_raw

        def can_add(h):
            return h.node_id not in selected_ids and count_for(h) < cap_for(h)

        def utility(h):
            count = count_for(h)
            penalty = wiki_penalty if h.collection == "company-core" else raw_penalty
            return float(h.score) * (penalty ** count)

        def mark(h):
            selected.append(h)
            selected_ids.add(h.node_id)
            kind, key = key_for(h)
            if kind == "wiki":
                wiki_counts[key] = wiki_counts.get(key, 0) + 1
            else:
                raw_counts[key] = raw_counts.get(key, 0) + 1

        def fill_reserve(collection, reserve):
            while len([x for x in selected if x.collection == collection]) < reserve and len(selected) < max_hits:
                candidates = [h for h in hits if h.collection == collection and can_add(h)]
                if not candidates:
                    break
                mark(max(candidates, key=utility))

        fill_reserve("company-core", reserve_core)
        fill_reserve("company-evidence", reserve_evidence)
        while len(selected) < max_hits:
            candidates = [h for h in hits if can_add(h)]
            if not candidates:
                break
            mark(max(candidates, key=utility))

        selected = sorted(selected, key=lambda h: h.score, reverse=True)

        def make_block(h, citation):
            return (
                f"[S{citation}] score={h.score:.3f} reason={h.reason} type={h.collection}\n"
                f"source={h.source_path}\n"
                f"title={h.page_title} > {h.section_title}\n\n{h.content}\n"
            )

        policy = self.budget_policy
        bp = self.budget_params
        blocks: list[str] = []
        final: list[RetrievalHit] = []
        used = 0

        def emit(h):
            nonlocal used
            citation = len(final) + 1
            block = make_block(h, citation)
            remaining = max_chars - used
            if remaining <= 0:
                return False
            if len(block) > remaining:
                if remaining < 700 and final:
                    return False
                block = block[:remaining] + "\n[截断]"
            blocks.append(block)
            final.append(h)
            used += len(block)
            return True

        def emit_capped(h, cap):
            nonlocal used
            citation = len(final) + 1
            block = make_block(h, citation)
            if len(block) > cap:
                block = block[:cap] + "\n[截断]"
            remaining = max_chars - used
            if remaining <= 0:
                return False
            if len(block) > remaining:
                if remaining < 700 and final:
                    return False
                block = block[:remaining] + "\n[截断]"
            blocks.append(block)
            final.append(h)
            used += len(block)
            return True

        if policy == "A":  # 密度序：按 score/length 装入
            order = sorted(selected, key=lambda h: h.score / max(1, len(make_block(h, 0))), reverse=True)
            for h in order:
                if used >= max_chars:
                    break
                emit(h)
        elif policy == "B":  # 高价值块保护 + 回收（截断腾位）
            thr = float(bp.get("protect_threshold", 0.85))
            frac = float(bp.get("protect_fraction", 0.85))
            prot = [h for h in selected if h.score >= thr]
            rest = [h for h in selected if h.score < thr]
            if prot:
                cap = max(500, int(max_chars * frac / len(prot)))
                for h in prot:
                    if used >= max_chars:
                        break
                    emit_capped(h, cap)
            for h in rest:
                if used >= max_chars:
                    break
                emit(h)
        elif policy == "C":  # 两阶段：topN 限长保底 → 余量填充
            n1 = int(bp.get("phase1_n", 5))
            frac1 = float(bp.get("phase1_fraction", 0.6))
            phase1 = selected[:n1]
            phase2 = selected[n1:]
            cap1 = max(500, int(max_chars * frac1 / max(1, len(phase1))))
            for h in phase1:
                if used >= max_chars:
                    break
                emit_capped(h, cap1)
            for h in phase2:
                if used >= max_chars:
                    break
                emit(h)
        else:
            raise ValueError(f"unknown policy {policy}")

        raw_sources = {self.manifest["nodes"][h.node_id].get("raw_path") for h in final if h.collection == "company-evidence"}
        wiki_pages = {self.manifest["nodes"][h.node_id].get("page_relpath") for h in final if h.collection == "company-core"}
        dropped_hits = len(selected) - len(final)
        edge_cut = used > max_chars
        stats = {
            "policy": context_policy,
            "budget_policy": policy,
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


class E4RAG(PolicyRAG):
    """E4-A 原型：受限扩展候选改用云端批量 rerank 打分（主搜索/主重排不动）。

    覆盖 `_restricted_qmd_scores`：先用 RRF（rerank=False）取候选，再对「白名单内候选」
    做一次云端 rerank（api.rerank），以 relevance 作为注入项打分；云端失败回退 RRF 分数
    （与生产 fail-soft 语义一致）。计数器记录调用次数/候选数，供成本核算。
    """

    def __init__(self, root):
        super().__init__(root)
        self.e4_calls = 0
        self.e4_docs = 0
        self.e4_errors = 0

    def _restricted_qmd_scores(self, *, searches, collection, allowed_node_ids, labels, rerank, intent):
        base = super()._restricted_qmd_scores(
            searches=searches, collection=collection, allowed_node_ids=allowed_node_ids,
            labels=labels, rerank=False, intent=intent)
        if not base:
            return base
        api = self.rerank_api
        if api is None or not api.available():
            return base
        qtext = ""
        for s in searches:
            if s.get("query"):
                qtext = s["query"]
                break
        nodes = self.manifest["nodes"]
        ids, docs = [], []
        for nid in base:
            node = nodes.get(nid) or {}
            c = node.get("content") or ""
            if not c:
                try:
                    c = self._local_content(node)
                except Exception:  # noqa: BLE001
                    c = ""
            if not c.strip():
                continue
            ids.append(nid)
            docs.append(c[: api.max_doc_chars])
        if not docs:
            return base
        try:
            ranked = api.rerank(qtext, docs, top_n=len(docs))
            self.e4_calls += 1
            self.e4_docs += len(docs)
        except Exception:  # noqa: BLE001
            self.e4_errors += 1
            return base
        out = {}
        for item in ranked or []:
            i = item.get("index")
            if isinstance(i, int) and 0 <= i < len(ids):
                out[ids[i]] = float(item.get("relevance_score") or 0.0)
        return out or base


# ---------------------------------------------------------------- 指标
def to_pages(items, nodes):
    pages = set()
    for it in items:
        n = nodes.get(it.get("node_id")) or {}
        if n.get("page_relpath"):
            pages.add(n["page_relpath"])
    return pages


def _raws_of(items, nodes):
    s = set()
    for it in items:
        n = nodes.get(it.get("node_id")) or {}
        if n.get("raw_path"):
            s.add(n["raw_path"])
    return s


def metrics_from(r, gold_pages, gold_raws, nodes):
    out = {}
    retr = sorted(r.get("retrieved") or [], key=lambda x: x.get("score") or 0, reverse=True)
    sel = r.get("selected") or []
    unit_pages = bool(gold_pages)
    for k in (5, 10):
        rp = to_pages(retr[:k], nodes)
        rr = _raws_of(retr[:k], nodes)
        out[f"page_cov@{k}"] = len(gold_pages & rp) / max(1, len(gold_pages))
        out[f"raw_hit@{k}"] = 1.0 if (gold_raws & rr) else 0.0
        out[f"gold_cov@{k}"] = (len(gold_pages & rp) / max(1, len(gold_pages))) if unit_pages \
            else (len(gold_raws & rr) / max(1, len(gold_raws)))
    sp = to_pages(sel, nodes)
    sr = _raws_of(sel, nodes)
    rp_all = to_pages(retr, nodes)
    rr_all = _raws_of(retr, nodes)
    out["page_in_selected"] = len(gold_pages & sp) / max(1, len(gold_pages))
    if unit_pages:
        denom = len(gold_pages & rp_all)
        num = len(gold_pages & sp)
    else:
        denom = len(gold_raws & rr_all)
        num = len(gold_raws & sr)
    out["pool_any"] = 1.0 if denom else 0.0
    out["sel_any"] = 1.0 if num else 0.0
    out["sel_ratio"] = (num / denom) if denom else 0.0
    if unit_pages:
        rank = next((i + 1 for i, it in enumerate(retr)
                     if ((nodes.get(it.get("node_id")) or {}).get("page_relpath") in gold_pages)), None)
    else:
        rank = next((i + 1 for i, it in enumerate(retr)
                     if ((nodes.get(it.get("node_id")) or {}).get("raw_path") in gold_raws)), None)
    out["mrr"] = (1.0 / rank) if rank else 0.0
    st = r.get("context_stats") or {}
    out["blocks"] = st.get("selected_hits", len(sel))
    out["chars"] = st.get("chars", 0)
    out["dropped"] = st.get("dropped_hits", 0)
    out["elapsed_ms"] = r.get("elapsed_ms") or 0
    return out


def _load_rows(file_arg):
    rows = []
    for f in (file_arg or "bench/dataset/queries.jsonl").split(","):
        p = ROOT / f.strip()
        rows += [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows


def run_once(exp, variant, repeat_idx, only=None, nodes=None, file=None):
    rows = _load_rows(file)
    if only:
        want = {s.strip() for s in only.split(",")}
        rows = [r for r in rows if r["id"] in want]
    # 追加式轮次：自动跳过已存在的 rN，避免覆盖历史结果
    i = repeat_idx
    while (ROOT / f"bench/results/exp_{exp}_{variant}_r{i}.json").exists():
        i += 1
    repeat_idx = i
    rag = PolicyRAG(ROOT)
    if exp in ("e6", "h50"):
        rag.budget_policy = variant
    elif exp in ("v2", "v2h", "v2c", "v2hc"):
        # v2/v2h=E6-A(density) 基座；v2c/v2hc=control 基座（无 E6-A=生产现状）——单变量叠加
        base_policy = "A" if exp in ("v2", "v2h") else "control"
        rag.budget_policy = base_policy
        if variant == "e4":
            rag = E4RAG(ROOT)
            rag.budget_policy = base_policy
        elif variant == "hyde":
            pass  # hyde 通过 search(kwargs) 注入
        elif variant.startswith("res"):
            v = int(variant[3:])
            for pol in rag.config["context"]["policies"].values():
                pol["reserve_evidence_hits"] = v
        elif variant.startswith("aug"):
            rag.config["structure"]["max_augmented_hits"] = int(variant[3:])
        else:
            raise ValueError(f"unknown v2 variant {variant}")
    elif exp == "e3":
        scale = {"x100": 1.0, "x115": 1.15, "x130": 1.3}.get(variant)
        if scale is None:
            raise ValueError(f"unknown e3 variant {variant}")
        for k in ("parent_score_discount", "child_score_discount",
                  "graph_score_discount", "evidence_score_discount"):
            rag.config["structure"][k] = round(float(rag.config["structure"][k]) * scale, 4)
    elif exp == "combo":
        pol, _, sc = variant.partition("_x")
        if pol not in ("A", "B", "C"):
            raise ValueError(f"unknown combo policy {variant}")
        scale = (int(sc) / 100.0) if sc else 1.0
        rag.budget_policy = pol
        if abs(scale - 1.0) > 1e-9:
            for k in ("parent_score_discount", "child_score_discount",
                      "graph_score_discount", "evidence_score_discount"):
                rag.config["structure"][k] = round(float(rag.config["structure"][k]) * scale, 4)
    hyde_cache = {}
    if exp in ("v2", "v2h", "v2c", "v2hc") and variant == "hyde":
        hp = ROOT / "bench/results/hyde_cache.json"
        if hp.exists():
            hyde_cache = json.loads(hp.read_text(encoding="utf-8"))
    recs = []
    t0 = time.time()
    for row in rows:
        gp = set(row["target_pages"])
        gr = set(row["target_raws"])
        for q in row["queries"]:
            rec = {"id": row["id"], "style": q["style"], "category": row["category"]}
            e4_before = getattr(rag, "e4_calls", 0)
            kwargs = {}
            if hyde_cache:
                h = hyde_cache.get(q["text"])
                if h:
                    kwargs["hyde"] = h
            try:
                r = rag.search(q["text"], mode="auto", profile="wu01", **kwargs)
                rec.update(metrics_from(r, gp, gr, rag.manifest["nodes"]))
            except Exception as e:
                rec["error"] = str(e)[:150]
                rec.update({"page_cov@5": 0, "page_cov@10": 0, "page_in_selected": 0, "mrr": 0, "elapsed_ms": 0})
            rec["e4_api"] = getattr(rag, "e4_calls", 0) - e4_before
            recs.append(rec)
    wall = time.time() - t0
    s = {"exp": exp, "variant": variant, "repeat": repeat_idx, "ts": time.strftime("%F %T"),
         "n": len(recs), "wall_s": round(wall, 1)}
    for k in ["page_cov@5", "page_cov@10", "gold_cov@5", "gold_cov@10", "raw_hit@10",
              "page_in_selected", "mrr", "blocks", "chars", "dropped"]:
        s[k] = statistics.mean([x.get(k, 0) for x in recs])
    lat = [x.get("elapsed_ms", 0) for x in recs if x.get("elapsed_ms")]
    s["latency_mean_ms"] = statistics.mean(lat) if lat else 0
    s["latency_p50_ms"] = statistics.median(lat) if lat else 0
    s["latency_p90_ms"] = sorted(lat)[min(len(lat) - 1, int(len(lat) * 0.9))] if lat else 0
    pool = [x for x in recs if x.get("pool_any")]
    s["SHR_any"] = (sum(1 for x in pool if x.get("sel_any")) / len(pool)) if pool else 0
    s["SHR_ratio"] = statistics.mean([min(1.0, x.get("sel_ratio", 0)) for x in pool]) if pool else 0
    err = sum(1 for x in recs if "error" in x)
    s["errors"] = err
    s["e4_api_total"] = sum(x.get("e4_api", 0) for x in recs)
    out = ROOT / f"bench/results/exp_{exp}_{variant}_r{repeat_idx}.json"
    out.write_text(json.dumps({"summary": s, "per_query": recs}, ensure_ascii=False, indent=1),
                   encoding="utf-8", newline="\n")
    print(f"[{exp}/{variant} r{repeat_idx}] n={s['n']} @5={s['page_cov@5']:.3f} @10={s['page_cov@10']:.3f} "
          f"sel={s['page_in_selected']:.3f} SHR={s['SHR_any']:.3f}/{s['SHR_ratio']:.3f} "
          f"blocks={s['blocks']:.1f} chars={s['chars']:.0f} p50={s['latency_p50_ms']:.0f}ms wall={s['wall_s']}s err={err}")
    return s


def summary(exp):
    files = sorted((ROOT / "bench/results").glob(f"exp_{exp}_*_r*.json"))
    ctrl = []
    if exp == "e6":
        ctrl = sorted((ROOT / "bench/results").glob("exp_e0_control_r*.json"))
    print(f"===== {exp} 汇总（control: {len(ctrl)} runs）=====")
    print(f"{'variant':14s} {'runs':>4s} {'@5':>6s} {'@10':>6s} {'sel':>6s} {'SHRany':>7s} {'SHRratio':>9s} {'blocks':>7s} {'p50ms':>6s}")
    groups = {}
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))["summary"]
        groups.setdefault(d["variant"], []).append(d)
    for v, ds in sorted(groups.items()):
        def m(k):
            return statistics.mean([d[k] for d in ds])
        print(f"{v:14s} {len(ds):>4d} {m('page_cov@5'):>6.3f} {m('page_cov@10'):>6.3f} {m('page_in_selected'):>6.3f} "
              f"{m('SHR_any'):>7.3f} {m('SHR_ratio'):>9.3f} {m('blocks'):>7.2f} {m('latency_p50_ms'):>6.0f}")
    if ctrl:
        ctrl_ds = [json.loads(f.read_text(encoding="utf-8"))["summary"] for f in ctrl]

        def mc(k):
            return statistics.mean([d[k] for d in ctrl_ds])
        print(f"{'control(e0)':14s} {len(ctrl_ds):>4d} {mc('page_cov@5'):>6.3f} {mc('page_cov@10'):>6.3f} {mc('page_in_selected'):>6.3f} "
              f"{mc('SHR_any'):>7.3f} {mc('SHR_ratio'):>9.3f} {mc('blocks'):>7.2f} {mc('latency_p50_ms'):>6.0f}")
        print("--- E0 方差带（control 各轮极差）---")
        for k in ("page_cov@5", "page_cov@10", "page_in_selected", "SHR_any", "SHR_ratio"):
            vals = [d[k] for d in ctrl_ds]
            print(f"  {k}: min={min(vals):.3f} max={max(vals):.3f} range={max(vals)-min(vals):.3f}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("exp")
    r.add_argument("variant")
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--only", default=None)
    r.add_argument("--file", default=None)
    s = sub.add_parser("summary")
    s.add_argument("exp")
    args = ap.parse_args()
    if args.cmd == "run":
        for i in range(1, args.repeat + 1):
            run_once(args.exp, args.variant, i, only=args.only, file=args.file)
    elif args.cmd == "summary":
        summary(args.exp)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
