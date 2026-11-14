"""Same-process, same-batch baseline vs merged_fixed for 58 + h50.

Paired per query: baseline and merged_fixed run back-to-back in one process.
Writes rows incrementally, then a per-query comparison.
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
from exp_qmd_merge import MergedFixed  # noqa: E402
from run_experiments import metrics_from  # noqa: E402

OUT = ROOT / "bench/results/samebatch_merge.json"
ROWS = ROOT / "bench/results/samebatch_merge_rows.jsonl"
KEYS = ["page_cov@5", "page_cov@10", "gold_cov@5", "gold_cov@10", "sel_ratio", "mrr"]
SETS = {
    "58": ROOT / "bench/dataset/queries.jsonl",
    "h50": ROOT / "bench/dataset/queries_hard50.jsonl",
}


def instrument(rag):
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
    return calls, rrs


def main() -> None:
    rags = {"baseline": CompanyRAG(ROOT), "merged_fixed": MergedFixed(ROOT)}
    stats = {a: {s: {k: [] for k in KEYS} for s in SETS} for a in rags}
    rows = []
    ROWS.write_text("", encoding="utf-8")
    for set_name, path in SETS.items():
        cases = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"START {set_name}: {len(cases)} cases", flush=True)
        for ci, case in enumerate(cases):
            for qi, q in enumerate(case["queries"]):
                rec = {"set": set_name, "id": case["id"], "style": q.get("style", ""), "q_index": qi}
                for arm, rag in rags.items():
                    calls, rrs = instrument(rag)
                    t = time.perf_counter()
                    res = rag.search(q["text"], mode="auto", profile="wu01")
                    total = (time.perf_counter() - t) * 1000
                    m = metrics_from(res, set(case["target_pages"]), set(case["target_raws"]),
                                     rag.manifest["nodes"])
                    row = {**rec, "arm": arm, **{k: m.get(k) for k in KEYS},
                           "chars": len(res.get("context") or ""), "mode": res.get("mode"),
                           "total_ms": total, "qmd_calls": len(calls), "qmd_ms": sum(calls),
                           "rr_calls": len(rrs), "rr_ms": sum(rrs)}
                    rows.append(row)
                    with ROWS.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    for k in KEYS:
                        stats[arm][set_name][k].append(float(m.get(k, 0)))
                print(f"{set_name} {ci+1}/{len(cases)} {case['id']}/{qi} done", flush=True)

    b = {(r["set"], r["id"], r["style"], r["q_index"]): r for r in rows if r["arm"] == "baseline"}
    f = {(r["set"], r["id"], r["style"], r["q_index"]): r for r in rows if r["arm"] == "merged_fixed"}
    diffs = []
    for key in b:
        d = [k for k in KEYS if abs(float(b[key].get(k, 0)) - float(f[key].get(k, 0))) > 1e-9]
        if d:
            diffs.append({"key": key, "metrics": {k: [b[key].get(k), f[key].get(k)] for k in d},
                          "chars": [b[key].get("chars"), f[key].get("chars")]})

    summary = {}
    for set_name in SETS:
        summary[set_name] = {}
        for arm in rags:
            summary[set_name][arm] = {
                k: round(statistics.mean(stats[arm][set_name][k]), 4) for k in KEYS
            }
            summary[set_name][arm]["n"] = len(stats[arm][set_name]["mrr"])
            summary[set_name][arm]["qmd_calls"] = round(statistics.mean(
                [r["qmd_calls"] for r in rows if r["set"] == set_name and r["arm"] == arm]), 2)
            summary[set_name][arm]["chars"] = round(statistics.mean(
                [r["chars"] for r in rows if r["set"] == set_name and r["arm"] == arm]), 1)
    out = {"summary": summary, "diff_count": len(diffs), "diffs": diffs, "rows": rows}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("SUMMARY", json.dumps(summary, ensure_ascii=False), flush=True)
    print(f"DIFF_COUNT {len(diffs)}", flush=True)
    if diffs:
        print(json.dumps(diffs[:10], ensure_ascii=False, indent=1), flush=True)


if __name__ == "__main__":
    main()
