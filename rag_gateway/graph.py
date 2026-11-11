from __future__ import annotations
from pathlib import Path
from collections import defaultdict
from .frontmatter import read_markdown
from .markdown_tree import extract_wikilinks

INFRA = {"index.md", "overview.md", "graph-report.md", "log.md"}


def build_wiki_graph(root: Path, include_infra: bool = False) -> dict:
    wiki_dir = root / "wiki"
    pages = {}
    slug_to_page = {}
    raw_to_pages = defaultdict(list)
    for path in sorted(wiki_dir.rglob("*.md")):
        rel = path.relative_to(root).as_posix()
        if not include_infra and path.name in INFRA:
            continue
        meta, body = read_markdown(path)
        slug = path.stem
        title = str(meta.get("title") or slug)
        ptype = str(meta.get("type") or path.parent.name.rstrip("s"))
        sources = meta.get("sources") or []
        if isinstance(sources, str):
            sources = [sources]
        links = extract_wikilinks(body)
        pages[rel] = {
            "slug": slug,
            "title": title,
            "type": ptype,
            "sources": list(sources),
            "outgoing_slugs": [x["slug"] for x in links],
        }
        slug_to_page[slug] = rel
        for src in sources:
            raw_to_pages[str(src)].append(rel)

    edges = {}
    incoming = defaultdict(list)
    for rel, page in pages.items():
        resolved = []
        for slug in page["outgoing_slugs"]:
            target = slug_to_page.get(slug)
            if target and target != rel and target not in resolved:
                resolved.append(target)
                incoming[target].append(rel)
        edges[rel] = resolved

    return {
        "pages": pages,
        "slug_to_page": slug_to_page,
        "edges": edges,
        "incoming": dict(incoming),
        "raw_to_pages": dict(raw_to_pages),
    }
