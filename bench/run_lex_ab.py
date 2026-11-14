# -*- coding: utf-8 -*-
"""bench/run_lex_ab.py — 词法策略 A/B/C/C2 电池（58 / h50 / literal）

用法：
  python bench/run_lex_ab.py --policy C --sets 58,h50,literal --repeat 3
  python bench/run_lex_ab.py --policy B --sets 58,h50 --repeat 3

- 58 / h50 走 mode=auto（生产路径）；literal 固定 mode=balanced（见 LITERAL_SET_DESIGN）。
- 结果：bench/results/exp_lex_{policy}_{set}_r{N}.json（追加式找空位，不覆盖）。
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
import os  # noqa: E402

os.chdir(ROOT)

import lex_routing  # noqa: E402
from run_experiments import PolicyRAG, metrics_from  # noqa: E402

rag = PolicyRAG(ROOT)  # control 基座 = 当前生产
nodes = rag.manifest["nodes"]


def load_rows(p):
    return [json.loads(l) for l in (ROOT / p).read_text(encoding="utf-8").splitlines() if l.strip()]


SETS = {
    "58": ("bench/dataset/queries.jsonl", "auto"),
    "h50": ("bench/dataset/queries_hard50.jsonl", "auto"),
    "literal": ("bench/dataset/queries_literal.jsonl", "balanced"),
}


def next_round(policy, setname):
    i = 1
    while (ROOT / "bench/results" / f"exp_lex_{policy}{BUDGET_TAG}_{setname}_r{i}.json").exists():
        i += 1
    return i


def run_once(policy, setname, repeat):
    path, mode = SETS[setname]
    rows = load_rows(path)
    per = []
    for r in rows:
        for q in r["queries"]:
            res = rag.search(q["text"], mode=mode, profile="wu01")
            m = metrics_from(res, set(r["target_pages"]), set(r["target_raws"]), nodes)
            m.update({"id": r["id"], "style": q["style"],
                      "lex_used": any(s.get("type") == "lex" for s in (res.get("searches") or [])),
                      "rerank_mode": res.get("rerank_mode")})
            per.append(m)
            if args.sleep:
                time.sleep(args.sleep)
    def mean(k):
        return statistics.mean([x.get(k, 0) for x in per]) if per else 0
    lat = [x.get("elapsed_ms", 0) for x in per]
    lat_sorted = sorted(lat)
    summary = {
        "exp": "lex", "policy": policy, "set": setname, "repeat": repeat, "n": len(per),
        "page_cov@5": mean("page_cov@5"), "page_cov@10": mean("page_cov@10"),
        "gold_cov@5": mean("gold_cov@5"), "gold_cov@10": mean("gold_cov@10"),
        "page_in_selected": mean("page_in_selected"), "sel_ratio": mean("sel_ratio"),
        "mrr": mean("mrr"), "blocks": mean("blocks"), "chars": mean("chars"),
        "latency_mean_ms": mean("elapsed_ms"),
        "latency_p50_ms": lat_sorted[len(lat_sorted) // 2] if lat_sorted else 0,
        "latency_p90_ms": lat_sorted[int(len(lat_sorted) * 0.9)] if lat_sorted else 0,
        "lex_rate": mean("lex_used"),
        "rerank_api_rate": sum(1 for x in per if x.get("rerank_mode") == "api") / max(1, len(per)),
        "slow_rows": sum(1 for x in per if x.get("elapsed_ms", 0) > 10000),
    }
    rn = next_round(policy, setname)
    out = {"summary": summary, "per_query": per}
    (ROOT / "bench/results" / f"exp_lex_{policy}{BUDGET_TAG}_{setname}_r{rn}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"[{policy}/{setname} r{rn}] n={len(per)} @5={summary['page_cov@5']:.3f} "
          f"@10={summary['page_cov@10']:.3f} sel={summary['sel_ratio']:.3f} "
          f"lex_rate={summary['lex_rate']:.2f} p50={summary['latency_p50_ms']:.0f}ms "
          f"p90={summary['latency_p90_ms']:.0f}ms", flush=True)


ap = argparse.ArgumentParser()
ap.add_argument("--policy", required=True, choices=list(lex_routing.POLICIES))
ap.add_argument("--sets", default="58,h50,literal")
ap.add_argument("--repeat", type=int, default=3)
ap.add_argument("--sleep", type=float, default=0.4)
ap.add_argument("--budget", default="control", choices=["control", "A"])
args = ap.parse_args()

BUDGET_TAG = "_A" if args.budget == "A" else ""
rag.budget_policy = args.budget
lex_routing.activate(args.policy)
try:
    for r in range(args.repeat):
        for s in [x.strip() for x in args.sets.split(",") if x.strip()]:
            run_once(args.policy, s, r + 1)
finally:
    lex_routing.deactivate()
print("DONE", flush=True)
