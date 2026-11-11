from __future__ import annotations
from pathlib import Path
from collections import defaultdict
import hashlib, json, re, shutil
from .frontmatter import read_markdown, dump_frontmatter
from .markdown_tree import parse_markdown_tree, strip_md
from .graph import build_wiki_graph, INFRA
from .evidence import SUPPORTED, extract_document, extracted_markdown, chunk_extracted_document, sha256_file, load_sidecar
from .multimodal import VisualEvidenceProcessor


def decode_u_escapes(value: str) -> str:
    """Decode export-safe #U4e2d style filenames back to their logical Unicode form."""
    return re.sub(r"#U([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), value)


def safe_name(value: str, max_len: int = 80) -> str:
    v = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._")
    return (v or "item")[:max_len]


def summarize(text: str, limit: int) -> str:
    clean = strip_md(text)
    return clean[:limit] + ("…" if len(clean) > limit else "")


def _core_relpath(page_type: str, slug: str, node_id: str, idx: int) -> Path:
    hid = node_id.rsplit("#", 1)[-1]
    return Path(page_type) / safe_name(slug) / f"{idx:03d}-{hid}.md"


def build_projection(root: Path, config: dict) -> dict:
    p_cfg = config.get("projection", {})
    project_cfg = config.get("project", {})
    rag_dir = root / project_cfg.get("rag_dir", "rag")
    core_dir = root / project_cfg.get("projection_dir", "rag/qmd") / "core"
    evidence_dir = root / project_cfg.get("projection_dir", "rag/qmd") / "evidence"
    extracted_dir = root / project_cfg.get("extracted_dir", "rag/extracted")
    include_infra = bool(p_cfg.get("include_infrastructure_pages", False))
    summary_chars = int(p_cfg.get("summary_chars", 260))
    chunk_chars = int(p_cfg.get("evidence_chunk_chars", 3200))
    overlap = int(p_cfg.get("evidence_overlap_chars", 350))
    visual_processor = VisualEvidenceProcessor(root, config, mode="full")

    for d in (core_dir, evidence_dir, extracted_dir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    rag_dir.mkdir(parents=True, exist_ok=True)

    graph = build_wiki_graph(root, include_infra=include_infra)
    manifest = {
        "version": 4,
        "nodes": {},
        "projection_to_node": {},
        "page_roots": {},
        "page_nodes": defaultdict(list),
        "raw_chunks": defaultdict(list),
        "raw_alias_to_actual": {},
        "raw_to_wiki_pages": graph.get("raw_to_pages", {}),
        "raw_extracted": {},
    }
    lex_terms = set()

    wiki_dir = root / project_cfg.get("wiki_dir", "wiki")
    for path in sorted(wiki_dir.rglob("*.md")):
        if not include_infra and path.name in INFRA:
            continue
        rel = path.relative_to(root).as_posix()
        meta, body = read_markdown(path)
        page_title = str(meta.get("title") or path.stem)
        page_type = str(meta.get("type") or path.parent.name.rstrip("s"))
        tags = meta.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        raw_sources = meta.get("sources") or []
        if isinstance(raw_sources, str):
            raw_sources = [raw_sources]
        nodes = parse_markdown_tree(body, rel, page_title)
        slug = path.stem
        for idx, node in enumerate(nodes):
            proj_rel = _core_relpath(page_type, slug, node.node_id, idx)
            out = core_dir / proj_rel
            out.parent.mkdir(parents=True, exist_ok=True)
            link_labels = [x["label"] for x in node.wikilinks]
            link_slugs = [x["slug"] for x in node.wikilinks]
            keywords = list(dict.fromkeys([page_title, node.title, *tags, *link_labels, *link_slugs]))
            lex_terms.update(x for x in keywords if x and len(str(x)) <= 80)
            fm = {
                "rag_node_id": node.node_id,
                "rag_kind": "wiki_section",
                "source_wiki": rel,
                "page_slug": slug,
                "page_type": page_type,
                "page_title": page_title,
                "section_title": node.title,
                "breadcrumb": " > ".join(node.breadcrumb),
                "level": node.level,
                "parent_id": node.parent_id,
                "children_ids": node.children_ids,
                "tags": tags,
                "raw_sources": raw_sources,
                "wikilinks": link_slugs,
                "updated": meta.get("updated"),
                "confidence": meta.get("confidence"),
            }
            summary = summarize(node.content, summary_chars)
            context_line = (
                f"知识类型：{page_type}；页面：{page_title}；章节路径：{' > '.join(node.breadcrumb)}。"
                f"相关关键词：{'、'.join(str(x) for x in keywords[:18])}。"
            )
            body_out = (
                dump_frontmatter(fm)
                + f"# {page_title} · {node.title}\n\n"
                + f"> 检索上下文：{context_line}\n\n"
                + f"> 章节摘要：{summary or '本节以结构关系和来源信息为主。'}\n\n"
                + "检索关键词：" + " ".join(str(x) for x in keywords) + "\n\n"
                + "## 正文\n\n" + node.content.strip() + "\n"
            )
            out.write_text(body_out, encoding="utf-8", newline="\n")
            projection_key = proj_rel.as_posix()
            manifest["nodes"][node.node_id] = {
                **node.to_dict(),
                "projection": projection_key,
                "collection": "company-core",
                "page_type": page_type,
                "page_title": page_title,
                "page_slug": slug,
                "raw_sources": raw_sources,
            }
            manifest["projection_to_node"][projection_key] = node.node_id
            manifest["page_nodes"][rel].append(node.node_id)
            if node.parent_id is None and rel not in manifest["page_roots"]:
                manifest["page_roots"][rel] = node.node_id

    # Raw evidence projection. Deterministic extractors preserve traceability.
    raw_dir = root / project_cfg.get("raw_dir", "raw")
    if raw_dir.exists():
        for path in sorted(raw_dir.rglob("*")):
            if not path.is_file() or path.name.endswith(".meta.json") or path.suffix.lower() not in SUPPORTED:
                continue
            raw_rel = path.relative_to(root).as_posix()
            logical_raw_rel = decode_u_escapes(raw_rel)
            manifest["raw_alias_to_actual"][logical_raw_rel] = raw_rel
            extracted = extract_document(path, visual_processor=visual_processor)
            normalized = extracted_markdown(extracted)
            if not normalized.strip():
                continue
            chunks = chunk_extracted_document(extracted, max_chars=chunk_chars, overlap=overlap)
            digest = extracted.get("sha256") or sha256_file(path)
            sidecar = load_sidecar(path)
            category = path.parent.name
            source_key = hashlib.sha1(raw_rel.encode("utf-8")).hexdigest()[:12]
            basename = safe_name(path.stem, 56)
            extracted_rel = Path(category) / f"{source_key}-{basename}.md"
            extracted_out = extracted_dir / extracted_rel
            extracted_out.parent.mkdir(parents=True, exist_ok=True)
            extracted_out.write_text(normalized, encoding="utf-8", newline="\n")
            linked_wiki = graph.get("raw_to_pages", {}).get(raw_rel, []) or graph.get("raw_to_pages", {}).get(logical_raw_rel, [])
            manifest["raw_extracted"][raw_rel] = {
                "path": extracted_rel.as_posix(),
                "sha256": digest,
                "warnings": extracted.get("warnings") or [],
                "needs_vision": bool(extracted.get("needs_vision")),
                "block_count": len(extracted.get("blocks") or []),
                "visual_analysis": extracted.get("visual_analysis") or {},
                "visual_asset_count": len(extracted.get("visual_assets") or []),
            }
            for idx, chunk in enumerate(chunks):
                chunk_id = f"raw:{raw_rel}#{idx:04d}"
                proj_rel = Path(category) / f"{source_key}-{basename}" / f"{idx:04d}.md"
                out = evidence_dir / proj_rel
                out.parent.mkdir(parents=True, exist_ok=True)
                crumb = " > ".join(chunk["breadcrumb"]) or f"片段 {idx+1}"
                summary = summarize(chunk["text"], summary_chars)
                fm = {
                    "rag_node_id": chunk_id,
                    "rag_kind": "raw_evidence",
                    "raw_path": raw_rel,
                    "logical_raw_path": logical_raw_rel,
                    "raw_sha256": digest,
                    "chunk_index": idx,
                    "breadcrumb": crumb,
                    "uploaded_by": sidecar.get("uploaded_by"),
                    "uploaded_at": sidecar.get("uploaded_at"),
                    "file_type": sidecar.get("file_type"),
                    "linked_wiki_pages": linked_wiki,
                    "extracted_source": extracted_rel.as_posix(),
                    "source_locators": chunk.get("locators") or [],
                    "source_block_ids": chunk.get("block_ids") or [],
                    "source_kinds": chunk.get("kinds") or [],
                    "visual_assets": chunk.get("visual_assets") or [],
                    "visual_kinds": chunk.get("visual_kinds") or [],
                    "needs_vision": bool(extracted.get("needs_vision")),
                }
                context = (
                    f"原始证据：{path.name}；分类：{category}；章节/位置：{crumb}。"
                    + (f"关联 Wiki：{', '.join(linked_wiki[:5])}。" if linked_wiki else "")
                )
                out.write_text(
                    dump_frontmatter(fm)
                    + f"# {path.name} · {crumb}\n\n"
                    + f"> 检索上下文：{context}\n\n"
                    + f"> 片段摘要：{summary}\n\n"
                    + "## 原始证据\n\n" + chunk["text"].strip() + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                projection_key = proj_rel.as_posix()
                manifest["nodes"][chunk_id] = {
                    "node_id": chunk_id,
                    "projection": projection_key,
                    "collection": "company-evidence",
                    "raw_path": raw_rel,
                    "logical_raw_path": logical_raw_rel,
                    "breadcrumb": chunk["breadcrumb"],
                    "chunk_index": idx,
                    "page_title": path.name,
                    "title": crumb,
                    "raw_sha256": digest,
                    "linked_wiki_pages": linked_wiki,
                    "extracted_source": extracted_rel.as_posix(),
                    "source_locators": chunk.get("locators") or [],
                    "source_block_ids": chunk.get("block_ids") or [],
                    "source_kinds": chunk.get("kinds") or [],
                    "visual_assets": chunk.get("visual_assets") or [],
                    "visual_kinds": chunk.get("visual_kinds") or [],
                    "needs_vision": bool(extracted.get("needs_vision")),
                }
                manifest["projection_to_node"]["evidence/" + projection_key] = chunk_id
                manifest["raw_chunks"][raw_rel].append(chunk_id)
                lex_terms.add(path.stem)

    manifest["page_nodes"] = dict(manifest["page_nodes"])
    manifest["raw_chunks"] = dict(manifest["raw_chunks"])
    (root / project_cfg.get("manifest", "rag/manifest.json")).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    (root / project_cfg.get("graph", "rag/graph.json")).write_text(
        json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    lexicon = {"terms": sorted(lex_terms, key=lambda x: (-len(str(x)), str(x)))}
    (root / project_cfg.get("lexicon", "rag/lexicon.json")).write_text(
        json.dumps(lexicon, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    visual_docs = list(manifest.get("raw_extracted", {}).values())
    return {
        "wiki_nodes": sum(1 for n in manifest["nodes"].values() if n["collection"] == "company-core"),
        "evidence_chunks": sum(1 for n in manifest["nodes"].values() if n["collection"] == "company-evidence"),
        "wiki_pages": len(manifest["page_nodes"]),
        "raw_sources": len(manifest["raw_chunks"]),
        "visual_assets": sum(int(d.get("visual_asset_count", 0)) for d in visual_docs),
        "ocr_blocks": sum(int((d.get("visual_analysis") or {}).get("ocr_block_count", 0)) for d in visual_docs),
        "vision_blocks": sum(int((d.get("visual_analysis") or {}).get("vision_block_count", 0)) for d in visual_docs),
        "visual_pending_assets": sum(int((d.get("visual_analysis") or {}).get("pending_asset_count", 0)) for d in visual_docs),
        "visual_unresolved_assets": sum(int((d.get("visual_analysis") or {}).get("unresolved_vision_asset_count", 0)) for d in visual_docs),
    }
