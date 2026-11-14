# -*- coding: utf-8 -*-
"""lex_recheck_20261012.py — 词法腿复测（当前栈）：vec_only vs lex_on + 字面串探针

方法：运行时把 `rag_gateway.search` 模块的 `build_typed_searches` 绑定换成剥除 lex 腿的
版本（进程内生效，零生产代码改动）；对照 = 今日 lex_on 基线 exp_e0_control_r4 / exp_h50_control_r3。
产物：bench/results/lex_recheck_20261012.json（分阶段增量落盘）
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


from run_experiments import PolicyRAG, metrics_from  # noqa: E402

rag = PolicyRAG(ROOT)  # control 基座 = 生产现状
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


def snap(a, keys=("page_cov@5", "page_cov@10", "gold_cov@5", "gold_cov@10", "sel_ratio")):
    return {k: a.get(k) for k in keys}


def save(out):
    (ROOT / "bench/results/lex_recheck_20261012.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


OUT = {"meta": {"date": "2026-10-12", "mode": "vec_only (lex leg stripped, runtime injection)",
                "baselines": ["exp_e0_control_r4.json", "exp_h50_control_r3.json"]}}

rows58 = load_rows("bench/dataset/queries.jsonl")
rows50 = load_rows("bench/dataset/queries_hard50.jsonl")

# 阶段 1：58 集 vec_only
S.build_typed_searches = _vec_only
pq58 = per_query(rows58)
b58, R4 = base_means("exp_e0_control_r4.json")
print("58 lex_on   :", json.dumps(b58, ensure_ascii=False))
print("58 vec_only :", json.dumps(means(pq58), ensure_ascii=False))
d58 = []
for x in pq58:
    a = R4[(x["id"], x["style"])]
    d58.append({"key": f'{x["id"]}|{x["style"]}', "lex_on": snap(a), "vec_only": snap(x)})
diff58 = [d for d in d58 if d["lex_on"] != d["vec_only"]]
print(f"58 差异行 {len(diff58)}:")
for d in diff58:
    print("  ", d["key"], d["lex_on"], "->", d["vec_only"])
OUT["58"] = {"lex_on_mean": b58, "vec_only_mean": means(pq58), "diff_rows": d58}
save(OUT)

# 阶段 2：困难集 vec_only
pq50 = per_query(rows50)
b50, R3 = base_means("exp_h50_control_r3.json")
print("h50 lex_on  :", json.dumps(b50, ensure_ascii=False))
print("h50 vec_only:", json.dumps(means(pq50), ensure_ascii=False))
d50 = []
for x in pq50:
    a = R3[(x["id"], x["style"])]
    d50.append({"key": f'{x["id"]}|{x["style"]}', "lex_on": snap(a), "vec_only": snap(x)})
diff50 = [d for d in d50 if d["lex_on"] != d["vec_only"]]
print(f"h50 差异行 {len(diff50)}:")
for d in diff50:
    print("  ", d["key"], d["lex_on"], "->", d["vec_only"])
OUT["h50"] = {"lex_on_mean": b50, "vec_only_mean": means(pq50), "diff_rows": d50}
save(OUT)


# 阶段 3：字面串探针（两模式各跑一遍）
def probe(label, qtext, checks):
    res = rag.search(qtext, mode="auto", profile="wu01")
    ret = res.get("retrieved") or []
    blob = json.dumps(ret, ensure_ascii=False)
    line = f'[{label:8s}] "{qtext}" -> retrieved={len(ret)} selected={len(res.get("selected") or [])}'
    for c in checks:
        line += f" | 含'{c}': {c in blob}"
    print(line)
    for h in ret[:4]:
        rp = (h.get("page_relpath") or h.get("file") or "")[-64:]
        print(f"      - {h.get('collection','')} {rp} score={round(h.get('score') or 0, 3)}")
    return {"label": label, "query": qtext, "n_retrieved": len(ret),
            "checks": {c: (c in blob) for c in checks}}


probes = [
    ("#U0001#U0002#U0003#U0004", ["U0001"]),
    ("#U0005#U0006#U0007#U0008", ["U0005"]),
    ("20260618_demo", ["demo"]),
    ("示例代号", ["示例代号"]),
    ("#U0009", ["U0009"]),
    ("U0009", ["U0009"]),
]
OUT["probes"] = {}
OUT["probes"][f"vec_only"] = [probe("vec_only", q, c) for q, c in probes]
S.build_typed_searches = _orig  # 恢复 lex 腿
OUT["probes"]["lex_on"] = [probe("lex_on", q, c) for q, c in probes]
save(OUT)
print("DONE")
