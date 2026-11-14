# Company Wiki RAG V2.4

这是基于现有 Company Wiki 的工程化升级版，核心目标是让企业员工从“上传文件的下一秒”开始就能可靠问答，同时保留长期 LLM-Wiki 知识编译、QMD Hybrid Retrieval 和 Wiki Graph 的优势。

## 核心能力

- 保留 `raw/ → LLM-Wiki → Entity / Concept / Source / Wikilink` 的长期知识编译链路。
- 新增 **Hot Document Lane**：刚上传的 DOCX/PPTX/PDF/XLSX/文本无需等待 Wiki Compile 或 QMD embedding，即可结构化抽取并立即问答。
- 新增 **Retrieval Gate**：统一判断 `direct / hot-document / hot+company / company-rag`，不再只靠 Agent Prompt 猜是否需要 RAG。
- QMD 继续作为正式知识库的本地 Hybrid Retrieval Kernel（BM25 / Vector / RRF / Rerank）。
- 借鉴 hierarchical wiki RAG：Chapter Tree、Breadcrumb Context、Parent-Child Retrieval。
- Wikilink 1-hop Graph Retrieval：Graph neighbor → restricted retrieval → 邻居页最相关 section。
- Wiki `raw_sources`：restricted evidence retrieval → 关联原始资料中最相关 chunk，而不是固定第一个 chunk。
- Adaptive Context Builder：`diversity / balanced / source-focused`；单文档深挖时允许更多同源 chunk 和相邻 chunk。
- **多模态高保真 Evidence Extraction**：长期索引采用 Native Text + OCR + Vision API 三路合并。图片、扫描页、流程图、SmartArt、图表/截图等视觉语义会转为 EvidenceBlock，写入 `rag/qmd/evidence` 并被 QMD 索引。每个 chunk 保存页码/幻灯片/段落/表格/单元格/视觉资产等 source locator。
- **V2.4 双视觉路径**：Hot Document 视觉问题交给 Hermes 内置 Vision，后台 full ingestion 才调用外部模型 API 生成可缓存、可索引的长期 `visual_semantics`。
- 所有员工拥有完整知识权限；Profile 仅用于 trace 和“最近上传文件”关联，不做 ACL。
- 内置可复现的检索评测（`bench/`）：页覆盖、金标覆盖、MRR、入选率、SHR、延迟与可选答案质量抽查；方法与核心实验见 [docs/EVALUATION.md](docs/EVALUATION.md)。

## 项目目录

```text
company-wiki-rag-v2/
├── raw/                         # 原始事实文件，最终 Source of Truth
├── wiki/                        # LLM-Wiki 知识资产
├── rag/
│   ├── extracted/               # Native/OCR/API-Vision 合并后的可审计标准化证据层
│   ├── assets/                  # 从 PDF/Office/图片中物化的视觉资产（可重建）
│   ├── visual_cache/            # OCR/API-Vision 结果缓存（按图片 sha + 模型设置）
│   ├── hot/                     # 最近上传文档的临时即时检索层（运行时生成）
│   ├── qmd/
│   │   ├── core/                # Wiki Chapter-aware projection
│   │   └── evidence/            # raw evidence structural chunks
│   ├── manifest.json            # node / parent / child / source locator / projection mapping
│   ├── graph.json               # Wikilink page graph
│   ├── lexicon.json             # 企业术语
│   └── traces/
├── rag_gateway/
│   ├── evidence.py              # Native text/source block 抽取 + lossless chunking
│   ├── multimodal.py            # 视觉资产提取/渲染 + OCR + 后台 Vision API → EvidenceBlock
│   ├── hot.py                   # Hot Document ingest / immediate local retrieval
│   ├── gating.py                # direct / hot / hot+company / company-rag 路由
│   ├── gateway.py               # Agent-facing unified Knowledge Gateway
│   ├── projection.py            # Wiki/raw → RAG Projection
│   ├── markdown_tree.py         # Chapter Tree
│   ├── qmd_client.py            # shared QMD daemon client
│   ├── router.py                # RAG 深度 + Context policy
│   ├── graph.py                 # Wiki Graph
│   └── search.py                # formal QMD RAG orchestration/context builder
├── bench/                       # 评测/实验执行器（不含语料与结果）
├── config/rag.yaml
├── .qmd/index.yml
├── scripts/
│   ├── rag_hot_ingest.py        # 上传后立即执行
│   ├── rag_ask.py               # Hermes 统一查询入口
│   ├── rag_build.py
│   ├── rag_refresh.py
│   ├── rag_search.py            # 仅正式 Company RAG 的低层入口
│   ├── rag_multimodal_status.py # OCR/Vision/LibreOffice 运行状态
│   └── qmd_bootstrap.sh
└── skills/
    ├── company-wiki-upload/
    └── company-wiki-rag/
```

## 三阶段可用性模型

```text
员工上传文件
    │
    ▼
raw/ 落盘
    │
    ├──────── 立即：rag_hot_ingest.py
    │                 │
    │                 ▼
    │              rag/hot/
    │                 │
    │          Hot Document QA
    │          不等 LLM / QMD
    │
    ├──────── 后台：LLM-Wiki Compile
    │                 │
    │                 ▼
    │               wiki/
    │
    └──────── 编译后：rag_refresh.py
                      │
                      ├─ rag/extracted/
                      ├─ rag/qmd/core
                      ├─ rag/qmd/evidence
                      └─ qmd update + qmd embed
                              │
                              ▼
                        正式 Hybrid Graph-RAG
```

**关键点：编译前不是“没有 RAG”。** Hot Document Lane 是针对单个刚上传文件的临时原文检索层；长期 QMD/Graph RAG 则在知识编译后接管。

## Retrieval Gate：员工问题到底走不走 RAG

Hermes 优先调用统一入口：

```bash
python3 /home/demo/company-wiki/scripts/rag_ask.py \
  "<用户问题>" --profile "$PROFILE" --json
```

路由规则：

- `direct`：润色、翻译、写邮件、代码、通用创作，且不依赖企业内部事实。
- `hot-document`：有当前附件；或“这份/刚上传/原文/报告中”等追问命中最近上传文档。
- `hot+company`：当前附件需要和历史项目、其他客户、Company Wiki 做比较。
- `company-rag`：命中企业实体/术语、客户项目、访谈、内部方法论等知识意图。

当前附件必须显式传路径，最可靠：

```bash
python3 scripts/rag_ask.py "这份访谈的核心问题是什么？" \
  --attachment "/home/demo/company-wiki/raw/interviews/xxx.docx" \
  --profile "$PROFILE" --json
```

## Hot Document Lane

上传 skill 在文件保存后立即执行：

```bash
python3 scripts/rag_hot_ingest.py /home/demo/company-wiki/raw/.../file.docx --profile "$PROFILE"
```

Hot Ingest：

1. 不等待后台 Compile，也不要求 QMD vector embedding。
2. 先做 Native Text / 表格 / source locator 抽取。
3. 对图片、扫描页和视觉页面只做**有预算的视觉资产物化/渲染**，不调用 OCR/Vision API；员工立即提问时由 Hermes 内置 Vision 查看与当前问题最相关的图片/页面。
4. 小文档若能放进 Context Budget，直接使用完整 Native Text；若问题依赖视觉内容，Gateway 同时返回 `hermes_vision.assets` 供 Hermes 原生 Vision 当场查看。
5. 大文档才做结构感知 local retrieval + 邻接 chunk 补全。
6. Hot 视觉资产统一标记为 Hermes-deferred；后台 full `rag_refresh` 再执行 OCR + Vision API，生成可长期索引的多模态 evidence。

这条通道尤其解决：员工在飞书刚发一个文件，马上问“里面有哪些要求？”的真实业务场景。

## 最大程度保留原始信息：Native + OCR + Vision

`raw/` 永远保留原件；V2.4 的“最大程度保留”不仅指文本，还包括**把视觉信息转成可检索证据**。

```text
raw
 ├─ Native extractor ───────────────┐
 └─ Visual asset collector          │
       ├─ OCR：图片里写了什么字      │
       └─ Vision：图表达什么关系      │
                                     ▼
                         EvidenceBlock stream
                                     ▼
                             rag/extracted/
                                     ▼
                         rag/qmd/evidence/
                                     ▼
                                  QMD
```

- DOCX：Heading、段落、列表、表格、页眉页脚；提取 inline images；LibreOffice 可用时可渲染页面，补 SmartArt/Drawing Canvas/复杂版式。
- PPTX：文本框、表格、可读图表数据、备注、嵌入图片；同时把整张 slide 渲染给 Vision，恢复箭头、分组、战略屋、流程图和空间关系。
- PDF：保留原文本 block；无文本扫描页、含图片页、含较多 vector drawings 的页面会渲染后 OCR + Vision。
- XLSX：单元格、公式、缓存值、嵌入图片；可选页面级渲染。
- PNG/JPG/WebP/TIFF：图片本身是一等 raw source，直接 OCR + Vision。

后台 OCR 生成 `visual_ocr` block；后台 Vision API 生成 `visual_semantics` block。两者都带 `source_locator / visual_asset / sha256 / provider`，并与 Native block 一起切片，所以**视觉内容最终真实存在于 `rag/qmd/evidence/*.md`，不是只保存一张图片路径**。

例如流程图会形成类似：

```markdown
## OCR 识别文本
步骤甲 / 步骤乙 / 步骤丙 / 步骤丁

## 视觉语义解析
流程为：步骤甲 → 步骤乙；条件成立进入步骤丙，否则返回步骤甲；完成后结束。
```

`rag/assets/` 保存 Hot Hermes Vision 与后台 OCR/API Vision 可复用的图片/页面渲染；`rag/visual_cache/` 按图片 sha 和模型设置缓存后台分析结果，避免每次 `rag_refresh` 重复跑视觉模型。详见 `docs/MULTIMODAL_EVIDENCE_PIPELINE.md`。

切片仍遵循：

> **先保留原子 EvidenceBlock，再组合 chunk；绝不为了 chunk 大小静默删除 Native/OCR/Vision block。**

每个 evidence chunk 在 `manifest.json` 中保存 `source_locators / source_block_ids / source_kinds / visual_assets / extracted_source`，因此可以从 QMD 命中反向定位到具体 PDF 页、PPT slide、DOCX 段落/图片和视觉资产。

## `rag/qmd/` 与 QMD SQLite

`rag/qmd/core` 和 `rag/qmd/evidence` 是 QMD 的**输入语料**；执行 `qmd update / qmd embed` 后，QMD 将全文与向量索引写入自己的 SQLite（默认 `~/.cache/qmd/index.sqlite`）。

正常正式查询：

```text
Query
  ↓
QMD index.sqlite
  ├─ FTS5 / BM25
  └─ sqlite-vec
  ↓
RRF / Rerank
  ↓
QMD URI
  ↓
manifest.json
  ↓
rag/qmd 对应 Projection Markdown
```

所以 QMD **查 SQLite**，不是查询时扫描 `rag/qmd/`；`rag/qmd/` 是可重建的搜索语料与最终 Context 内容源。

## 正式 Company RAG

```text
Query
  ↓
QMD core + evidence
  ↓
BM25 + Vector → RRF → Rerank
  ↓
Primary Candidates
  ├─ Parent/Child
  ├─ Graph neighbor → within-page restricted retrieval → best section
  └─ linked raw source → restricted retrieval → best evidence chunk
  ↓
Adaptive Context Builder
  ├─ diversity
  ├─ balanced
  └─ source-focused
  ↓
[S1]...[Sn]
  ↓
Hermes grounded answer
```

## Core / Evidence / Hot 的职责

- `company-core`：LLM-Wiki 高密度知识，偏知识密度。
- `company-evidence`：长期 raw 原始证据，偏覆盖原话 / 数字 / 细节。
- `rag/hot`：尚未完成 Wiki/QMD 编译的最近上传文件，解决“上传即问”。

它们不是三套重复知识库，而是三个生命周期阶段/信息层次。


## 快速开始

```bash
cd /home/demo/company-wiki
python3 scripts/project_validate.py
bash scripts/qmd_bootstrap.sh
```

正式知识库：

```bash
python3 scripts/rag_ask.py "示例公司A文化建设有哪些问题？" --profile main --json
```

模拟上传即问：

```bash
python3 scripts/rag_hot_ingest.py raw/interviews/example.docx --profile main
python3 scripts/rag_ask.py "这份访谈主要讲了什么？" \
  --attachment "$PWD/raw/interviews/example.docx" --profile main --json
```

## 当前仍保留的边界

- OCR/API Vision 已进入正式 Evidence Pipeline：推荐 PaddleOCR；Vision 通过外部多模态模型 API（OpenAI-compatible schema）调用。API 未配置/失败时系统明确保留 warning/pending，不会伪造视觉文本。
- Hot Document 不调用外部 Vision API，视觉理解由 Hermes 内置 Vision 即时完成；完整 `rag_refresh.py` 才负责 OCR + API Vision 的长期 full multimodal evidence。
- DOCX 脚注、批注、修订历史仍可能需要更专门的 OOXML extractor；原始文件始终保留，因此不会丢失事实源。

## 评测

仓库自带可复现的检索评测执行器（`bench/`），指标定义、核心实验与复现方式见
[docs/EVALUATION.md](docs/EVALUATION.md)；如何接自己的语料见
[docs/BYO_CORPUS.md](docs/BYO_CORPUS.md)。

公开仓**不附带真实语料、题集、金标或结果文件**，因此下表是聚合历史结果，不是开箱即跑的基准；
用同一套脚本在自己的语料上复测才有可比较的数字。

| 集合 | page@5 | page@10 | gold@5 | gold@10 | MRR | sel_ratio |
|---|---:|---:|---:|---:|---:|---:|
| 58 自然语言 | .911 | .974 | .911 | .974 | .837 | .983 |
| hard50 | .770 | .790 | .870 | .910 | .803 | .960 |
| 24 字面串 | — | — | .875 | — | — | — |

- 推荐配置：`lex_leg=conditional` + `budget_policy=density`；延迟 p50 ≈ 1.22s（含云端重排）。
- 收紧候选/上下文后的单轮复核：58 页/金标 `.9195/.9741`；hard50 页 `.780/.790`、金标 `.900/.910`（与上表不是同批）。
- 答案质量（58 案、同一 answer/judge 模型）：Faithfulness `4.879`、Correctness `3.517`（1–5）；这是次级检查，不声明答案层优势。
- 公开仓**不包含外部基线对比实验或对比指标**。

## 评测说明

公开仓只发布聚合指标与实验结论，不发布真实语料、查询原文、回答、金标路径或结果明细。

