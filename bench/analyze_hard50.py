# -*- coding: utf-8 -*-
"""bench/analyze_hard50.py — 困难集（50 条）复验分析：control vs E6-A"""
import json
import statistics
from pathlib import Path

base = Path(__file__).resolve().parents[1] / "bench/results"
ctrl = json.loads((base / "exp_h50_control_r1.json").read_text(encoding="utf-8"))["per_query"]
e6a = json.loads((base / "exp_h50_A_r1.json").read_text(encoding="utf-8"))["per_query"]
C = {q["id"]: q for q in ctrl}
A = {q["id"]: q for q in e6a}

print("=== 总览 ===")
def mean(rows, k):
    return statistics.mean([r.get(k, 0) for r in rows]) if rows else 0
for name, rows in (("ctrl", ctrl), ("A", e6a)):
    print(f"  {name}: n={len(rows)} @5={mean(rows,'gold_cov@5'):.3f} @10={mean(rows,'gold_cov@10'):.3f} "
          f"sel={mean(rows,'sel_ratio'):.3f} blk={mean(rows,'blocks'):.1f} chars={mean(rows,'chars'):.0f} p50={mean(rows,'elapsed_ms'):.0f}ms")

print("\n=== 分类别（n | ctrl@10 → A@10 | 池覆盖率 | sel | SHR | 说明）===")
cats = {}
for q in ctrl:
    cats.setdefault(q["category"], []).append(q["id"])
for cat, ids in cats.items():
    cr = [C[i] for i in ids]
    ar = [A[i] for i in ids]
    pool = [i for i in ids if C[i].get("pool_any")]
    shr_c = sum(1 for i in pool if C[i].get("sel_any")) / max(1, len(pool))
    shr_a = sum(1 for i in pool if A[i].get("sel_any")) / max(1, len(pool))
    print(f"  {cat:6s} n={len(ids):2d} | ctrl@10={mean(cr,'gold_cov@10'):.2f} → A@10={mean(ar,'gold_cov@10'):.2f} "
          f"| 池覆盖={len(pool)}/{len(ids)} | sel {mean(cr,'sel_ratio'):.2f}→{mean(ar,'sel_ratio'):.2f} "
          f"| SHR {shr_c:.2f}→{shr_a:.2f} | blk {mean(cr,'blocks'):.1f}→{mean(ar,'blocks'):.1f}")

print("\n=== 逐条差异（sel_ratio / gold_cov / dropped 任一不同）===")
for qid in sorted(set(C) & set(A)):
    c, a = C[qid], A[qid]
    if (round(c.get("sel_ratio") or 0, 3) != round(a.get("sel_ratio") or 0, 3)
            or round(c.get("gold_cov@10") or 0, 3) != round(a.get("gold_cov@10") or 0, 3)
            or c.get("dropped", 0) != a.get("dropped", 0)):
        print(f"  {qid:5s} [{c['category']}] sel {c.get('sel_ratio'):.2f}→{a.get('sel_ratio'):.2f} | "
              f"@10 {c.get('gold_cov@10'):.2f}→{a.get('gold_cov@10'):.2f} | blk {c['blocks']}→{a['blocks']} | "
              f"drop {c.get('dropped')}→{a.get('dropped')} | chars {c.get('chars')}→{a.get('chars')}")

print("\n=== 回归检查 ===")
reg = [qid for qid in C if (A[qid].get("sel_ratio") or 0) < (C[qid].get("sel_ratio") or 0) - 1e-9
       or (A[qid].get("gold_cov@5") or 0) < (C[qid].get("gold_cov@5") or 0) - 1e-9]
print("  回归:", len(reg), reg or "")

print("\n=== 池外缺失（ctrl 池未含金标 = 检索层缺口）===")
miss = [qid for qid in C if not C[qid].get("pool_any")]
bycat = {}
for qid in miss:
    bycat.setdefault(C[qid]["category"], []).append(qid)
for k, v in bycat.items():
    print(f"  {k}: {len(v)} 条 {v}")
