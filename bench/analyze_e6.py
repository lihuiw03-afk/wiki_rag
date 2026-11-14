# -*- coding: utf-8 -*-
"""bench/analyze_e6.py — E6 首轮结果分析：逐案例 / 回归检查 / 残差

用法: python bench/analyze_e6.py [control_file] [A_file] [B_file] [C_file]
"""
import json
import sys
from pathlib import Path

base = Path(__file__).resolve().parents[1] / "bench/results"
names = sys.argv[1:5] or ["exp_e0_control_r1.json", "exp_e6_A_r1.json", "exp_e6_B_r1.json", "exp_e6_C_r1.json"]
labels = ["ctrl", "A", "B", "C"]
Vs = {}
for lab, f in zip(labels, names):
    Vs[lab] = {(q["id"], q["style"]): q for q in json.loads((base / f).read_text(encoding="utf-8"))["per_query"]}

print("=== 池/选中计数（SHR 口径核对）===")
for lab in labels:
    V = Vs[lab]
    pool = [k for k, q in V.items() if q.get("pool_any")]
    selany = [k for k in pool if V[k].get("sel_any")]
    full = [k for k in pool if (V[k].get("sel_ratio") or 0) >= 0.999]
    print(f"  {lab:5s} pool={len(pool)} sel_any={len(selany)} full_ratio={len(full)}")

missed = [k for k, q in Vs["ctrl"].items() if not q.get("pool_any")]
print(f"\n=== 池外残差（候选池没找到金标，变体无法影响）: {len(missed)} 条 ===")
for k in sorted(missed):
    print("   ", k[0], k[1])

print("\n=== 逐条差异（sel_ratio 四个变体不完全一致的行）===")
keys = sorted(Vs["ctrl"].keys())
diffs = []
for k in keys:
    vals = [round(Vs[l][k].get("sel_ratio") or 0, 3) for l in labels]
    if len(set(vals)) > 1:
        diffs.append(k)
        q0 = Vs["ctrl"][k]
        print(f"  {k[0]:6s} {k[1]:9s} ctrl/A/B/C={vals}  ctrl:blk={q0.get('blocks')} drop={q0.get('dropped')} chars={q0.get('chars')}  A:blk={Vs['A'][k].get('blocks')} C:blk={Vs['C'][k].get('blocks')} B:blk={Vs['B'][k].get('blocks')}")

print(f"\n=== 回归检查（变体 < 对照 的行）===")
for lab in ["A", "B", "C"]:
    reg = [k for k in keys if (Vs[lab][k].get("sel_ratio") or 0) < (Vs["ctrl"][k].get("sel_ratio") or 0) - 1e-9]
    print(f"  {lab}: {len(reg)} 条 {[f'{a}:{b}' for a, b in reg] if reg else ''}")

print("\n=== 关键案例逐条 ===")
for cid in ["C15", "E06", "X01", "X02", "X04", "X06", "C12", "S22"]:
    rows = [k for k in keys if k[0] == cid]
    for k in rows:
        line = f"  {k[0]:6s} {k[1]:9s}"
        for lab in labels:
            q = Vs[lab][k]
            line += f" | {lab}: r={(q.get('sel_ratio') or 0):.2f} b={q.get('blocks')} d={q.get('dropped')}"
        print(line)
