"""Verify production CompanyRAG (merge enabled) equals bench merged_fixed.

Runs only the production arm (58 + h50) and compares against the same-batch
merged_fixed rows from bench/results/samebatch_merge.json.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from rag_gateway.search import CompanyRAG  # noqa: E402
from run_experiments import metrics_from  # noqa: E402

OUT = ROOT / "bench/results/prod_merge_verify.json"
ROWS = ROOT / "bench/results/prod_merge_rows.jsonl"
REF = ROOT / "bench/results/samebatch_merge.json"
KEYS = ["page_cov@5", "page_cov@10", "gold_cov@5", "gold_cov@10", "sel_ratio", "mrr"]
SETS = {"58": ROOT / "bench/dataset/queries.jsonl", "h50": ROOT / "bench/dataset/queries_hard50.jsonl"}


def main() -> None:
    rag = CompanyRAG(ROOT)
    assert bool(rag.config["structure"].get("merge_restricted_expansions")), "merge flag is off"
    calls, rrs = [], []
    orig_qmd, orig_rr = rag.qmd.query, rag.rerank_api.rerank

    def qmd_wrap(**kwargs):
        t = time.perf_counter()
        out = orig_qmd(**kwargs)
        calls.append((time.perf_counter() - t) * 1000)
        return out

    def rr_wrap(query, docs, top_n=None):
        t = time.perf_counter()
        out = orig_rr(query, docs, top_n)
        rrs.append((time.perf_counter() - t) * 1000)
        return out

    rag.qmd.query = qmd_wrap
    rag.rerank_api.rerank = rr_wrap
    rows = []
    ROWS.write_text("", encoding="utf-8")
    for set_name, path in SETS.items():
        cases = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        for ci, case in enumerate(cases):
            for qi, q in enumerate(case["queries"]):
                calls.clear()
                rrs.clear()
                t = time.perf_counter()
                res = rag.search(q["text"], mode="auto", profile="wu01")
                total = (time.perf_counter() - t) * 1000
                m = metrics_from(res, set(case["target_pages"]), set(case["target_raws"]),
                                 rag.manifest["nodes"])
                row = {"set": set_name, "id": case["id"], "style": q.get("style", ""), "q_index": qi,
                       **{k: m.get(k) for k in KEYS}, "chars": len(res.get("context") or ""),
                       "mode": res.get("mode"), "total_ms": total, "qmd_calls": len(calls),
                       "qmd_ms": sum(calls), "rr_calls": len(rrs), "rr_ms": sum(rrs)}
                rows.append(row)
                with ROWS.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"{set_name} {ci+1}/{len(cases)} {case['id']}/{qi} qmd={len(calls)}", flush=True)

    ref = json.loads(REF.read_text(encoding="utf-8"))["rows"]
    ref_fixed = {(r["set"], r["id"], r["style"], r["q_index"]): r
                 for r in ref if r["arm"] == "merged_fixed"}
    prod = {(r["set"], r["id"], r["style"], r["q_index"]): r for r in rows}
    diffs = []
    for key in ref_fixed:
        p = prod.get(key)
        if p is None:
            diffs.append({"key": key, "missing": True})
            continue
        d = [k for k in KEYS if abs(float(ref_fixed[key].get(k, 0)) - float(p.get(k, 0))) > 1e-9]
        if d:
            diffs.append({"key": key, "metrics": {k: [ref_fixed[key].get(k), p.get(k)] for k in d}})
    summary = {}
    for s in SETS:
        rr = [r for r in rows if r["set"] == s]
        summary[s] = {k: round(statistics.mean(float(r.get(k, 0)) for r in rr), 4) for k in KEYS}
        summary[s]["n"] = len(rr)
        summary[s]["qmd_calls"] = round(statistics.mean(r["qmd_calls"] for r in rr), 2)
        summary[s]["chars"] = round(statistics.mean(r["chars"] for r in rr), 1)
    OUT.write_text(json.dumps({"summary": summary, "diff_count": len(diffs), "diffs": diffs,
                               "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("SUMMARY", json.dumps(summary, ensure_ascii=False), flush=True)
    print("DIFF_COUNT", len(diffs), flush=True)
    if diffs:
        print(json.dumps(diffs[:5], ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    main()
