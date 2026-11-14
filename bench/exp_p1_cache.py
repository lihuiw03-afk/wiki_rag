# -*- coding: utf-8 -*-
"""Bench-only P1 cache experiment: exact-query cache + single-flight under 10 employees.

Workload: 10 profiles x 10 questions; 50% are repeated FAQ questions, 50% unique.
No production code changes; no qwen3.8 calls (retrieval only).
"""
import argparse
import json
import random
import statistics
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from rag_gateway.search import CompanyRAG  # noqa: E402


def load_queries():
    rows = [json.loads(l) for l in (ROOT / "bench/dataset/queries.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return [q["text"] for r in rows for q in r["queries"]]


def make_workload():
    qs = load_queries()
    random.seed(42)
    faq = qs[:10]
    unique = qs[10:60]
    items = []
    for i in range(10):
        for j in range(10):
            q = random.choice(faq) if j < 5 else unique[(i * 5 + j) % len(unique)]
            items.append((f"emp{i}", q))
    random.shuffle(items)
    return items


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(round((p / 100) * (len(v) - 1))))] if v else None


def run(use_cache: bool):
    items = make_workload()
    local = threading.local()
    qmd_calls = [0]
    qmd_lock = threading.Lock()
    cache = {}
    key_locks = defaultdict(threading.Lock)
    cache_lock = threading.Lock()
    stats = {"hits": 0, "misses": 0, "coalesced": 0}

    def get_rag():
        rag = getattr(local, "rag", None)
        if rag is None:
            rag = CompanyRAG(ROOT)
            orig = rag.qmd.query

            def wrapped(**kwargs):
                with qmd_lock:
                    qmd_calls[0] += 1
                return orig(**kwargs)

            rag.qmd.query = wrapped
            local.rag = rag
        return rag

    def task(item):
        profile, query = item
        key = (query.strip(), "auto", "auto")
        t = time.perf_counter()
        if use_cache:
            with cache_lock:
                if key in cache:
                    stats["hits"] += 1
                    return (time.perf_counter() - t) * 1000, "hit"
            lk = key_locks[key]
            with lk:
                with cache_lock:
                    if key in cache:
                        stats["coalesced"] += 1
                        return (time.perf_counter() - t) * 1000, "coalesced"
                res = get_rag().search(query, mode="auto", profile=profile)
                with cache_lock:
                    cache[key] = res
                    stats["misses"] += 1
                return (time.perf_counter() - t) * 1000, "miss"
        res = get_rag().search(query, mode="auto", profile=profile)
        return (time.perf_counter() - t) * 1000, "miss"

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=10) as ex:
        out = list(ex.map(task, items))
    wall = time.perf_counter() - t0
    lat = [x[0] for x in out]
    summary = {
        "use_cache": use_cache, "n": len(out), "wall_s": round(wall, 2),
        "throughput_rps": round(len(out) / wall, 2),
        "p50_ms": round(pct(lat, 50) or 0, 1), "p95_ms": round(pct(lat, 95) or 0, 1),
        "qmd_calls": qmd_calls[0], "qmd_calls_per_req": round(qmd_calls[0] / len(out), 2),
        "hit_rate": round((stats["hits"] + stats["coalesced"]) / len(out), 3),
        "hits": stats["hits"], "coalesced": stats["coalesced"], "misses": stats["misses"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    out_path = ROOT / f"bench/results/exp_p1_cache_{'on' if use_cache else 'off'}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=["off", "on", "both"], default="both")
    args = ap.parse_args()
    arms = ["off", "on"] if args.arm == "both" else [args.arm]
    for arm in arms:
        run(arm == "on")
