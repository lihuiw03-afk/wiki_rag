# -*- coding: utf-8 -*-
"""Process-level load test: exactly how the Hermes skill calls RAG.

Each request spawns:
  <venv>/python.exe scripts/rag_ask.py "<query>" --profile empN --json
i.e. one Python process per employee question, like the terminal tool in a gateway turn.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"


def health():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8181/health", timeout=5) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def jobs(limit):
    rows = [json.loads(l) for l in (ROOT / "bench/dataset/queries.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    flat = [(r["id"], q["text"]) for r in rows for q in r["queries"]]
    step = max(1, len(flat) // limit)
    return [flat[i] for i in range(0, len(flat), step)][:limit]


def one(item, idx, timeout):
    cid, query = item
    t = time.perf_counter()
    try:
        p = subprocess.run(
            [str(PY), str(ROOT / "scripts/rag_ask.py"), query, "--profile", f"emp{idx % 10}", "--json"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=timeout,
        )
        ms = (time.perf_counter() - t) * 1000
        s = p.stdout
        d = json.loads(s[s.find("{"):]) if "{" in s else {}
        return {"id": cid, "ok": p.returncode == 0 and bool(d), "wall_ms": round(ms, 1),
                "rc": p.returncode, "mode": d.get("mode"), "route": d.get("route"),
                "elapsed_ms": d.get("elapsed_ms"), "context_chars": len(d.get("context") or ""),
                "err": (p.stderr or "")[-160:] if p.returncode else None}
    except subprocess.TimeoutExpired:
        return {"id": cid, "ok": False, "wall_ms": round((time.perf_counter() - t) * 1000, 1),
                "err": f"timeout>{timeout}s"}
    except Exception as e:  # noqa: BLE001
        return {"id": cid, "ok": False, "wall_ms": round((time.perf_counter() - t) * 1000, 1),
                "err": f"{type(e).__name__}: {e}"}


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round((p / 100) * (len(v) - 1))))] if v else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--timeout", type=int, default=180)
    args = ap.parse_args()
    items = jobs(args.limit)
    print("health before:", health(), flush=True)
    print(f"process-level c={args.concurrency} n={len(items)}", flush=True)

    t0 = time.perf_counter()
    out = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [ex.submit(one, it, i, args.timeout) for i, it in enumerate(items)]
        for fu in as_completed(futs):
            out.append(fu.result())
    wall = time.perf_counter() - t0
    ok = [r for r in out if r["ok"]]
    lat = [r["wall_ms"] for r in out]
    summary = {
        "concurrency": args.concurrency, "n": len(out), "ok": len(ok),
        "wall_s": round(wall, 2), "throughput_rps": round(len(out) / wall, 2),
        "p50_ms": round(pct(lat, 50) or 0, 1), "p95_ms": round(pct(lat, 95) or 0, 1), "max_ms": round(max(lat), 1),
        "modes": {m: sum(1 for r in ok if r.get("mode") == m) for m in set(r.get("mode") for r in ok)},
        "errors": [r.get("err") for r in out if not r["ok"]][:5],
        "health_after": health(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    path = ROOT / f"bench/results/load_process_c{args.concurrency}_n{len(out)}.json"
    path.write_text(json.dumps({"summary": summary, "records": out}, ensure_ascii=False, indent=1),
                    encoding="utf-8", newline="\n")
    print("saved:", path.name, flush=True)


if __name__ == "__main__":
    main()
