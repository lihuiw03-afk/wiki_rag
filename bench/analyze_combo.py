# -*- coding: utf-8 -*-
"""bench/analyze_combo.py — 组合实验分析（ctrl / A / A_x115 / C / C_x115）"""
import json
from pathlib import Path

base = Path(__file__).resolve().parents[1] / "bench/results"
files = {
    "ctrl": "exp_e0_control_r1.json",
    "A": "exp_e6_A_r1.json",
    "A_x115": "exp_combo_A_x115_r1.json",
    "C": "exp_e6_C_r1.json",
    "C_x115": "exp_combo_C_x115_r1.json",
}
labels = list(files)
Vs = {lab: {q["id"] + "|" + q["style"]: q for q in json.loads((base / f).read_text(encoding="utf-8"))["per_query"]}
      for lab, f in files.items()}

print("=== 组合矩阵（各 3 轮一致；此处 r1 代表）===")
print(f"{'config':8s} {'@5':>6s} {'@10':>6s} {'sel':>6s} {'SHRany':>7s} {'SHRr':>6s} {'blk':>5s} {'chars':>6s} {'p50':>6s}")
for lab in labels:
    s = json.loads((base / files[lab]).read_text(encoding="utf-8"))["summary"]
    print(f"{lab:8s} {s['page_cov@5']:>6.3f} {s['page_cov@10']:>6.3f} {s['page_in_selected']:>6.3f} "
          f"{s['SHR_any']:>7.3f} {s['SHR_ratio']:>6.3f} {s['blocks']:>5.2f} {s['chars']:>6.0f} {s['latency_p50_ms']:>6.0f}")

keys = sorted(Vs["ctrl"].keys())
print("\n=== 任两配置间 sel/@5/@10 存在差异的行 ===")
n_diff = 0
for k in keys:
    sets = {lab: (round(Vs[lab][k].get("sel_ratio") or 0, 2), round(Vs[lab][k].get("page_cov@5") or 0, 2), round(Vs[lab][k].get("page_cov@10") or 0, 2)) for lab in labels}
    if len(set(sets.values())) > 1:
        n_diff += 1
        row = " ".join(f"{lab}={sets[lab]}" for lab in labels)
        print(f"  {k:22s} {row}")
print(f"  共 {n_diff} 行")

print("\n=== A vs A_x115 / C vs C_x115 微观差异（块数/字符/丢弃）===")
for a, b in [("A", "A_x115"), ("C", "C_x115")]:
    cnt = 0
    for k in keys:
        qa, qb = Vs[a][k], Vs[b][k]
        if (qa.get("blocks"), qa.get("chars"), qa.get("dropped")) != (qb.get("blocks"), qb.get("chars"), qb.get("dropped")):
            cnt += 1
            print(f"  {a}->{b} {k:22s} blk {qa.get('blocks')}->{qb.get('blocks')} chars {qa.get('chars')}->{qb.get('chars')} drop {qa.get('dropped')}->{qb.get('dropped')}")
    print(f"  {a} -> {b}: {cnt} 行微观差异")

print("\n=== 回归检查（vs ctrl）===")
for lab in ["A", "A_x115", "C", "C_x115"]:
    reg = [k for k in keys if (Vs[lab][k].get("sel_ratio") or 0) < (Vs["ctrl"][k].get("sel_ratio") or 0) - 1e-9
           or (Vs[lab][k].get("page_cov@5") or 0) < (Vs["ctrl"][k].get("page_cov@5") or 0) - 1e-9]
    print(f"  {lab:8s}: {len(reg)} 条 {reg if reg else ''}")

print("\n=== 关键案例 ===")
for cid in ["C15", "E06", "X04", "C14", "C12", "S22"]:
    for k in [k for k in keys if k.split("|")[0] == cid]:
        line = f"  {k:22s}"
        for lab in labels:
            q = Vs[lab][k]
            line += f" | {lab}: sel={q.get('sel_ratio') or 0:.2f} b={q.get('blocks')} d={q.get('dropped')}"
        print(line)
