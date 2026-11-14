# -*- coding: utf-8 -*-
"""bench/analyze_e3.py — E3 折扣系数对照分析（control vs x115 vs x130）"""
import json
from pathlib import Path

base = Path(__file__).resolve().parents[1] / "bench/results"


def load(f):
    return {q["id"] + "|" + q["style"]: q for q in json.loads((base / f).read_text(encoding="utf-8"))["per_query"]}


def summ(f):
    return json.loads((base / f).read_text(encoding="utf-8"))["summary"]


print("=== E3 轮次稳定性 ===")
for f in ["exp_e3_x115_r1.json", "exp_e3_x115_r2.json", "exp_e3_x115_r3.json",
          "exp_e3_x130_r1.json", "exp_e3_x130_r2.json", "exp_e3_x130_r3.json"]:
    try:
        s = summ(f)
        print(f"  {f:26s} @5={s['page_cov@5']:.3f} @10={s['page_cov@10']:.3f} sel={s['page_in_selected']:.3f} "
              f"SHR={s['SHR_any']:.3f}/{s['SHR_ratio']:.3f} blk={s['blocks']:.1f} p50={s['latency_p50_ms']:.0f}ms")
    except FileNotFoundError:
        print(f"  {f:26s} (缺失)")

ctrl = load("exp_e0_control_r1.json")
v115 = load("exp_e3_x115_r1.json")
v130 = load("exp_e3_x130_r1.json")

print("\n=== 池/选中计数 ===")
for name, V in [("ctrl", ctrl), ("x115", v115), ("x130", v130)]:
    pool = [k for k, q in V.items() if q.get("pool_any")]
    selany = [k for k in pool if V[k].get("sel_any")]
    full = [k for k in pool if (V[k].get("sel_ratio") or 0) >= 0.999]
    print(f"  {name:5s} pool={len(pool)} sel_any={len(selany)} full={len(full)}")

keys = sorted(ctrl.keys())
print("\n=== 差异行（sel / @5 / @10 有变化的行）===")
for k in keys:
    vals = [round(ctrl[k].get("sel_ratio") or 0, 2), round(v115[k].get("sel_ratio") or 0, 2), round(v130[k].get("sel_ratio") or 0, 2)]
    c5 = [round(ctrl[k].get("page_cov@5") or 0, 2), round(v115[k].get("page_cov@5") or 0, 2), round(v130[k].get("page_cov@5") or 0, 2)]
    c10 = [round(ctrl[k].get("page_cov@10") or 0, 2), round(v115[k].get("page_cov@10") or 0, 2), round(v130[k].get("page_cov@10") or 0, 2)]
    if len(set(vals)) > 1 or len(set(c5)) > 1 or len(set(c10)) > 1:
        print(f"  {k:24s} sel={vals}  @5={c5}  @10={c10}")

print("\n=== 回归检查（scaled < ctrl 的行）===")
for name, V in [("x115", v115), ("x130", v130)]:
    regs = {"sel": [], "@5": [], "@10": []}
    for k in keys:
        if (V[k].get("sel_ratio") or 0) < (ctrl[k].get("sel_ratio") or 0) - 1e-9:
            regs["sel"].append(k)
        if (V[k].get("page_cov@5") or 0) < (ctrl[k].get("page_cov@5") or 0) - 1e-9:
            regs["@5"].append(k)
        if (V[k].get("page_cov@10") or 0) < (ctrl[k].get("page_cov@10") or 0) - 1e-9:
            regs["@10"].append(k)
    print(f"  {name}: sel回归={len(regs['sel'])} @5回归={len(regs['@5'])} @10回归={len(regs['@10'])}")
    for lab in ["sel", "@5", "@10"]:
        fld = "sel_ratio" if lab == "sel" else f"page_cov@{lab[1:]}"
        for k in regs[lab][:10]:
            print(f"     [{lab}] {k}  ctrl={ctrl[k].get(fld):.3f} -> {V[k].get(fld):.3f}")

print("\n=== 关键案例 ===")
for cid in ["C15", "E06", "X01", "X02", "X04", "X06", "C12", "S22"]:
    for k in [k for k in keys if k.split("|")[0] == cid]:
        line = f"  {k:24s}"
        for name, V in [("ctrl", ctrl), ("x115", v115), ("x130", v130)]:
            q = V[k]
            line += f" | {name}: sel={q.get('sel_ratio') or 0:.2f} b={q.get('blocks')} d={q.get('dropped')}"
        print(line)
