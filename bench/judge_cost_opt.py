# -*- coding: utf-8 -*-
"""58-case answer-quality check: production config vs cost-optimized config.

A (production): candidate balanced 30 / deep 40, balanced context 18000
B (cost-opt):  candidate balanced 20 / deep 16, balanced context 12000

Same queries, same answer/judge model, refs from bench/results/judge/refs.
If both contexts are byte-identical, the case is counted as a tie without API calls.
"""
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from rag_gateway.search import CompanyRAG  # noqa: E402
from judge import _gen_answer, judge_call  # noqa: E402

OUT = ROOT / "bench/results/judge/cost_opt"
OUT.mkdir(parents=True, exist_ok=True)


def build(bal: int, deep: int, cap: int) -> CompanyRAG:
    rag = CompanyRAG(ROOT)
    rag.config["retrieval"]["modes"]["balanced"]["candidate_limit"] = bal
    rag.config["retrieval"]["modes"]["deep"]["candidate_limit"] = deep
    rag.config["context"]["policies"]["balanced"]["max_chars"] = cap
    return rag


base = build(30, 40, 18000)
opt = build(20, 16, 12000)

rows = [json.loads(l) for l in (ROOT / "bench/dataset/queries.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
jobs = [(r, q) for r in rows for q in r["queries"]]

prepared = []
for r, q in jobs:
    key = f"{r['id']}_{q['style']}"
    if (OUT / f"{key}.json").exists():
        continue
    rb = base.search(q["text"], mode="auto", profile="wu01", context_policy="balanced")
    ro = opt.search(q["text"], mode="auto", profile="wu01", context_policy="balanced")
    cb = rb.get("context") or ""
    co = ro.get("context") or ""
    prepared.append((r, q, cb, co, rb.get("context_stats") or {}, ro.get("context_stats") or {}))
limit = int(os.environ.get("MAX_CASES", "1000"))
prepared = prepared[:limit]
print(f"prepared {len(prepared)} cases (limit={limit})", flush=True)


def work(item):
    r, q, cb, co, sb, so = item
    key = f"{r['id']}_{q['style']}"
    if cb == co:
        out = {"id": r["id"], "style": q["style"], "question": q["text"], "identical_context": True,
               "base": {"chars": sb.get("chars"), "ans": None, "judge": None},
               "opt": {"chars": so.get("chars"), "ans": None, "judge": None}}
        (OUT / f"{key}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        return f"{key} identical"
    ref = json.loads((ROOT / "bench/results/judge/refs" / f"{key}.json").read_text(encoding="utf-8"))["ref"]
    ab, _ = _gen_answer(cb, q["text"])
    ao, _ = _gen_answer(co, q["text"])
    jb = judge_call(q["text"], ref, ab, cb)
    jo = judge_call(q["text"], ref, ao, co)
    out = {"id": r["id"], "style": q["style"], "question": q["text"], "identical_context": False,
           "base": {"chars": sb.get("chars"), "ans": ab, "judge": jb},
           "opt": {"chars": so.get("chars"), "ans": ao, "judge": jo}}
    (OUT / f"{key}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return f"{key} base {jb.get('faithfulness')}/{jb.get('correctness')} vs opt {jo.get('faithfulness')}/{jo.get('correctness')}"


with ThreadPoolExecutor(max_workers=3) as ex:
    futs = [ex.submit(work, it) for it in prepared]
    for fu in as_completed(futs):
        try:
            print("[ok]", fu.result(), flush=True)
        except Exception as e:  # noqa: BLE001
            print("[ERR]", str(e)[:180], flush=True)

rows_out = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(OUT.glob("*.json"))]
print(f"\n=== summary n={len(rows_out)} ===")
for side in ("base", "opt"):
    faith = [d[side]["judge"].get("faithfulness") for d in rows_out
             if d[side].get("judge") and d[side]["judge"].get("faithfulness") is not None]
    corr = [d[side]["judge"].get("correctness") for d in rows_out
            if d[side].get("judge") and d[side]["judge"].get("correctness") is not None]
    print(side, "n_judged=", len(faith), "faith=", round(statistics.mean(faith), 3) if faith else None,
          "corr=", round(statistics.mean(corr), 3) if corr else None)
for metric in ("faithfulness", "correctness"):
    w = l = t = 0
    diffs = []
    for d in rows_out:
        jb, jo = d["base"].get("judge"), d["opt"].get("judge")
        if not jb or not jo:
            t += 1
            continue
        a, b = jb.get(metric), jo.get(metric)
        if a is None or b is None:
            t += 1
            continue
        delta = b - a
        if delta > 0: w += 1
        elif delta < 0: l += 1
        else: t += 1
        if abs(delta) >= 2:
            diffs.append((d["id"], d["style"], a, b))
    print(metric, "opt wins", w, "losses", l, "ties", t, "| |Δ|>=2:", diffs)
