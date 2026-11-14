import json
import sys
from pathlib import Path

ROOT = Path(r"C:/Users/HP/Projects/cwr24")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
from rag_gateway.search import CompanyRAG  # noqa: E402
from exp_qmd_merge import make_rag  # noqa: E402
from run_experiments import metrics_from  # noqa: E402

case = next(json.loads(l) for l in (ROOT / "bench/dataset/queries_hard50.jsonl").read_text(encoding="utf-8").splitlines() if l.strip() and json.loads(l)["id"] == "H48")
q = case["queries"][0]["text"]
gold = set(case["target_raws"])


def run(label, variant="baseline", min_score=None):
    rag = make_rag(variant)
    if min_score is not None:
        rag.config["structure"]["restricted_min_score"] = min_score
    calls = []
    orig = rag.qmd.query

    def wrap(**kwargs):
        out = orig(**kwargs)
        calls.append((len(out), kwargs.get("collections"), kwargs.get("limit")))
        return out

    rag.qmd.query = wrap
    res = rag.search(q, mode="auto", profile="wu01")
    retr = res.get("retrieved") or []
    rank = next((i + 1 for i, h in enumerate(retr)
                 if (rag.manifest["nodes"].get(h.get("node_id"), {}) or {}).get("raw_path") in gold), None)
    m = metrics_from(res, set(case["target_pages"]), gold, rag.manifest["nodes"])
    print(f"{label:14s} qmd={len(calls)} calls={calls} gold_rank={rank} "
          f"mrr={m.get('mrr'):.4f} gold@5={m.get('gold_cov@5'):.4f} gold@10={m.get('gold_cov@10'):.4f} sel={m.get('sel_ratio'):.4f} chars={len(res.get('context') or '')}")


run("baseline", "baseline")
run("merged_005", "merged")
run("merged_fixed", "merged_fixed")
run("merged_localmax", "merged_localmax")
