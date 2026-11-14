# -*- coding: utf-8 -*-
"""bench/analyze_noe6.py — V2-NoE6 消融分析：control 基座上四机制 + E6 交互对照

对照组 = control（无 E6-A，生产现状）；每个机制给出：
  standalone Δ = 机制(control) − control
  on-top  Δ = 机制(E6-A) − E6-A（引用 V2 已跑结果）
并输出逐条差异行/回归计数，支撑 "E6 interaction / ablation" 结论。
"""
import json
import statistics
from pathlib import Path

b = Path(__file__).resolve().parents[1] / "bench/results"


def load(f):
    return json.loads((b / f).read_text(encoding="utf-8"))


def snap(per):
    return {q["id"] + "|" + q["style"]: q for q in per}


def mean(rows, k):
    return statistics.mean([r.get(k, 0) for r in rows]) if rows else 0


def pick_base(pattern):
    for f in sorted(b.glob(pattern), reverse=True):
        try:
            rows = load(f.name)["per_query"]
        except Exception:  # noqa: BLE001
            continue
        if rows and "gold_cov@5" in rows[0]:
            return f.name
    return None


VARS = ["e4", "hyde", "res2", "res6", "aug8", "aug20"]
SETS = [
    ("58集", "exp_e0_control_r*.json", "exp_v2c_{v}_r1.json", "exp_v2_{v}_r1.json", "exp_e6_A_r*.json"),
    ("困难集", "exp_h50_control_r*.json", "exp_v2hc_{v}_r1.json", "exp_v2h_{v}_r1.json", "exp_h50_A_r*.json"),
]

for name, cpat, patc, patt, bpat in SETS:
    ctrl_f = pick_base(cpat)
    base_f = pick_base(bpat)
    if not ctrl_f or not base_f:
        print(f"{name}: 基线缺失 (control={ctrl_f}, ontop-base={base_f})")
        continue
    C = snap(load(ctrl_f)["per_query"])
    crows = load(ctrl_f)["per_query"]
    T = snap(load(base_f)["per_query"])
    trows = load(base_f)["per_query"]
    print(f"\n===== {name} =====")
    print(f"  control: {ctrl_f} | on-top 基座: {base_f}")
    print(f"{'机制':7s} | {'standalone Δsel':>14s} {'Δ@10':>6s} {'diff':>4s} {'回归':>4s} | {'on-top Δsel':>11s} {'Δ@10':>6s} {'diff':>4s} {'回归':>4s} | 判定")
    c_sel, c_10 = mean(crows, "sel_ratio"), mean(crows, "gold_cov@10")
    t_sel, t_10 = mean(trows, "sel_ratio"), mean(trows, "gold_cov@10")
    for v in VARS:
        line = f"{v:7s} |"
        ok = True
        try:
            dc = snap(load(patc.format(v=v))["per_query"])
            dsel = mean(list(dc.values()), "sel_ratio") - c_sel
            d10 = mean(list(dc.values()), "gold_cov@10") - c_10
            nd = sum(1 for k in C if (round(dc[k].get('gold_cov@5', 0) - C[k].get('gold_cov@5', 0), 3) != 0
                                      or round(dc[k].get('gold_cov@10', 0) - C[k].get('gold_cov@10', 0), 3) != 0
                                      or round(dc[k].get('sel_ratio', 0) - C[k].get('sel_ratio', 0), 3) != 0))
            reg = sum(1 for k in C if (dc[k].get('sel_ratio', 0) < C[k].get('sel_ratio', 0) - 1e-9
                                       or dc[k].get('gold_cov@5', 0) < C[k].get('gold_cov@5', 0) - 1e-9))
            line += f" {dsel:>+14.3f} {d10:>+6.3f} {nd:>4d} {reg:>4d} |"
        except FileNotFoundError:
            line += f" {'-':>14s} {'-':>6s} {'-':>4s} {'-':>4s} |"
            ok = False
        try:
            dt = snap(load(patt.format(v=v))["per_query"])
            dsel_t = mean(list(dt.values()), "sel_ratio") - t_sel
            d10_t = mean(list(dt.values()), "gold_cov@10") - t_10
            nd_t = sum(1 for k in T if (round(dt[k].get('gold_cov@5', 0) - T[k].get('gold_cov@5', 0), 3) != 0
                                        or round(dt[k].get('gold_cov@10', 0) - T[k].get('gold_cov@10', 0), 3) != 0
                                        or round(dt[k].get('sel_ratio', 0) - T[k].get('sel_ratio', 0), 3) != 0))
            reg_t = sum(1 for k in T if (dt[k].get('sel_ratio', 0) < T[k].get('sel_ratio', 0) - 1e-9
                                         or dt[k].get('gold_cov@5', 0) < T[k].get('gold_cov@5', 0) - 1e-9))
            line += f" {dsel_t:>+11.3f} {d10_t:>+6.3f} {nd_t:>4d} {reg_t:>4d} |"
        except FileNotFoundError:
            line += f" {'-':>11s} {'-':>6s} {'-':>4s} {'-':>4s} |"
        if ok and 'dsel' in dir():
            pass
        print(line)
    # 逐条差异行明细（standalone）
    print("  standalone 逐条变化：")
    for v in VARS:
        try:
            dc = snap(load(patc.format(v=v))["per_query"])
        except FileNotFoundError:
            continue
        ch = []
        for k in sorted(C):
            if (round(dc[k].get('gold_cov@5', 0) - C[k].get('gold_cov@5', 0), 3) != 0
                    or round(dc[k].get('gold_cov@10', 0) - C[k].get('gold_cov@10', 0), 3) != 0
                    or round(dc[k].get('sel_ratio', 0) - C[k].get('sel_ratio', 0), 3) != 0):
                ch.append(f"{k}({C[k].get('sel_ratio',0):.2f}->{dc[k].get('sel_ratio',0):.2f})")
        print(f"    {v}: {ch if ch else '（无指标级变化）'}")
