# Company Wiki RAG V2 Architecture

## 目标

在不破坏现有 LLM-Wiki 的前提下，为 Hermes 多 Profile 增加本地 Hybrid RAG，并把结构化 Wiki 的优势用于提高召回、上下文完整性与事实可追溯性。

```text
飞书 / 微信
    │
    ▼
Hermes Profile
    │
    ▼
company-wiki-rag Skill
    │
    ▼
RAG Gateway
    ├── Adaptive Retrieval Router
    ├── Adaptive Context Policy
    └── Query lexical context
    │
    ▼
QMD shared daemon
    ├── FTS5 / BM25
    ├── sqlite-vec
    ├── RRF
    └── Reranker
    │
    ▼
Primary Ranked Nodes
    │
    ├── Parent / Child injection
    ├── Graph neighbor → within-page best section
    ├── Linked raw source → restricted best chunk
    └── Evidence neighbor expansion
    │
    ▼
Adaptive Context Builder
    ├── relevance
    ├── source/page repetition penalty
    ├── policy-specific hard caps
    └── character budget
    │
    ▼
[S1]...[Sn] grounded context
    │
    ▼
Hermes LLM Answer
```

## 权限模型

当前公司场景中所有员工拥有完整知识库权限，因此 RAG Gateway 不做 ACL/collection 过滤。`profile` 仍保留为 trace 中的 caller identity，方便后续排查哪一个 Agent/Profile 发起了请求。

## 为什么保留 QMD

QMD 承担本地检索内核：SQLite FTS5、sqlite-vec、Hybrid fusion 和 reranking。项目不再引入 ChromaDB 或第二套向量数据库，避免重复索引、进程和运维面。

## 从 hierarchical wiki RAG 借鉴的部分

- Markdown Heading → Chapter Tree。
- breadcrumb 写入检索文档，让 embedding/reranker 知道章节语境。
- Parent/Child Injection 只在高置信命中后发生，并设置严格预算。
- 检索文档不是原 Wiki 本体，而是可重建 Projection。

## 本项目进一步扩展的部分

- Entity / Concept / Source 跨文档 Wikilink Graph。
- Graph 不再只展开页面 root，而是对邻居页做 section-level retrieval。
- raw evidence 第二层检索，防止 LLM-Wiki 编译压缩丢失细节。
- source provenance 不再机械取第一个 chunk，而是 restricted evidence retrieval。
- Adaptive Context Builder 区分 diversity / balanced / source-focused。
- Raw evidence neighbor expansion，补齐连续语义。
- Adaptive fast/balanced/deep 检索模式。
- 本地 trace，为后续质量复盘预留数据。

## V2.4: Upload-to-Answer Hot Path

```text
Feishu/WeChat attachment
        ↓
Hermes Profile
        ↓
raw/ save
        ├─→ rag_hot_ingest.py → rag/hot/ → immediate document QA
        └─→ .pending → 后台 LLM-Wiki Compile → wiki/ → rag_refresh → QMD
```

The hot path is intentionally independent from QMD and the LLM compiler. It provides availability immediately after upload; the formal `company-core + company-evidence` path provides higher-quality long-term retrieval after compilation/indexing.

`rag_ask.py` is now the Agent-facing gateway. It chooses `direct`, `hot-document`, `hot+company`, or `company-rag` before any expensive retrieval work.

## V2.4: Evidence fidelity layer

`raw/` remains the authoritative binary source. `rag/extracted/` stores a reproducible normalized representation with source locators. `rag/qmd/evidence/` is then built from structural blocks rather than a lossy flat-text hard split. This makes retrieval chunks disposable while preserving a complete extracted view for audit and context expansion.


## V2.4: Multimodal Evidence Plane

```text
raw document/image
      │
      ├── native extractor ───────────────────────┐
      │                                           │
      └── visual asset collector                  │
              │                                   │
              ├── PaddleOCR/Tesseract             │
              │      └── visual_ocr               │
              │                                   │
              └── external Vision model API                │
                     └── visual_semantics          │
                                                  ▼
                                        EvidenceBlock stream
                                                  │
                                      ┌───────────┴───────────┐
                                      ▼                       ▼
                                rag/extracted           rag/qmd/evidence
                                                              │
                                                              ▼
                                                        QMD index.sqlite
```

`rag/assets/` 保存可重建的视觉输入，`rag/visual_cache/` 保存 OCR/VLM 结果缓存。QMD 不直接索引图片二进制，而是索引 OCR 与视觉语义转换后的 Markdown。这样流程图的箭头关系、组织图层级、扫描页文字等会和原生文本一起进入 Retriever。


## Hot Vision / Background Vision split

```text
Hot upload
  → materialize visual assets
  → Gateway selects query-relevant assets
  → Hermes native Vision
  → current answer only

Background full refresh
  → OCR
  → external multimodal model API
  → visual_ocr / visual_semantics EvidenceBlocks
  → rag/qmd/evidence
  → QMD SQLite
```

这条边界保证即时问答不依赖外部 API，同时长期知识索引仍具有稳定、可缓存、可重建的多模态证据。
