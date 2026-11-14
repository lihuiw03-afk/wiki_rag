# -*- coding: utf-8 -*-
"""bench/run_ablation.py — 消融实验：各模块对检索质量的贡献

变体：full / no_rerank / no_augment / lex_only / vec_only
口径：全部强制检索（绕过 gate），指标与检索评测一致；同一数据集、同一进程。

用法：.venv/Scripts/python.exe bench/run_ablation.py
输出：bench/results/ablation_<date>.json / .md
"""
from __future__ import annotations
import copy, json, os, statistics, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from rag_gateway.search import CompanyRAG
import rag_gateway.search as sm

DS = ROOT / "bench/dataset/queries.jsonl"
KS = (5, 10)
TODAY = time.strftime("%Y%m%d")


def agg(values):
    return statistics.mean(values) if values else 0.0


def main():
    rows = [json.loads(l) for l in DS.read_text(encoding="utf-8").splitlines() if l.strip()]
    rag = CompanyRAG(ROOT)
    nodes = rag.manifest["nodes"]

    def metas(items):
        pages, raws = set(), set()
        for it in items:
            n = nodes.get(it.get("node_id")) or {}
            if n.get("page_relpath"):
                pages.add(n["page_relpath"])
            if n.get("raw_path"):
                raws.add(n["raw_path"])
        return pages, raws

    def metrics(r, gold_pages, gold_raws):
        retr = sorted(r.get("retrieved") or [], key=lambda x: x.get("score") or 0, reverse=True)
        sel = r.get("selected") or []
        out = {}
        for k in KS:
            rp, rr = metas(retr[:k])
            out[f"page_cov@{k}"] = len(gold_pages & rp) / max(1, len(gold_pages))
            out[f"raw_hit@{k}"] = 1.0 if (gold_raws & rr) else 0.0
        sp, sr = metas(sel)
        out["page_in_selected"] = len(gold_pages & sp) / max(1, len(gold_pages))
        out["raw_in_selected"] = 1.0 if (gold_raws & sr) else 0.0
        rank = next((i + 1 for i, it in enumerate(retr)
                     if ((nodes.get(it.get("node_id")) or {}).get("page_relpath") in gold_pages)), None)
        out["mrr"] = (1.0 / rank) if rank else 0.0
        out["elapsed_ms"] = r.get("elapsed_ms") or 0
        return out

    def run_variant(name, apply, restore):
        apply()
        recs = []
        try:
            for row in rows:
                gold_pages = set(row["target_pages"])
                gold_raws = set(row["target_raws"])
                for q in row["queries"]:
                    try:
                        r = rag.search(q["text"], mode="auto", profile="wu01")
                        recs.append(metrics(r, gold_pages, gold_raws))
                    except Exception as e:
                        recs.append({"error": str(e)[:100], "elapsed_ms": 0})
        finally:
            restore()
        s = {"n": len(recs)}
        for k in ["page_cov@5", "page_cov@10", "page_in_selected", "raw_hit@10", "raw_in_selected", "mrr"]:
            s[k] = agg([x.get(k, 0) for x in recs])
        lat = [x.get("elapsed_ms", 0) for x in recs if x.get("elapsed_ms")]
        s["latency_mean_ms"] = agg(lat)
        s["latency_p50_ms"] = statistics.median(lat) if lat else 0
        print(f"[{name}] page@5={s['page_cov@5']:.2f} page@10={s['page_cov@10']:.2f} "
              f"sel={s['page_in_selected']:.2f} mrr={s['mrr']:.2f} p50={s['latency_p50_ms']:.0f}ms")
        return s

    modes = rag.config["retrieval"]["modes"]
    baseline = copy.deepcopy(modes)
    orig_bts = sm.build_typed_searches

    def restore_modes():
        for k, v in baseline.items():
            modes[k] = copy.deepcopy(v)

    results = {}

    results["full"] = run_variant("full", lambda: None, lambda: None)

    def no_rerank():
        for m in modes.values():
            m["rerank"] = False
    results["no_rerank"] = run_variant("no_rerank", no_rerank, restore_modes)

    def no_augment():
        for m in modes.values():
            m["use_graph"] = False
    results["no_augment"] = run_variant("no_augment", no_augment, restore_modes)

    def lex_only():
        for m in modes.values():
            m["use_vector"] = False
    results["lex_only"] = run_variant("lex_only", lex_only, restore_modes)

    def vec_only_apply():
        def bts(query, use_vector, terms, hyde=None):
            s = orig_bts(query, use_vector, terms, hyde=hyde)
            return [x for x in s if x.get("type") != "lex"]
        sm.build_typed_searches = bts

    def vec_only_restore():
        sm.build_typed_searches = orig_bts
    results["vec_only"] = run_variant("vec_only", vec_only_apply, vec_only_restore)

    out_dir = ROOT / "bench/results"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"ablation_{TODAY}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    lines = [f"# 消融实验（{TODAY}）", "",
             "| 变体 | 页@5 | 页@10 | 入选上下文 | 来源@10 | MRR | 延迟p50 |",
             "|---|---|---|---|---|---|---|"]
    for name, s in results.items():
        lines.append(f"| {name} | {100*s['page_cov@5']:.0f}% | {100*s['page_cov@10']:.0f}% | "
                     f"{100*s['page_in_selected']:.0f}% | {100*s['raw_hit@10']:.0f}% | {s['mrr']:.2f} | "
                     f"{s['latency_p50_ms']:.0f}ms |")
    (out_dir / f"ablation_{TODAY}.md").write_text("\n".join(lines) + "\n",
                                                  encoding="utf-8", newline="\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
