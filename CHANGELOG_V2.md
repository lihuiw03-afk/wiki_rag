# V2 Integration Summary

## V2.1 Retrieval Orchestration Upgrade

- Removed ACL filtering. All employees/Profiles have full knowledge access; `profile` is trace-only caller identity.
- Replaced fixed context diversity with adaptive `diversity / balanced / source-focused` policies.
- `source-focused` allows up to 8 chunks from one raw source and raises evidence/context budgets.
- Added evidence neighbor expansion for continuous raw-document context.
- Graph expansion now performs `neighbor page → restricted retrieval → best section` instead of injecting the page root.
- Wiki-linked raw sources now perform restricted evidence retrieval and select the most relevant chunk instead of always taking chunk 0.
- Added fail-soft local CJK/Latin lexical fallback when a restricted target is outside QMD's returned candidate window.
- Added `--context-policy` CLI override and context stats in traces/results.
- Expanded unit tests from 5 to 9, covering adaptive context, graph section selection, and restricted source evidence retrieval.

## V2.0 Added

- QMD project-local configuration with `company-core` and `company-evidence` collections.
- Chapter-aware Wiki projection into retrievable sections.
- Deterministic DOCX/PPTX/PDF evidence extraction.
- Context enrichment: page type/title, breadcrumb, tags, Wikilinks, raw sources, deterministic summary and lexical terms.
- Parent/Child context injection with score thresholds and strict budgets.
- 1-hop Wikilink graph expansion.
- Wiki → raw evidence support injection.
- Adaptive Fast / Balanced / Deep retrieval modes.
- Optional HyDE only for deep/abstractive questions.
- Source diversity and context budget controls.
- Local trace output for future quality review.
- Hermes `company-wiki-rag` Skill.
- Post-compile `rag_refresh.py` hook.
- QMD shared daemon bootstrap/systemd examples.
- Handling for `#Uxxxx` export-safe filesystem names so Wiki provenance can still resolve to raw files.

## Intentionally not added

- ChromaDB / second vector database.
- Independent Jieba BM25 index.
- A second Agent Harness beside Hermes.
- Automatic OCR/Vision for scanned PDFs.

## V2.2 — Upload-to-Answer Engineering

- 新增 Hot Document Lane：上传文件后不等 Wiki Compile/QMD embedding 即可立即问答。
- 新增统一 `scripts/rag_ask.py` 与 deterministic Retrieval Gate：`direct / hot-document / hot+company / company-rag`。
- 新增 `scripts/rag_hot_ingest.py`、`rag_gateway/hot.py`、`rag_gateway/gating.py`、`rag_gateway/gateway.py`。
- 原始 evidence pipeline 升级为结构化、高保真抽取：DOCX/PPTX/PDF/XLSX/文本；显式记录视觉信息缺口。
- 新增 `rag/extracted/`，保存每个 raw source 的完整标准化抽取文本，避免只留下 chunks。
- evidence chunk 改为结构 block 组合，保留 `source_locators / source_block_ids`，减少无意义硬切。
- 小型 Hot Document 优先全文 Context；大文档才做局部结构检索，并覆盖 broad-summary 场景。
- 扫描/图片型资料标记 `needs_vision`，只有用户问题真正依赖视觉内容时才使用 Hermes Vision。
- 新增路由、结构化切片和 provenance 测试；当前测试总数 14。

## V2.3 — Multimodal Evidence Preservation

- Added `rag_gateway/multimodal.py`.
- Visual content is now part of the RAG corpus instead of only a `needs_vision` marker.
- Added OCR evidence blocks (`visual_ocr`) and VLM semantic evidence blocks (`visual_semantics`).
- Added standalone image raw sources: PNG/JPG/JPEG/WebP/BMP/TIFF.
- PDF visual/scanned pages are rendered with PyMuPDF for OCR/Vision.
- PPTX embedded images are extracted; LibreOffice full-slide rendering recovers SmartArt/arrows/layout for VLM analysis.
- DOCX inline images are mapped to paragraph locators; optional LibreOffice page rendering covers drawing/layout semantics.
- XLSX embedded images supported; page rendering remains optional.
- Added `rag/assets/` visual asset materialization and `rag/visual_cache/` result cache.
- Cache supports partial enrichment: OCR can be cached while Vision is retried after the VLM service becomes available.
- Full projection writes visual asset provenance into `manifest.json` and `rag/qmd/evidence`.
- Hot Document Lane originally performed bounded eager multimodal enrichment; long-term `rag_refresh` performed full configured enrichment.
- Added `scripts/rag_multimodal_status.py`, `requirements-vision.txt`, and `docs/MULTIMODAL_EVIDENCE_PIPELINE.md`.
- Added 3 V2.3 multimodal tests; total unit tests: 17.


## V2.4 — Split Hot Hermes Vision from Background Vision API

- Hot Document no longer calls OCR or the external Vision API.
- Hot ingest only extracts Native Text and materializes/render visual assets with provenance.
- `rag_ask.py` now returns a `hermes_vision` contract containing query-relevant local image/page assets.
- Hermes Skill must use Hermes native Vision for current-upload visual questions.
- Added page/slide targeting, selected-locator matching, visual-intent detection, scan detection and broad-summary visual sampling.
- Background/full ingestion keeps OCR but switches Vision to a configurable external multimodal model API (`provider: model_api`).
- API URL/model/key are read from environment variables; local VLM deployment is no longer required.
- Hermes Vision observations remain turn-scoped and are not silently persisted into long-term evidence.
- Background API Vision results remain cacheable `visual_semantics` and continue to enter `rag/qmd/evidence` → QMD SQLite.
- `hot+company` propagates the Hermes Vision request alongside formal Company RAG context.
- Updated multimodal status/deployment/docs/skills for the two-path visual architecture.
- Added V2.4 tests for Hermes-deferred hot visual processing; total unit tests: 19.
