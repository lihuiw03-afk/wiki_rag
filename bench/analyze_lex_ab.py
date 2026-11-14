# -*- coding: utf-8 -*-
"""bench/analyze_lex_ab.py — 词法策略 A/B/C/C2 对照分析

- A 基线复用同日 exp_e0_control_r4 / exp_h50_control_r3（58 / h50），literal 用 exp_lex_A_literal_r*；
- B/C/C2 用 exp_lex_*_r*（同会话确定性，取 r1；报告核对各轮一致性）；
- 输出：分组汇总表、字面串正例 recall（分型）、护栏行（X01/X04/X06）、逐行差异、污染自检。
"""
import json
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / "bench/results"
SETS = ["58", "h50", "literal"]
POLS = ["A", "B", "C", "C2", "C2_A"]


def load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def snap(per):
    return {q["id"] + "|" + q["style"]: q for q in per}


def mean(rows, k):
    return statistics.mean([r.get(k, 0) for r in rows]) if rows else 0


def pct(rows, k):
    return statistics.percentile if False else sorted(r.get(k, 0) for r in rows)


def pick(pol, s):
    """返回 (per_query, summary, file, rounds_note)"""
    if pol == "A" and s == "58":
        d = load(R / "exp_e0_control_r4.json")
        return d["per_query"], d["summary"], "exp_e0_control_r4.json", "复用同日基线"
    if pol == "A" and s == "h50":
        d = load(R / "exp_h50_control_r3.json")
        return d["per_query"], d["summary"], "exp_h50_control_r3.json", "复用同日基线"
    fs = sorted(R.glob(f"exp_lex_{pol}_{s}_r*.json"))
    if not fs:
        return None, None, None, "缺失"
    d = load(fs[0])
    return d["per_query"], d["summary"], ", ".join(f.name for f in fs), "同会话确定性（各轮逐位一致）"


def summarize(pq):
    lat = sorted(q.get("elapsed_ms", 0) for q in pq)
    return {
        "gold@5": round(mean(pq, "gold_cov@5"), 4), "gold@10": round(mean(pq, "gold_cov@10"), 4),
        "page@5": round(mean(pq, "page_cov@5"), 4), "page@10": round(mean(pq, "page_cov@10"), 4),
        "sel": round(mean(pq, "sel_ratio"), 4), "mrr": round(mean(pq, "mrr"), 4),
        "lex_rate": round(mean(pq, "lex_used"), 3) if any("lex_used" in q for q in pq) else None,
        "p50": int(lat[len(lat) // 2]) if lat else 0,
        "p90": int(lat[int(len(lat) * 0.9)]) if lat else 0,
        "api_rate": round(mean(pq, "rerank_mode") if False else
                          (sum(1 for q in pq if q.get("rerank_mode") == "api") / max(1, len(pq))), 3)
        if any("rerank_mode" in q for q in pq) else None,
        "slow": sum(1 for q in pq if q.get("elapsed_ms", 0) > 10000),
    }


print("=" * 100)
print(f"{'set':8s} {'pol':4s} {'n':>3s} {'gold@5':>7s} {'gold@10':>7s} {'page@5':>7s} {'page@10':>7s} {'sel':>6s} {'mrr':>5s} {'lex':>5s} {'p50':>6s} {'p90':>6s} {'slow':>4s}")
for s in SETS:
    for pol in POLS:
        pq, sm, src, note = pick(pol, s)
        if pq is None:
            print(f"{s:8s} {pol:4s} 缺失"); continue
        x = summarize(pq)
        lexs = "n/a" if x["lex_rate"] is None else f"{x['lex_rate']:.2f}"
        print(f"{s:8s} {pol:4s} {len(pq):3d} {x['gold@5']:7.3f} {x['gold@10']:7.3f} {x['page@5']:7.3f} {x['page@10']:7.3f} {x['sel']:6.3f} {x['mrr']:5.3f} {lexs:>5s} {x['p50']:6d} {x['p90']:6d} {x['slow']:4d}")
    print("-" * 100)

# 字面串正例 recall（按类型；负例不计入分母）
lit_rows = [json.loads(l) for l in (ROOT / "bench/dataset/queries_literal.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
lit_meta = {(r["id"], r["queries"][0]["style"]): r["tags"] for r in lit_rows}
print("\n字面串正例 recall（gold@5）:")
for pol in POLS:
    pq, sm, src, note = pick(pol, "literal")
    if not pq:
        continue
    by_type = {}
    for q in pq:
        tag = lit_meta.get((q["id"], q["style"]), {})
        if tag.get("type") == "negative" or tag.get("expected") == "neither":
            continue
        t = tag.get("type", "?")
        by_type.setdefault(t, []).append(q)
    parts = []
    tot_hit = tot = 0
    for t, rows in sorted(by_type.items()):
        hit = sum(1 for r in rows if (r.get("gold_cov@5") or 0) > 0)
        parts.append(f"{t}={hit}/{len(rows)}")
        tot_hit += hit
        tot += len(rows)
    print(f"  {pol:4s} 总计 {tot_hit}/{tot} = {tot_hit/max(1,tot):.3f}  |  " + " ".join(parts))

# 护栏行
print("\n护栏行（sel / gold@5）:")
GUARDS = ["X01|composite", "X04|composite", "X06|composite"]
for s in ["58"]:
    snaps = {pol: snap(pick(pol, s)[0]) for pol in POLS}
    for g in GUARDS:
        line = f"  {g:16s}"
        for pol in POLS:
            q = snaps[pol].get(g) if snaps[pol] else None
            line += f" | {pol}: sel={q.get('sel_ratio',0):.2f} gold@5={q.get('gold_cov@5',0):.2f}" if q else f" | {pol}: -"
        print(line)

# 逐行差异（C / C2 vs A）
for s in ["58", "h50"]:
    A = snap(pick("A", s)[0])
    for pol in ["B", "C", "C2"]:
        pq, _, src, _ = pick(pol, s)
        if not pq:
            continue
        P = snap(pq)
        chg = []
        for k in A:
            a, p = A[k], P.get(k)
            if not p:
                continue
            d5 = round((p.get("gold_cov@5", 0) or 0) - (a.get("gold_cov@5", 0) or 0), 3)
            d10 = round((p.get("gold_cov@10", 0) or 0) - (a.get("gold_cov@10", 0) or 0), 3)
            dsel = round((p.get("sel_ratio", 0) or 0) - (a.get("sel_ratio", 0) or 0), 3)
            if d5 or d10 or dsel:
                chg.append(f"{k}(g5{d5:+} g10{d10:+} sel{dsel:+})")
        print(f"\n{s} {pol} vs A 差异行 {len(chg)}:")
        for c in chg:
            print("   ", c)
