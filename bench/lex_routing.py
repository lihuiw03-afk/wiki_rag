# -*- coding: utf-8 -*-
"""bench/lex_routing.py — 词法腿策略原型（运行时注入 rag_gateway.search，零生产改动）

策略：
  A  当前生产（双开，lex 输入 = 词表命中词条，否则原始查询）
  B  仅向量（诊断参照）
  C  条件路由（修订版）：identifier → Lex(identifier tokens)；多实体（词表命中≥2）→ 保留原词表 lex；
      其余 → 无 lex。多实体保护有 X01 证据（control 基座上 lex 对其覆盖有贡献）。
  C2 纯词条过滤：identifier → Lex(identifier tokens)；其余 → 无 lex（不做多实体特殊）

用法：import lex_routing; lex_routing.activate("C") / deactivate()
"""
import re

import rag_gateway.search as S
from rag_gateway.router import build_typed_searches as _orig

IDENT_RXS = [
    re.compile(r"[#-]U[0-9A-Fa-f]{4}"),                      # 转义码片段
    re.compile(r"20\d{6}[_-][A-Za-z]{2,4}"),                 # 日期_缩写
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9][A-Za-z0-9_.-]{5,}"),  # 长字母数字/下划线串
]


def identifier_tokens(q: str):
    out, seen = [], set()
    for rx in IDENT_RXS:
        for m in rx.finditer(q):
            t = m.group(0)
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def _split(out):
    lex = [s for s in out if s.get("type") == "lex"]
    rest = [s for s in out if s.get("type") != "lex"]
    return lex, rest


class Policy:
    name = "A"

    def apply(self, out, query, lex_terms):
        return out

    def __call__(self, query, use_vector, lex_terms, *a, **kw):
        return self.apply(_orig(query, use_vector, lex_terms, *a, **kw), query, lex_terms)


class VecOnly(Policy):
    name = "B"

    def apply(self, out, query, lex_terms):
        _, rest = _split(out)
        return rest if rest else out


class Conditional(Policy):
    name = "C"

    def apply(self, out, query, lex_terms):
        lex, rest = _split(out)
        toks = identifier_tokens(query)
        if toks:
            return [{"type": "lex", "query": " ".join(toks)}] + rest
        matched = [t for t in lex_terms if t and t in query]
        if len(matched) >= 2:
            return out  # 多实体保护：保留原词表 lex
        return rest if rest else out


class TermFilter(Policy):
    name = "C2"

    def apply(self, out, query, lex_terms):
        lex, rest = _split(out)
        toks = identifier_tokens(query)
        if toks:
            return [{"type": "lex", "query": " ".join(toks)}] + rest
        return rest if rest else out


POLICIES = {"A": Policy(), "B": VecOnly(), "C": Conditional(), "C2": TermFilter()}


def activate(name):
    S.build_typed_searches = POLICIES[name]


def deactivate():
    S.build_typed_searches = _orig
