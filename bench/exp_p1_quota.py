# -*- coding: utf-8 -*-
"""Bench-only P1 quota experiment: per-profile cap vs no cap.

Starts a second RAG service on 8191 with a per-profile semaphore (cap=2), then sends
a skewed 10-request burst (A x6, B/C/D/E x1) to 8190 (production, no per-profile cap)
and 8191 (cap=2). Retrieval only; no qwen3.8 calls.
"""
import json
import statistics
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from rag_gateway.service import RagService, ServiceBusy, make_handler  # noqa: E402


class QuotaService(RagService):
    def __init__(self, root, cap=2, wait=30):
        super().__init__(root)
        self.cap = cap
        self.wait = wait
        self._sems = {}
        self._sem_lock = threading.Lock()

    def _sem(self, profile):
        with self._sem_lock:
            sem = self._sems.get(profile)
            if sem is None:
                sem = threading.BoundedSemaphore(self.cap)
                self._sems[profile] = sem
            return sem

    def ask(self, payload):
        profile = str(payload.get("profile") or "anon")
        sem = self._sem(profile)
        if not sem.acquire(timeout=self.wait):
            raise ServiceBusy(f"profile {profile} quota exceeded")
        try:
            return super().ask(payload)
        finally:
            sem.release()


def start_quota():
    svc = QuotaService(ROOT)
    httpd = ThreadingHTTPServer(("127.0.0.1", 8191), make_handler(svc))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def post(url, payload, timeout=180):
    t = time.perf_counter()
    req = urllib.request.Request(url + "/ask", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
            return {"profile": payload["profile"], "ok": True, "status": r.status,
                    "wall_ms": round((time.perf_counter() - t) * 1000, 1), "mode": body.get("mode")}
    except Exception as exc:
        return {"profile": payload["profile"], "ok": False,
                "wall_ms": round((time.perf_counter() - t) * 1000, 1),
                "error": f"{type(exc).__name__}: {str(exc)[:120]}"}


def workload():
    rows = [json.loads(l) for l in (ROOT / "bench/dataset/queries.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    qs = [q["text"] for r in rows for q in r["queries"]][:10]
    items = [("A", qs[i]) for i in range(6)] + [(p, qs[6 + i]) for i, p in enumerate(["B", "C", "D", "E"])]
    return [{"query": q, "profile": p, "route": "company-rag", "mode": "auto",
             "context_policy": "auto", "attachment": None, "hyde": None} for p, q in items]


def run(url, label):
    items = workload()
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=10) as ex:
        out = list(ex.map(lambda p: post(url, p), items))
    wall = time.perf_counter() - t0
    lat = [x["wall_ms"] for x in out]
    per_profile = {}
    for x in out:
        per_profile.setdefault(x["profile"], []).append(x["wall_ms"])
    summary = {
        "label": label, "n": len(out), "ok": sum(1 for x in out if x["ok"]),
        "wall_s": round(wall, 2), "p50_ms": round(statistics.median(lat), 1),
        "p95_ms": round(sorted(lat)[int(len(lat) * 0.95) - 1], 1),
        "per_profile_mean_ms": {p: round(statistics.mean(v), 1) for p, v in sorted(per_profile.items())},
        "per_profile_max_ms": {p: round(max(v), 1) for p, v in sorted(per_profile.items())},
        "errors": [x.get("error") for x in out if not x["ok"]],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    httpd = start_quota()
    print("quota service 8191 ready", flush=True)
    results = []
    # production sidecar first (no per-profile cap), then quota service
    results.append(run("http://127.0.0.1:8190", "no_quota_8190"))
    results.append(run("http://127.0.0.1:8191", "quota2_8191"))
    out = ROOT / "bench/results/exp_p1_quota.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print("saved", out.name)
    httpd.shutdown()
