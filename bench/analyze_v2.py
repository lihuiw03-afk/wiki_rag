# -*- coding: utf-8 -*-
"""bench/analyze_v2.py — V2 剩余机制评估汇总（58集 + 困难集，全部以 E6-A 为基座）

读 r1 代表文件（所有变体各轮已逐位一致，hy h50 除外已在文中注明）。
"""
import json
import statistics
from pathlib import Path

b = Path(__file__).resolve().parents[1] / "bench/results"


def load(f):
    return json.loads((b / f).read_text(encoding="utf-8"))


def snap(per):
    return {q["id"] + "|" + q["style"]: q for q in per}


def stats(rows, k):
    return statistics.mean([r.get(k, 0) for r in rows]) if rows else 0


def pick_base(pattern):
    cands = sorted(b.glob(pattern))
    for f in reversed(cands):
        rows = json.loads(f.read_text(encoding="utf-8"))["per_query"]
        if rows and "gold_cov@5" in rows[0]:
            return f.name
    return cands[0].name if cands else None


SETS = [
    ("58集", pick_base("exp_e6_A_r*.json"), "exp_v2_{v}_r1.json", ["e4", "hyde", "res2", "res6", "aug8", "aug20"]),
    ("困难集", pick_base("exp_h50_A_r*.json"), "exp_v2h_{v}_r1.json", ["e4", "hyde", "res2", "res6", "aug8", "aug20"]),
]

for name, base_f, pat, variants in SETS:
    base = load(base_f)
    B = snap(base["per_query"])
    brows = base["per_query"]
    print(f"\n===== {name}（基座 E6-A，n={len(brows)}）=====")
    print(f"{'conf':6s} {'@5':>6s} {'@10':>6s} {'sel':>6s} {'SHRr':>6s} {'blk':>5s} {'chars':>6s} {'p50ms':>6s} {'e4api':>5s} {'diff行':>5s}")
    print(f"{'A':6s} {stats(brows,'gold_cov@5'):>6.3f} {stats(brows,'gold_cov@10'):>6.3f} "
          f"{stats(brows,'sel_ratio'):>6.3f} {base['summary']['SHR_ratio']:>6.3f} "
          f"{stats(brows,'blocks'):>5.2f} {stats(brows,'chars'):>6.0f} {stats(brows,'elapsed_ms'):>6.0f} {'-':>5s} {'-':>5s}")
    for v in variants:
        try:
            d = load(pat.format(v=v))
        except FileNotFoundError:
            print(f"{v:6s} (缺失 {pat.format(v=v)})")
            continue
        rows = d["per_query"]
        S = snap(rows)
        nd = 0
        changes = []
        for k in B:
            a, h = B[k], S.get(k, {})
            if (round(h.get("gold_cov@5", 0) - a.get("gold_cov@5", 0), 3) != 0
                    or round(h.get("gold_cov@10", 0) - a.get("gold_cov@10", 0), 3) != 0
                    or round(h.get("sel_ratio", 0) - a.get("sel_ratio", 0), 3) != 0):
                nd += 1
                changes.append(k)
            if (h.get("blocks"), h.get("chars"), h.get("dropped")) != (a.get("blocks"), a.get("chars"), a.get("dropped")):
                pass
        micro = sum(1 for k in B if (S[k].get("blocks"), S[k].get("chars"), S[k].get("dropped")) != (B[k].get("blocks"), B[k].get("chars"), B[k].get("dropped")))
        e4 = d["summary"].get("e4_api_total", 0)
        print(f"{v:6s} {stats(rows,'gold_cov@5'):>6.3f} {stats(rows,'gold_cov@10'):>6.3f} "
              f"{stats(rows,'sel_ratio'):>6.3f} {d['summary']['SHR_ratio']:>6.3f} "
              f"{stats(rows,'blocks'):>5.2f} {stats(rows,'chars'):>6.0f} {stats(rows,'elapsed_ms'):>6.0f} {e4:>5.0f} {nd:>5d}")
        if changes:
            print(f"       变化行: {changes}")
    # 微差统计
    print("  微差（块组成）行数：", end="")
    for v in variants:
        try:
            d = load(pat.format(v=v))
        except FileNotFoundError:
            continue
        S = snap(d["per_query"])
        micro = sum(1 for k in B if (S[k].get("blocks"), S[k].get("chars"), S[k].get("dropped")) != (B[k].get("blocks"), B[k].get("chars"), B[k].get("dropped")))
        print(f" {v}={micro}", end="")
    print()
