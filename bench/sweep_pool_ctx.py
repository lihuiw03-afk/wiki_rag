import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from rag_gateway.search import CompanyRAG  # noqa: E402
from run_experiments import metrics_from  # noqa: E402

SETS = {
    "58": ROOT / "bench/dataset/queries.jsonl",
    "h50": ROOT / "bench/dataset/queries_hard50.jsonl",
}

POOL_SETTINGS = {
    "pool16": {"balanced": 16, "deep": 16, "min": 0.05},
    "base": {"balanced": 30, "deep": 40, "min": 0.05},
    "ms03": {"balanced": 30, "deep": 40, "min": 0.03},
    "ms02": {"balanced": 40, "deep": 40, "min": 0.02},
    "b20d16": {"balanced": 20, "deep": 16, "min": 0.05},
    "b16d24": {"balanced": 16, "deep": 24, "min": 0.05},
    "b20d24": {"balanced": 20, "deep": 24, "min": 0.05},
    "b18d20": {"balanced": 18, "deep": 20, "min": 0.05},
}
COMBO = ["b20d16", "b16d24", "b20d24", "b18d20"]
CTX_CAPS = [9000, 12000, 15000, 18000, 24000, 32000]


def load_rows(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def run_pool(set_name: str, label: str, cfg: dict):
    rag = CompanyRAG(ROOT)
    rag.config["retrieval"]["modes"]["balanced"]["candidate_limit"] = cfg["balanced"]
    rag.config["retrieval"]["modes"]["deep"]["candidate_limit"] = cfg["deep"]
    rag.rerank_api.fetch_min_score = cfg["min"]

    calls = []
    orig = rag.rerank_api.rerank

    def wrapped(query, documents, top_n=None):
        calls.append((len(documents), sum(len(d or "") for d in documents)))
        return orig(query, documents, top_n)

    rag.rerank_api.rerank = wrapped

    per = []
    for r in load_rows(SETS[set_name]):
        for q in r["queries"]:
            before = len(calls)
            res = rag.search(q["text"], mode="auto", profile="wu01")
            new = calls[before:]
            m = metrics_from(res, set(r["target_pages"]), set(r["target_raws"]), rag.manifest["nodes"])
            st = res.get("context_stats") or {}
            m.update({
                "id": r["id"], "style": q["style"], "mode": res.get("mode"),
                "rerank_mode": res.get("rerank_mode"),
                "rerank_docs": sum(c[0] for c in new),
                "rerank_chars": sum(c[1] for c in new),
                "truncated": bool(st.get("truncated")),
                "chars": st.get("chars", 0),
                "blocks": st.get("selected_hits", 0),
                "dropped": st.get("dropped_hits", 0),
            })
            per.append(m)
            time.sleep(0.25)
    save(set_name, label, per, {"candidate_limit": cfg})
    return per


def run_ctx(set_name: str, cap: int):
    rag = CompanyRAG(ROOT)
    rag.config["context"]["policies"]["balanced"]["max_chars"] = cap
    per = []
    for r in load_rows(SETS[set_name]):
        for q in r["queries"]:
            res = rag.search(q["text"], mode="auto", profile="wu01", context_policy="balanced")
            m = metrics_from(res, set(r["target_pages"]), set(r["target_raws"]), rag.manifest["nodes"])
            m.update({"id": r["id"], "style": q["style"], "mode": res.get("mode"),
                      "truncated": bool((res.get("context_stats") or {}).get("truncated")),
                      "chars": (res.get("context_stats") or {}).get("chars", 0),
                      "dropped": (res.get("context_stats") or {}).get("dropped_hits", 0)})
            per.append(m)
            time.sleep(0.2)
    save(set_name, f"ctx{cap}", per, {"ctx_max_chars": cap})
    return per


def save(set_name, label, per, meta):
    keys = ["page_cov@5", "page_cov@10", "gold_cov@5", "gold_cov@10", "sel_ratio", "mrr",
            "chars", "blocks", "dropped", "elapsed_ms", "rerank_docs", "rerank_chars"]
    summary = {k: statistics.mean([x.get(k, 0) for x in per]) for k in keys}
    summary["truncated"] = sum(1 for x in per if x.get("truncated"))
    summary["n"] = len(per)
    summary["n_fallback"] = sum(1 for x in per if x.get("rerank_mode") in ("local-fallback", "rrf-fallback"))
    out = ROOT / f"bench/results/sweep_{set_name}_{label}.json"
    out.write_text(json.dumps({"meta": meta, "summary": summary, "per_query": per}, ensure_ascii=False, indent=1),
                   encoding="utf-8", newline="\n")
    est_tokens = summary["rerank_chars"] * 0.57
    print(f"{set_name:3s} {label:8s} n={summary['n']:2d} @5={summary['page_cov@5']:.4f} "
          f"@10={summary['page_cov@10']:.4f} sel={summary['sel_ratio']:.4f} mrr={summary['mrr']:.4f} "
          f"ctx={summary['chars']:.0f} trunc={summary['truncated']:2d} "
          f"docs={summary['rerank_docs']:.1f} rchars={summary['rerank_chars']:.0f} "
          f"tok≈{est_tokens:.0f} cost≈¥{est_tokens/1e6*0.5:.4f} "
          f"p50={statistics.median([x['elapsed_ms'] for x in per]):.0f}ms "
          f"fallback={summary['n_fallback']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=["pool58", "poolh50", "combo58", "combo50", "ctx58"])
    args = ap.parse_args()
    if args.phase == "pool58":
        for label, cfg in POOL_SETTINGS.items():
            run_pool("58", label, cfg)
    elif args.phase == "poolh50":
        for label, cfg in POOL_SETTINGS.items():
            run_pool("h50", label, cfg)
    elif args.phase == "combo58":
        for label in COMBO:
            run_pool("58", label, POOL_SETTINGS[label])
    elif args.phase == "combo50":
        for label in COMBO:
            run_pool("h50", label, POOL_SETTINGS[label])
    elif args.phase == "ctx58":
        for cap in CTX_CAPS:
            run_ctx("58", cap)
