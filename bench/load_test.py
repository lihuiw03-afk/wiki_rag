# -*- coding: utf-8 -*-
"""10-employee concurrency load test for the current gateway path.

Models the deployed path: each request constructs CompanyKnowledgeGateway and calls
ask(route="company-rag"), i.e. no shared service process, no query cache.

Usage:
  python bench/load_test.py --concurrency 1 --limit 20 --repeat 1
  python bench/load_test.py --concurrency 5 --limit 20 --repeat 2
  python bench/load_test.py --concurrency 10 --limit 20 --repeat 2
  python bench/load_test.py --concurrency 10 --limit 10 --repeat 1 --answer

Outputs: bench/results/load_test_c{C}_n{N}_r{R}[_answer].json
"""
import argparse
import json
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from rag_gateway.gateway import CompanyKnowledgeGateway  # noqa: E402


def qmd_health() -> dict:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8181/health", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def load_jobs(limit: int):
    rows = [json.loads(l) for l in (ROOT / "bench/dataset/queries.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    jobs = [(r["id"], q["text"], q["style"]) for r in rows for q in r["queries"]]
    # spread across the set rather than taking the first N (avoid one topic)
    step = max(1, len(jobs) // limit)
    return [jobs[i] for i in range(0, len(jobs), step)][:limit]


def one(job, mode: str, context_policy: str, answer: bool):
    cid, query, style = job
    started = time.perf_counter()
    rec = {"id": cid, "style": style, "query": query, "ok": False}
    try:
        gw = CompanyKnowledgeGateway(ROOT)
        r = gw.ask(query, profile="wu01", route="company-rag", mode=mode, context_policy=context_policy)
        rec.update({
            "ok": True,
            "route": r.get("route"),
            "mode": r.get("mode"),
            "elapsed_ms": r.get("elapsed_ms"),
            "wall_ms": round((time.perf_counter() - started) * 1000, 1),
            "rerank_mode": r.get("rerank_mode"),
            "context_chars": len(r.get("context") or ""),
            "selected": len(r.get("selected") or []),
        })
        if answer:
            from judge import _gen_answer
            a0 = time.perf_counter()
            text, usage = _gen_answer(r.get("context") or "", query)
            rec.update({
                "answer_ok": True,
                "answer_ms": round((time.perf_counter() - a0) * 1000, 1),
                "answer_chars": len(text or ""),
                "answer_usage": usage,
            })
        rec["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    except Exception as e:  # noqa: BLE001
        rec.update({"error": f"{type(e).__name__}: {str(e)[:200]}",
                    "wall_ms": round((time.perf_counter() - started) * 1000, 1)})
    return rec


def pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(len(vals) - 1, max(0, int(round((p / 100) * (len(vals) - 1)))))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--mode", default="auto")
    ap.add_argument("--context-policy", default="auto")
    ap.add_argument("--answer", action="store_true")
    ap.add_argument("--warmup", type=int, default=2)
    args = ap.parse_args()

    jobs = load_jobs(args.limit) * args.repeat
    print(f"jobs={len(jobs)} concurrency={args.concurrency} answer={args.answer}", flush=True)
    print("qmd before:", qmd_health(), flush=True)

    for j in jobs[: args.warmup]:
        one(j, args.mode, args.context_policy, False)
    print("warmup done", flush=True)

    started = time.perf_counter()
    recs = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(one, j, args.mode, args.context_policy, args.answer) for j in jobs]
        for fu in as_completed(futs):
            try:
                recs.append(fu.result())
            except Exception as e:  # noqa: BLE001
                recs.append({"ok": False, "error": f"future: {type(e).__name__}: {e}"})
    wall = time.perf_counter() - started

    ok = [r for r in recs if r.get("ok")]
    err = [r for r in recs if not r.get("ok")]
    lat = [r["wall_ms"] for r in ok]
    ret = [r["elapsed_ms"] for r in ok if r.get("elapsed_ms") is not None]
    ans = [r["answer_ms"] for r in ok if r.get("answer_ms") is not None]
    fallback = [r for r in ok if r.get("rerank_mode") in ("local-fallback", "rrf-fallback")]
    summary = {
        "concurrency": args.concurrency,
        "jobs": len(jobs),
        "ok": len(ok),
        "errors": len(err),
        "error_rate": round(len(err) / max(1, len(recs)), 4),
        "wall_s": round(wall, 3),
        "throughput_rps": round(len(recs) / wall, 3) if wall else None,
        "p50_ms": pct(lat, 50),
        "p90_ms": pct(lat, 90),
        "p95_ms": pct(lat, 95),
        "p99_ms": pct(lat, 99),
        "retrieval_p50_ms": pct(ret, 50),
        "retrieval_p95_ms": pct(ret, 95),
        "answer_p50_ms": pct(ans, 50),
        "answer_p95_ms": pct(ans, 95),
        "e2e_p50_ms": pct([r.get("total_ms") for r in ok], 50),
        "e2e_p95_ms": pct([r.get("total_ms") for r in ok], 95),
        "e2e_p99_ms": pct([r.get("total_ms") for r in ok], 99),
        "fallback": len(fallback),
        "context_chars_mean": round(statistics.mean([r["context_chars"] for r in ok]), 1) if ok else None,
        "errors_sample": [r.get("error") for r in err[:5]],
        "qmd_after": qmd_health(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    suffix = "_answer" if args.answer else ""
    out = ROOT / f"bench/results/load_test_c{args.concurrency}_n{len(jobs)}_r{args.repeat}{suffix}.json"
    out.write_text(json.dumps({"summary": summary, "records": recs}, ensure_ascii=False, indent=1),
                   encoding="utf-8", newline="\n")
    print("saved:", out.name, flush=True)


if __name__ == "__main__":
    main()
