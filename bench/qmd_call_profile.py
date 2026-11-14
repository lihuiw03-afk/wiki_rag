import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from rag_gateway.search import CompanyRAG  # noqa: E402

rag = CompanyRAG(ROOT)
qmd_calls = []
rr_calls = []
orig_qmd = rag.qmd.query
orig_rr = rag.rerank_api.rerank


def qmd_wrap(**kwargs):
    t = time.perf_counter()
    hits = orig_qmd(**kwargs)
    qmd_calls.append({
        "ms": (time.perf_counter() - t) * 1000,
        "n": len(hits),
        "limit": kwargs.get("limit"),
        "candidate_limit": kwargs.get("candidate_limit"),
        "rerank": kwargs.get("rerank"),
        "collections": kwargs.get("collections"),
    })
    return hits


def rr_wrap(query, docs, top_n=None):
    t = time.perf_counter()
    out = orig_rr(query, docs, top_n)
    rr_calls.append({"ms": (time.perf_counter() - t) * 1000, "docs": len(docs)})
    return out


rag.qmd.query = qmd_wrap
rag.rerank_api.rerank = rr_wrap

rows = [json.loads(l) for l in (ROOT / "bench/dataset/queries.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
jobs = [(r["id"], q["text"]) for r in rows for q in r["queries"]]
step = max(1, len(jobs) // 20)
jobs = [jobs[i] for i in range(0, len(jobs), step)][:20]

# warmup
rag.search(jobs[0][1], mode="auto", profile="wu01")

per = []
for cid, q in jobs:
    qmd_calls.clear(); rr_calls.clear()
    t = time.perf_counter()
    res = rag.search(q, mode="auto", profile="wu01")
    total = (time.perf_counter() - t) * 1000
    per.append({
        "id": cid, "mode": res.get("mode"), "total_ms": total,
        "qmd_calls": len(qmd_calls), "qmd_ms": sum(c["ms"] for c in qmd_calls),
        "qmd_limits": [c["limit"] for c in qmd_calls],
        "qmd_rerank_flags": [c["rerank"] for c in qmd_calls],
        "rr_calls": len(rr_calls), "rr_ms": sum(c["ms"] for c in rr_calls),
    })

def s(k):
    v = [x[k] for x in per]
    return round(statistics.mean(v), 1), round(statistics.median(v), 1), round(max(v), 1)

print("n", len(per))
print("qmd_calls mean/p50/max:", s("qmd_calls"))
print("qmd_ms    mean/p50/max:", s("qmd_ms"))
print("rr_calls  mean/p50/max:", s("rr_calls"))
print("rr_ms     mean/p50/max:", s("rr_ms"))
print("total_ms  mean/p50/max:", s("total_ms"))
print("qmd call distribution:", {k: sum(1 for x in per if x["qmd_calls"] == k) for k in sorted(set(x["qmd_calls"] for x in per))})
print("limits distribution:", {str(k): sum(1 for x in per if x["qmd_limits"] == k) for k in {tuple(x["qmd_limits"]) for x in per}})
for x in per:
    if x["qmd_calls"] > 1:
        print("multi-qmd:", x)
