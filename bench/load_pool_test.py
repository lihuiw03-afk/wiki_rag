import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from rag_gateway.search import CompanyRAG  # noqa: E402


def pct(vals, p):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round((p / 100) * (len(vals) - 1))))] if vals else None


rows = [json.loads(l) for l in (ROOT / "bench/dataset/queries.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
jobs = [(r["id"], q["text"]) for r in rows for q in r["queries"]]
step = max(1, len(jobs) // 20)
jobs = [jobs[i] for i in range(0, len(jobs), step)][:20] * 2

print("warming pool...", flush=True)
pool = [CompanyRAG(ROOT) for _ in range(10)]


def task(i):
    rag = pool[i % len(pool)]
    t = time.perf_counter()
    r = rag.search(jobs[i][1], mode="auto", profile="wu01")
    return (time.perf_counter() - t) * 1000, r.get("mode"), r.get("rerank_mode")


started = time.perf_counter()
with ThreadPoolExecutor(max_workers=10) as ex:
    out = list(ex.map(task, range(len(jobs))))
wall = time.perf_counter() - started
lat = [x[0] for x in out]
summary = {
    "n": len(out), "wall_s": round(wall, 3), "throughput_rps": round(len(out) / wall, 3),
    "p50_ms": round(pct(lat, 50), 1), "p95_ms": round(pct(lat, 95), 1), "max_ms": round(max(lat), 1),
    "modes": {m: sum(1 for x in out if x[1] == m) for m in set(x[1] for x in out)},
    "fallbacks": sum(1 for x in out if x[2] in ("local-fallback", "rrf-fallback")),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
out_path = ROOT / "bench/results/load_pool_c10_n40.json"
out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
print("saved:", out_path.name)
