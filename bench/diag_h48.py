import json
import sys
import time
from pathlib import Path

ROOT = Path(r"C:/Users/HP/Projects/cwr24")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from rag_gateway.search import CompanyRAG  # noqa: E402
from exp_qmd_merge import MergedRAG  # noqa: E402
from run_experiments import metrics_from  # noqa: E402

rows = [json.loads(l) for l in (ROOT / "bench/dataset/queries_hard50.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
case = next(r for r in rows if r["id"] == "H48")
q = case["queries"][0]["text"]
gold_raw = set(case["target_raws"])


def run(rag, label):
    calls = []
    orig = rag.qmd.query

    def wrap(**kwargs):
        t = time.perf_counter()
        out = orig(**kwargs)
        calls.append({
            "ms": round((time.perf_counter() - t) * 1000),
            "collections": kwargs.get("collections"),
            "limit": kwargs.get("limit"),
            "candidate_limit": kwargs.get("candidate_limit"),
            "n_searches": len(kwargs.get("searches") or []),
            "returned": len(out),
            "top_score": round(out[0].score, 4) if out else None,
        })
        return out

    rag.qmd.query = wrap
    res = rag.search(q, mode="auto", profile="wu01")
    print("====", label, "====")
    print("qmd:", json.dumps(calls, ensure_ascii=False))
    print("metrics:", {k: round(v, 4) for k, v in metrics_from(res, set(case["target_pages"]), gold_raw, rag.manifest["nodes"]).items() if k in ("page_cov@5","page_cov@10","gold_cov@5","gold_cov@10","sel_ratio","mrr")})
    print("context chars:", len(res.get("context") or ""), "selected:", len(res.get("selected") or []))
    for i, h in enumerate(res.get("retrieved") or [], 1):
        n = rag.manifest["nodes"].get(h.get("node_id"), {})
        mark = " <== GOLD" if n.get("raw_path") in gold_raw else ""
        print(f"{i:2d} score={h.get('score',0):.4f} reason={h.get('reason')} type={h.get('collection')} raw={Path(str(n.get('raw_path') or '')).name[:50]} page={Path(str(n.get('page_relpath') or '')).name}{mark}")


run(CompanyRAG(ROOT), "baseline")
run(MergedRAG(ROOT), "merged")
