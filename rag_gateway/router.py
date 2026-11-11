from __future__ import annotations
import re

DEEP_PATTERNS = (
    "综合", "对比", "比较", "共同点", "差异", "跨", "所有", "分别", "归纳", "总结", "主要矛盾", "趋势", "原因", "为什么", "有哪些问题", "多份", "多个案例",
)
FAST_PATTERNS = (
    "是谁", "是什么", "全称", "简称", "哪份", "哪个文件", "日期", "什么时候", "多少", "在哪", "定义",
)

# Context selection is independent from retrieval depth. A deep question can still be
# source-focused (for example: "详细总结这份资料"), while a balanced query can require
# diversity across several sources.
DIVERSITY_CONTEXT_PATTERNS = (
    "综合", "对比", "比较", "不同", "多份", "多位", "多个", "共同点", "差异", "分别", "跨文档", "跨来源", "各份", "各位",
)
SOURCE_FOCUSED_CONTEXT_PATTERNS = (
    "这份", "该份", "这篇", "该文档", "这个文件", "该文件", "原文", "全文", "访谈中", "报告中", "文档中", "文件中",
    "ppt中", "PPT中", "逐条", "完整总结", "详细总结", "具体提到", "具体怎么说", "怎么表述", "所有细节", "全部内容",
)


def choose_mode(query: str, requested: str = "auto") -> str:
    if requested in {"fast", "balanced", "deep"}:
        return requested
    q = query.strip()
    if any(x in q for x in DEEP_PATTERNS) or len(q) > 90:
        return "deep"
    if identifier_tokens(q):
        # Identifier-shaped queries need the vector+evidence stack. fast is lex-only over
        # company-core, so a date/code fragment would be trapped with junk wiki hits and
        # never see evidence documents (measured 2026-10-12: "20260622_demo").
        return "balanced"
    if len(q) <= 36 and any(x in q for x in FAST_PATTERNS):
        return "fast"
    if re.fullmatch(r"[A-Za-z0-9_.-]{2,24}", q):
        return "fast"
    return "balanced"


def choose_context_policy(query: str, requested: str = "auto") -> str:
    """Choose how aggressively the final context should diversify across sources.

    diversity:      cross-source synthesis; intentionally caps repeated sources.
    source-focused: deep read of one document/source; allows many chunks from one source.
    balanced:       default compromise.
    """
    if requested in {"balanced", "diversity", "source-focused"}:
        return requested
    q = query.strip()
    # Cross-source language wins over generic document words such as "访谈".
    if any(x in q for x in DIVERSITY_CONTEXT_PATTERNS):
        return "diversity"
    if any(x in q for x in SOURCE_FOCUSED_CONTEXT_PATTERNS):
        return "source-focused"
    return "balanced"


def entity_mentions(query: str, entity_names: list[str] | None, min_prefix: int = 4) -> list[str]:
    """查询中出现的实体名（完整名，或 ≥min_prefix 字的名称前缀）。

    多实体回归场景：比较类问句只靠整句 embedding 时，简称/长名差
    会导致某侧实体无信号；这里补充实体独立检索。
    """
    if not entity_names:
        return []
    found: list[str] = []
    for name in sorted({str(x) for x in entity_names}, key=len, reverse=True):
        if len(name) < 2:
            continue
        if name in query:
            found.append(name)
            continue
        for length in range(min_prefix, len(name)):
            if name[:length] in query:
                found.append(name)
                break
    return found


# Identifier-shaped tokens are the only class that reliably benefits from the lex leg
# (measured 2026-10-12: escaped codes and date fragments are lex-only wins; name/title
# terms hurt natural-language queries).
_IDENTIFIER_PATTERNS = (
    re.compile(r"[#-]U[0-9A-Fa-f]{4}"),
    re.compile(r"20\d{6}[_-][A-Za-z]{2,4}"),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9][A-Za-z0-9_.-]{5,}"),
)


def identifier_tokens(query: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for rx in _IDENTIFIER_PATTERNS:
        for m in rx.finditer(query):
            tok = m.group(0)
            if tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


def build_typed_searches(query: str, use_vector: bool, lex_terms: list[str], hyde: str | None = None,
                         entity_names: list[str] | None = None,
                         lex_leg: str = "on") -> list[dict[str, str]]:
    matched = []
    for term in lex_terms:
        term = str(term)
        if len(term) >= 2 and term in query and term not in matched:
            matched.append(term)
        if len(matched) >= 8:
            break
    # qmd lex on CJK matches each whitespace-segment as a contiguous PHRASE (chars are
    # indexed per-character), and ALL segments must match — so a raw natural sentence
    # ("…是谁/哪些/了") is guaranteed to return 0 hits, which silently empties `fast`
    # (lex-only) answers. Feed space-joined short terms instead; keep the raw query only
    # as the no-terms fallback.
    lex_query = " ".join(matched) if matched else query
    include_lex = True
    if use_vector and lex_leg == "conditional":
        # Measured (2026-10-12): name/title lex terms hurt natural-language queries, while
        # escaped codes / date fragments are lex-only wins. Conditional keeps the lex leg
        # only for identifier-shaped tokens and never feeds name/title terms to it.
        idents = identifier_tokens(query)
        if idents:
            lex_query = " ".join(idents)
        else:
            include_lex = False
    elif use_vector and lex_leg == "off":
        include_lex = False
    searches: list[dict[str, str]] = []
    if include_lex:
        searches.append({"type": "lex", "query": lex_query})
    if use_vector:
        searches.append({"type": "vec", "query": query})
    if hyde and use_vector:
        searches.append({"type": "hyde", "query": hyde.strip()})
    if use_vector and entity_names:
        # 多实体比较类问句（≥2 个实体提及）：为每个实体追加独立向量腿，
        # 避免简称/长名差或互相稀释导致某侧实体在融合候选中缺席（bench X01/X02）。
        mentions = entity_mentions(query, entity_names)
        if len(mentions) >= 2:
            for name in mentions[:3]:
                searches.append({"type": "vec", "query": name})
    return searches
