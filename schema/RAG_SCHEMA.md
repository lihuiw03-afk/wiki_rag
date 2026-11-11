# RAG Projection Schema

`rag/` 是 **派生索引层**，可以随时删除并由 `scripts/rag_build.py` 重建。它不是真实知识源。

## 三层真相

1. `raw/`：原始事实与文件，最高可追溯层。
2. `wiki/`：LLM 编译后的 Entity / Concept / Source / Synthesis / Finding / Comparison 知识资产。
3. `rag/`：为了检索效果而生成的 Chapter-aware / Context-enriched Markdown 投影和结构 Manifest。

## Wiki RAG Node

每个 Markdown 标题章节被投影为独立检索节点：

- `rag_node_id`
- `source_wiki`
- `page_type`
- `page_title`
- `section_title`
- `breadcrumb`
- `parent_id`
- `children_ids`
- `wikilinks`
- `raw_sources`
- `confidence`

正文前增加确定性的“检索上下文”和“章节摘要”，避免孤立 chunk 丢失页面/章节语义。

## Raw Evidence Node

DOCX/PPTX/PDF/XLSX/图片先形成统一 EvidenceBlock stream：Native blocks + `visual_ocr` + `visual_semantics`，再按结构与字符预算组合为 evidence chunk：

- `raw_path`
- `raw_sha256`
- `chunk_index`
- `breadcrumb`
- `uploaded_by`
- `linked_wiki_pages`
- `source_locators`
- `source_block_ids`
- `source_kinds`
- `visual_assets`
- `visual_kinds`

视觉 asset 保存在 `rag/assets/`，OCR/VLM 缓存保存在 `rag/visual_cache/`。扫描页、截图、流程图等在视觉后端可用时会转换成真实 Markdown evidence；后端不可用时才保留 warning/pending，禁止伪造视觉内容。

## Runtime Structural Retrieval

`rag/manifest.json` 保存 Chapter Tree 与 raw chunk 顺序；`rag/graph.json` 保存 page-level Wikilink 关系。运行时不直接把这些 JSON 内容提供给 LLM，而是用于导航到真实 Projection Node：

- Parent / Child：根据 `parent_id` / `children_ids` 补结构上下文。
- Graph：先找到邻居 page，再在该 page 的 `page_nodes` 中选择与 Query 最相关的 section。
- Raw source：根据 `raw_chunks` 在关联 source 中选择最相关 evidence chunk。
- Evidence neighbors：对高分 raw chunk 可按 `raw_chunks` 顺序补前后相邻 chunk。

## Context Policy

最终 Context 不是固定 source cap，而是运行时按 Query 选择：

- `diversity`：强调跨来源覆盖。
- `balanced`：默认折中。
- `source-focused`：允许单一原始来源占据更多 Context，并增加连续 chunk。
