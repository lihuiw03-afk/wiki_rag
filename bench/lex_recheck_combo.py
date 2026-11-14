# -*- coding: utf-8 -*-
"""lex_recheck_combo.py — E6-A × vec_only 组合复测（两个待批准项的交互面）

E6-A 与"剥词法"都在修 E06/H10（一在装入层、一在检索层）——组合是双赢还是重叠？
方法：PolicyRAG(budget_policy="A") + 运行时剥 lex 腿；对照 exp_e6_A_r4 / exp_h50_A_r4。
追加写入 bench/results/lex_recheck_20261012.json 的 combo_A_vec_only 段。
"""
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\HP\Projects\cwr24")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
import os  # noqa: E402

os.chdir(ROOT)

import rag_gateway.search as S  # noqa: E402

_orig = S.build_typed_searches


def _vec_only(q, use_vector, lex_terms, *a, **kw):
    out = _orig(q, use_vector, lex_terms, *a, **kw)
    out2 = [s for s in out if s.get("type") != "lex"]
    return out2 if out2 else out


S.build_typed_searches = _vec_only

from run_experiments import PolicyRAG, metrics_from  # noqa: E402

rag = PolicyRAG(ROOT)
rag.budget_policy = "A"
nodes = rag.manifest["nodes"]


def load_rows(p):
    return [json.loads(l) for l in (ROOT / p).read_text(encoding="utf-8").splitlines() if l.strip()]


def per_query(rows):
    out = []
    for r in rows:
        for q in r["queries"]:
            res = rag.search(q["text"], mode="auto", profile="wu01")
            m = metrics_from(res, set(r["target_pages"]), set(r["target_raws"]), nodes)
            m.update({"id": r["id"], "style": q["style"]})
            out.append(m)
    return out


def means(pq):
    def mean(k):
        return statistics.mean([x.get(k, 0) for x in pq])
    return {k: round(mean(k), 4) for k in ("page_cov@5", "page_cov@10", "gold_cov@5", "gold_cov@10", "sel_ratio")}


def base_means(f):
    d = json.loads((ROOT / "bench/results" / f).read_text(encoding="utf-8"))
    return means(d["per_query"]), {(q["id"], q["style"]): q for q in d["per_query"]}


def snap(a):
    return {k: a.get(k) for k in ("page_cov@5", "page_cov@10", "gold_cov@5", "gold_cov@10", "sel_ratio")}


rows58 = load_rows("bench/dataset/queries.jsonl")
rows50 = load_rows("bench/dataset/queries_hard50.jsonl")

OUT = json.loads((ROOT / "bench/results/lex_recheck_20261012.json").read_text(encoding="utf-8"))

for tag, rows, base_f in (("58", rows58, "exp_e6_A_r4.json"), ("h50", rows50, "exp_h50_A_r4.json")):
    pq = per_query(rows)
    b, B = base_means(base_f)
    print(f"{tag} E6-A(lex_on) :", json.dumps(b, ensure_ascii=False))
    print(f"{tag} E6-A+vec_only:", json.dumps(means(pq), ensure_ascii=False))
    diffs = []
    for x in pq:
        a = B[(x["id"], x["style"])]
        if snap(a) != snap(x):
            diffs.append({"key": f'{x["id"]}|{x["style"]}', "A_lex_on": snap(a), "A_vec_only": snap(x)})
    print(f"{tag} 差异行 {len(diffs)}:")
    for d in diffs:
        print("  ", d["key"], d["A_lex_on"], "->", d["A_vec_only"])
    OUT.setdefault("combo_A_vec_only", {})[tag] = {"A_lex_on_mean": b, "A_vec_only_mean": means(pq), "diff_rows": diffs}

(ROOT / "bench/results/lex_recheck_20261012.json").write_text(
    json.dumps(OUT, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
print("DONE")
