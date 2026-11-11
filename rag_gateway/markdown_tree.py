from __future__ import annotations
from dataclasses import dataclass, field, asdict
from pathlib import Path
import hashlib, re

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")


def strip_md(text: str) -> str:
    text = re.sub(r"`{1,3}.*?`{1,3}", " ", text, flags=re.S)
    text = re.sub(r"!?(?:\[([^\]]*)\])\([^)]*\)", r"\1", text)
    text = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"[*_>#|~-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_wikilinks(text: str) -> list[dict[str, str]]:
    out = []
    seen = set()
    for slug, label in WIKILINK_RE.findall(text):
        slug = slug.strip()
        label = (label or slug).strip()
        key = (slug, label)
        if key not in seen:
            out.append({"slug": slug, "label": label})
            seen.add(key)
    return out


@dataclass
class SectionNode:
    node_id: str
    page_relpath: str
    title: str
    level: int
    breadcrumb: list[str]
    parent_id: str | None
    children_ids: list[str] = field(default_factory=list)
    start_line: int = 1
    end_line: int = 1
    content: str = ""
    wikilinks: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _node_id(page_relpath: str, breadcrumb: list[str], occurrence: int) -> str:
    raw = page_relpath + "|" + " > ".join(breadcrumb) + f"|{occurrence}"
    return f"wiki:{page_relpath}#{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"


def parse_markdown_tree(body: str, page_relpath: str, fallback_title: str) -> list[SectionNode]:
    lines = body.splitlines()
    headings: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m:
            headings.append((i, len(m.group(1)), strip_md(m.group(2))))

    if not headings:
        node = SectionNode(
            node_id=_node_id(page_relpath, [fallback_title], 0),
            page_relpath=page_relpath,
            title=fallback_title,
            level=1,
            breadcrumb=[fallback_title],
            parent_id=None,
            start_line=1,
            end_line=max(1, len(lines)),
            content=body.strip(),
            wikilinks=extract_wikilinks(body),
        )
        return [node]

    nodes: list[SectionNode] = []
    stack: list[SectionNode] = []
    occurrence: dict[str, int] = {}

    # Text before first heading is folded into the first section.
    for idx, (line_no, level, title) in enumerate(headings):
        while stack and stack[-1].level >= level:
            stack.pop()
        breadcrumb = [n.title for n in stack] + [title]
        key = " > ".join(breadcrumb)
        occ = occurrence.get(key, 0)
        occurrence[key] = occ + 1
        parent = stack[-1] if stack else None
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(lines)
        start = 0 if idx == 0 else line_no
        # Direct section text only: stop at the next heading of any level.
        section_text = "\n".join(lines[start:end]).strip()
        node = SectionNode(
            node_id=_node_id(page_relpath, breadcrumb, occ),
            page_relpath=page_relpath,
            title=title,
            level=level,
            breadcrumb=breadcrumb,
            parent_id=parent.node_id if parent else None,
            start_line=start + 1,
            end_line=max(start + 1, end),
            content=section_text,
            wikilinks=extract_wikilinks(section_text),
        )
        nodes.append(node)
        if parent:
            parent.children_ids.append(node.node_id)
        stack.append(node)
    return nodes
