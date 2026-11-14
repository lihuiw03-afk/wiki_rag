# -*- coding: utf-8 -*-
"""bench/gen_literal_queries.py — 字面串评测集挖掘（候选生成 + 双模式行为验证）

从 manifest 的 company-evidence 节点标题中抽取"疑似字面串"（#U/-U 转义码、日期_缩写、
大写缩写、长数字），用当前配置（双开）与 vec-only 各检索一次，判定候选类别：
  lex_only ：双开命中目标、vec-only 未命中  → 条件路由必须保住的场景（正例）
  both_hit ：两模式都命中                   → 等价场景（路由不应破坏）
  neither  ：两模式都未命中                  → 不可达（负例/诊断）
  vec_only ：仅 vec 命中（lex 破坏）         → 反例（条件化应避免的形态）

输出：bench/results/literal_candidates.json（未人工筛的候选池 + 命中证据）
用法：python bench/gen_literal_queries.py [--limit 60]
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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


from run_experiments import PolicyRAG  # noqa: E402

rag = PolicyRAG(ROOT)  # control 基座 = 当前生产
nodes = rag.manifest["nodes"]

# 每个 raw_path → 标题（取该 raw 的 evidence 节点 page_title）
raw_title = {}
for n in nodes.values():
    if n.get("collection") == "company-evidence" and n.get("raw_path"):
        raw_title.setdefault(n["raw_path"], n.get("page_title") or "")

ESC = re.compile(r"[#-]U[0-9A-Fa-f]{4}")
DATE_ABBR = re.compile(r"20\d{6}[_-][a-z]{2,4}")

# token → 出现的标题集合（用于唯一性判断；qmd lex 只对单片段 token 命中）
tok_map = {}
for rp, title in raw_title.items():
    base = Path(rp).name
    for m in ESC.finditer(base):
        tok_map.setdefault(m.group(0), set()).add(rp)
    for m in DATE_ABBR.finditer(base):
        tok_map.setdefault(m.group(0), set()).add(rp)

cands = []
for tok, raws in tok_map.items():
    raws = sorted(raws)
    cands.append({"token": tok, "kind": "esc" if len(tok) == 6 else "date_abbr",
                  "raws": raws, "uniq": len(raws) == 1})
uniq = [c for c in cands if c["uniq"]]
uniq.sort(key=lambda c: (c["kind"] != "esc", c["token"]))  # esc 优先

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=80)
args = ap.parse_args()
pool = uniq[: args.limit]
print(f"唯一 token: {len(uniq)} / 全部: {len(cands)}；评测 {len(pool)} 个（固定 mode=balanced）")


def raw_set(res, k=10):
    s = set()
    for h in (res.get("retrieved") or [])[:k]:
        n = nodes.get(h.get("node_id")) or {}
        if n.get("raw_path"):
            s.add(n["raw_path"])
    return s


def hit(res, golds):
    return 1 if (raw_set(res) & golds) else 0


out = []
for c in pool:
    tok, golds = c["token"], set(c["raws"])
    # 模式 A（双开）
    ra = rag.search(tok, mode="balanced", profile="wu01")
    ha = hit(ra, golds)
    # 模式 B（vec-only）
    S.build_typed_searches = _vec_only
    rb = rag.search(tok, mode="balanced", profile="wu01")
    S.build_typed_searches = _orig
    hb = hit(rb, golds)
    label = "lex_only" if (ha and not hb) else ("both_hit" if (ha and hb) else
                                                ("vec_only" if (hb and not ha) else "neither"))
    c.update({"A_hit": ha, "B_hit": hb, "label": label})
    out.append(c)
    print(f"{label:9s} {c['kind']:8s} {tok:10s} gold={Path(sorted(golds)[0]).name[:56]}")

summary = {}
for c in out:
    summary[c["label"]] = summary.get(c["label"], 0) + 1
print("汇总：", summary)

(ROOT / "bench/results/literal_candidates.json").write_text(
    json.dumps({"summary": summary, "candidates": out}, ensure_ascii=False, indent=1),
    encoding="utf-8", newline="\n")
print("已写 bench/results/literal_candidates.json")
