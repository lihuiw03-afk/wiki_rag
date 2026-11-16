# Company Wiki RAG V2.4

面向企业知识库的即时问答与长期检索系统：文件上传后可以先通过 Hot Document Lane 立即问答，再经过 LLM-Wiki 编译、Evidence Extraction 和 QMD 索引，进入正式的 Hybrid Graph-RAG。

> **公开仓状态**：不包含真实语料、Wiki 页面、QMD 索引和评测数据；`raw/`、`wiki/`、`rag/`、`bench/dataset/`、`bench/results/` 都是运行时目录，需自行准备。项目不发布与外部基线或普通 RAG 的对比实验。

> **Agent-agnostic**：核心检索、Hot 路径、CLI 和 HTTP `/ask` 不依赖 Hermes；Hermes 只是参考 Agent 集成。自建 Agent、其他 Agent 框架或普通服务都可以调用这套 RAG。

## 目录

- [1. 这是什么](#1-这是什么)
- [2. 核心能力](#2-核心能力)
- [3. 三阶段可用性模型](#3-三阶段可用性模型)
- [4. 项目结构](#4-项目结构)
- [5. 快速开始](#5-快速开始)
- [6. Routing / Retrieval Gate](#6-routing--retrieval-gate)
- [7. Hot Document Lane](#7-hot-document-lane)
- [8. 多模态 Evidence](#8-多模态-evidence)
- [9. rag/qmd 与 QMD](#9-ragqmd-与-qmd)
- [10. 正式 Company RAG](#10-正式-company-rag)
- [11. Core / Evidence / Hot 的职责](#11-core--evidence--hot-的职责)
- [12. 评测](#12-评测)
- [13. 当前边界](#13-当前边界)
- [14. 文档索引](#14-文档索引)
- [15. License](#15-license)

## 1. 这是什么

这是一个面向企业知识库的 RAG 工程实现，重点解决三类问题：

1. **上传即问**：员工在飞书等入口刚上传 DOCX、PPTX、PDF、XLSX 或图片，不应该等 Wiki 编译和 QMD embedding 完成才能提问。
2. **长期知识资产**：原始文件需要经过 LLM-Wiki 编译，形成 Entity、Concept、Source 和 Wikilink 页面，再投影到 QMD 的 `company-core` 与 `company-evidence` 两个 collection。
3. **可追溯的回答**：每个命中块都要能反查到原始文件、页码、幻灯片、段落、表格或视觉资产，而不是只给一段不可核验的摘要。

项目使用本地 QMD 作为 Hybrid Retrieval Kernel，使用外部云端 rerank 做候选重排，使用可选的 OCR / Vision API 补全图片、扫描页、流程图和图表语义；没有配置视觉 API 时，系统会保留 warning/pending，不会伪造视觉文本。

### 集成定位

- **核心 RAG**：`rag_gateway/` + QMD + `scripts/rag_ask.py` + sidecar `POST /ask`，不依赖 Hermes。
- **Hermes 参考集成**：`skills/` 和 SOUL 规则是可选适配层；Hermes 客户端可以对刚上传的单文件优先使用原生 `read_file` / `vision_analyze`，这是客户端侧快捷方式，不是核心路由逻辑。
- **其他 Agent / 自建服务**：通过 CLI 或 HTTP 调用同一套检索；没有 Hermes 时，`hot-document` 路由由 `HotDocumentStore` 返回抽取后的上下文，视觉资产由调用方自行处理。
- **视觉契约**：Hot 响应中的 `hermes_vision` 是历史命名，语义是“视觉资产 + 提示”；其他 Agent 可把 `assets` 映射到自己的视觉工具；不使用视觉能力时，Native Text 路径仍然可用。

### 使用模式

先区分三种使用方式，不要假设“文件放进 raw 就会自动编译”：

| 模式 | 上传 / 入库 | 编译与 RAG 刷新 | 查询 |
|---|---|---|---|
| **Hermes 端到端** | 上传 skill：raw + Hot Ingest + `append_pending` | 后台 cron `company-wiki-compile` 异步编译；编译第 6 步调用 `rag_refresh.py` | Agent 自动调用 `rag_ask.py` 或使用原生读取 |
| **其他 Agent 独立接入** | 你的上传处理必须调用 `rag_hot_ingest.py` + `append_pending.py` | 你的调度器必须实现等价的 LLM-Wiki 编译；公开仓提供 schema、pending 和 refresh 工具，不包含常驻自动 watcher 或 Hermes compile skill | 调用 `rag_ask.py` 或 `POST /ask` |
| **运维 / 手动** | 手动执行命令 | 手动运行编译、`update_hashes.py`、`rag_refresh.py` | 手动运行 `rag_ask.py` |

关键区别：

- **上传文件本身不会自动编译**。Hermes 的上传 skill 只负责存 raw、做 Hot Ingest、把文件加入 `.pending`；真正的 Wiki 编译是后台异步任务。
- 编译完成并执行 `rag_refresh.py` 之后，正式 QMD / Graph RAG 才能检索到新内容；在此之前，Hot 路径可以先回答单文件问题。
- 非 Hermes 部署没有 Hermes cron，也没有 SOUL 规则；需要自己的上传接口、编译调度和查询客户端，不能只把文件复制进 `raw/`。

另外，**Wiki 编译本身不是一条 `python compile.py` 命令**，而是一个按 `schema/SCHEMA.md` 生成/更新页面的 LLM-Wiki agent 流程。Hermes 用 `company-wiki-compile` skill 执行；该 skill 不随公开仓发布。其他 Agent 需要复用同样流程或实现等价编译器。编译后的 `rag_refresh.py` 才是确定性的投影 + QMD 更新脚本。

### Hermes 端到端上传流程

在 Hermes 中使用时，员工只需要上传文件并继续提问：

```text
员工在 Hermes 上传文件
  ↓
company-wiki-upload skill 自动执行：
  1. 保存 raw
  2. rag_hot_ingest.py 建立 Hot 视图
  3. append_pending.py 写入待编译队列
  ↓
员工可以立刻问这份文件（Hot / 原生 read_file）
  ↓
后台 cron `company-wiki-compile` 自动检测 pending：
  4. 编译 Wiki
  5. update_hashes.py
  6. rag_refresh.py（projection + QMD update/embed）
  ↓
正式 Company RAG 可检索
```

注意：

- **上传 skill 不会在上传请求里直接跑完整编译**，只负责 raw、Hot 和 pending；
- 真正的 Wiki 编译由后台 cron 异步执行；员工不需要运行任何编译命令；
- 如果 cron 未启用，pending 不会被自动处理，这是部署前提；
- 其他 Agent 不会自动获得这套上传/编译流程，需要自己实现上传 handler 和编译调度。

## 2. 核心能力

- `raw/ → LLM-Wiki → Entity / Concept / Source / Wikilink` 的长期知识编译链路。
- **Hot Document Lane**：刚上传的 Office / PDF / 文本 / 图片无需等待 Wiki Compile 或 QMD embedding，结构化抽取后可立即问答。
- **Retrieval Gate**：统一判断 `direct / hot-document / hot+company / company-rag`，不再只靠 Agent Prompt 猜是否需要 RAG；路由本身不绑定 Hermes，任何调用方都可以使用。
- **QMD Hybrid Retrieval**：BM25、Vector、RRF 和 Rerank；正式索引落在 QMD 的 SQLite 中。
- **Chapter Tree / Breadcrumb / Parent-Child**：保留 Wiki 页面的层级结构，按章节和父子关系补上下文。
- **Wikilink Graph Retrieval**：1-hop 邻居页 → 受限检索 → 邻居页中最相关的 section。
- **Raw Source Evidence**：Wiki 页面关联的 raw source 通过受限检索取最相关 chunk，而不是固定取第一段。
- **Adaptive Context Builder**：`diversity / balanced / source-focused` 三种上下文策略；单文档深挖时允许更多同源 chunk 和相邻 chunk。
- **多模态 Evidence Extraction**：Native Text + OCR + Vision API 三路合并；图片、扫描页、流程图、SmartArt、图表和截图会转成 EvidenceBlock，写入 `rag/qmd/evidence` 并由 QMD 索引。
- **双视觉路径**：Hot Document 的即时视觉问题可以由调用方 Agent 处理（Hermes 参考实现使用内置 Vision）；后台 full ingestion 才调用外部视觉模型生成可缓存、可审计的长期 `visual_semantics`。
- **可复现的检索评测**：页覆盖、金标覆盖、MRR、入选率、SHR、延迟和可选的答案质量抽查；方法与核心实验见 [docs/EVALUATION.md](docs/EVALUATION.md)。

## 3. 三阶段可用性模型

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

**关键点：编译前不是“没有 RAG”。** Hot Document Lane 是针对单个刚上传文件的临时原文检索层；长期 QMD / Graph RAG 在知识编译完成后接管。

## 4. 项目结构

```text
wiki_rag/
├── rag_gateway/
│   ├── evidence.py              # Native text / table / source block 抽取与 chunking
│   ├── multimodal.py            # 视觉资产提取、渲染、OCR、Vision API
│   ├── hot.py                   # Hot Document ingest / immediate local retrieval
│   ├── gating.py                # direct / hot / hot+company / company-rag 路由
│   ├── gateway.py               # Agent-facing unified Knowledge Gateway
│   ├── projection.py            # Wiki / raw → RAG Projection
│   ├── markdown_tree.py         # Chapter Tree
│   ├── qmd_client.py            # shared QMD daemon client
│   ├── router.py                # RAG 深度 + Context policy
│   ├── graph.py                 # Wiki Graph
│   └── search.py                # QMD RAG orchestration + context builder
├── scripts/
│   ├── rag_hot_ingest.py        # 上传后立即执行
│   ├── rag_ask.py               # 统一查询入口
│   ├── rag_build.py
│   ├── rag_refresh.py
│   ├── rag_search.py            # 正式 Company RAG 的低层入口
│   ├── rag_multimodal_status.py # OCR / Vision / LibreOffice 状态
│   ├── project_validate.py
│   └── qmd_bootstrap.sh
├── config/
│   └── rag.yaml                 # 检索模式、candidate、context、rerank、lex_leg 等
├── bench/                       # 评测执行器（不含题集、金标和结果）
├── docs/                        # 架构、检索、部署、评测、BYO corpus
├── schema/                      # Wiki / RAG schema
├── deploy/                      # systemd 等部署文件
├── skills/                      # Hermes 参考适配器（可选）
└── tests/                       # 单元测试
```

以下目录是**运行时数据目录**，不随公开仓发布：

```text
raw/                             # 原始事实文件，最终 Source of Truth
wiki/                            # LLM-Wiki 知识页面
rag/
├── extracted/                   # Native / OCR / Vision 合并后的证据层
├── assets/                      # 从 PDF / Office / 图片物化的视觉资产
├── visual_cache/                # OCR / Vision 结果缓存
├── hot/                         # 最近上传文件的即时检索层
├── qmd/core/                    # Wiki Chapter-aware projection
├── qmd/evidence/                # raw evidence structural chunks
├── manifest.json                # node / parent / child / source locator 映射
├── graph.json                   # Wikilink page graph
├── lexicon.json                 # 企业术语
└── traces/                      # 查询 trace
.qmd/                            # QMD 配置与索引
bench/dataset/                   # 评测题集与金标
bench/results/                   # 评测结果
```

## 5. 快速开始

> 这一节是部署、接入和运维命令，不是员工日常操作。Hermes 端到端模式下，员工只需要上传文件并提问；下面的 `rag_hot_ingest.py` / `rag_ask.py` 由上传 skill 和 Agent 调用。

### 5.1 环境要求

- Python 3.10+
- QMD CLI 与本地 QMD daemon（默认 `127.0.0.1:8181`）
- 可选：PaddleOCR、LibreOffice、云端 rerank / Vision API Key
- 可选：`requirements-vision.txt` 中的视觉依赖

### 5.2 安装

```bash
git clone https://github.com/lihuiw03-afk/wiki_rag.git
cd wiki_rag

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 可选：OCR / Vision
pip install -r requirements-vision.txt
```

QMD 是外部依赖，不在 Python requirements 中；安装并初始化 QMD 后，再执行：

```bash
bash scripts/qmd_bootstrap.sh
```

### 5.3 准备自己的知识库

公开仓不带语料，需要先准备：

```text
raw/        原始 DOCX / PPTX / PDF / XLSX / 图片 / 文本
wiki/       LLM-Wiki 编译产物
rag/        Projection、manifest、QMD 语料
```

然后在有数据的环境中执行：

```bash
python3 scripts/project_validate.py
```

### 5.4 查询

正式知识库：

```bash
python3 scripts/rag_ask.py "示例公司A文化建设有哪些问题？" \
  --profile main --json
```

模拟上传即问：

```bash
python3 scripts/rag_hot_ingest.py raw/interviews/example.docx --profile main

python3 scripts/rag_ask.py "这份访谈主要讲了什么？" \
  --attachment "$PWD/raw/interviews/example.docx" \
  --profile main --json
```

Windows 下把 `python3` 换成本项目 venv 的 `python.exe`，把 POSIX 路径换成本机路径。

### 5.5 HTTP / 其他 Agent

sidecar 可以直接被其他 Agent 或服务调用：

```bash
curl -X POST http://127.0.0.1:8190/ask \
  -H 'Content-Type: application/json' \
  -d '{"query":"这份文件有哪些要求？","attachment":"/path/to/raw/interviews/example.docx","route":"auto"}'
```

核心响应字段：

- `route`：`direct / hot-document / hot+company / company-rag`
- `context`：已带 `[S#]` 引用的上下文
- `selected`：命中块和 source locator
- `requires_vision` / `hermes_vision`：需要调用方补充视觉观察时返回；`hermes_vision` 是兼容命名，其他 Agent 可读取其中的 `assets`
- `answer_policy`：给调用方的回答约束，不绑定某个 Agent 框架

如果不使用 Hermes，可以直接把 `context` 和 `selected` 交给自己的生成层；视觉字段交给自己的 OCR / Vision 工具，或者只使用 Native Text 路径。

## 6. Routing / Retrieval Gate

任何 Agent 或服务都可以调用统一入口：

```bash
python3 scripts/rag_ask.py "<用户问题>" --profile "$PROFILE" --json
```

路由规则：

| 路由 | 核心行为 | Hermes 参考客户端 |
|---|---|---|
| `direct` | 返回空上下文，由调用方自行回答 | 按普通对话回答 |
| `hot-document` | `HotDocumentStore` 返回刚上传/最近文档的抽取上下文 | 可以对单文件优先使用原生 `read_file` / `vision_analyze`，这是客户端快捷方式 |
| `hot+company` | Hot 上下文 + 正式 Company RAG 上下文合并 | Hermes 可同时使用原生读文件和 RAG |
| `company-rag` | 走正式 QMD / Evidence / Context Builder 链路 | Hermes 使用正式 RAG 上下文回答 |

需要特别区分：

- **Gate 只负责决定路由**。它本身不调用 Hermes 的 `read_file`，也不依赖 Hermes。
- `hot-document` 在核心实现中是 `HotDocumentStore.search()`；Hermes 的“原生读文件优先”是 `skills/` 或 SOUL 层的适配策略，适用于“刚上传的单文件、立即追问”的客户端场景。
- 不使用 Hermes 时，`hot-document` 不会失效；它会走核心 Hot 路径，返回抽取后的上下文和 `selected` 块。

当前附件必须显式传路径。CLI 和 HTTP 没有“当前聊天附件”这种环境状态；`--attachment` / JSON 的 `attachment` 字段就是告诉 Gateway：这次问题要针对哪个已落盘的 raw 文件。Hermes 上传 skill 知道保存后的 raw 路径，所以由它传入；其他 Agent 也必须传同一个路径。

不传路径时，只有在“最近有 Hot 文档 + 问题里出现这份/刚上传/原文等关键词”时才可能命中最近文件；多文件、并发或需要确定性的场景应始终显式传路径。

```bash
python3 scripts/rag_ask.py "这份访谈的核心问题是什么？" \
  --attachment "/path/to/raw/interviews/xxx.docx" \
  --profile "$PROFILE" --json
```

也可以强制指定路由：

```bash
python3 scripts/rag_ask.py "这份文件有哪些要求？" \
  --attachment "/path/to/raw/interviews/xxx.docx" \
  --route hot-document --json
```

## 7. Hot Document Lane

核心入口（不依赖 Hermes）：

```bash
python3 scripts/rag_hot_ingest.py /path/to/raw/.../file.docx --profile "$PROFILE"
```

Hot Ingest 的行为：

1. 不等待后台 Compile，也不要求 QMD vector embedding。
2. 先做 Native Text、表格和 source locator 抽取。
3. 对图片、扫描页和视觉页面只做**有预算的视觉资产物化/渲染**，不调用 OCR / Vision API；调用方 Agent 可以查看与当前问题最相关的图片或页面。
4. 小文档如果能放进 Context Budget，直接使用完整 Native Text；如果问题依赖视觉内容，响应同时返回 `hermes_vision.assets`（兼容命名）供调用方处理。
5. 大文档才做结构感知的 local retrieval 和邻接 chunk 补全。
6. Hot 视觉资产统一标记为 deferred；后台 full `rag_refresh` 再执行 OCR + Vision API，生成可长期索引的多模态 evidence。

### 7.1 Hermes 与非 Hermes 客户端

- **Hermes 参考客户端**：刚上传单文件、立即追问时，可以在 `skills/` / SOUL 层优先使用原生 `read_file` 或 `vision_analyze`；这不改变核心 Hot 路由。
- **其他 Agent 或自建服务**：调用 `rag_hot_ingest.py`，再调用 `rag_ask.py` 或 `POST /ask`；`hot-document` 路由会返回 Hot 上下文。
- 如果调用方没有视觉能力：仍可使用 Native Text 和 `selected` 块；当 `requires_vision=true` 时，应明确告知用户当前视觉内容尚未解析，或接入自己的 OCR / Vision。
- `hermes_vision` 是历史兼容字段，不是强制依赖；其他 Agent 读取 `assets` 即可映射到自己的视觉工具。

这条通道主要解决：员工在飞书刚发一个文件，马上问“里面有哪些要求？”的真实场景；在没有 Hermes 的 Agent 里同样成立。

## 8. 多模态 Evidence

`raw/` 永远保留原件。V2.4 的“最大程度保留”不仅指文本，还包括把视觉信息转成可检索证据：

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

不同格式的处理：

- **DOCX**：Heading、段落、列表、表格、页眉页脚；提取 inline images；LibreOffice 可用时渲染页面，补 SmartArt / Drawing Canvas / 复杂版式。
- **PPTX**：文本框、表格、可读图表数据、备注、嵌入图片；同时把整张 slide 渲染给 Vision，恢复箭头、分组、战略屋、流程图和空间关系。
- **PDF**：保留原文本 block；无文本扫描页、含图片页、含较多 vector drawings 的页面会渲染后 OCR + Vision。
- **XLSX**：单元格、公式、缓存值、嵌入图片；可选页面级渲染。
- **PNG / JPG / WebP / TIFF**：图片本身是一等 raw source，直接 OCR + Vision。

后台 OCR 生成 `visual_ocr` block，后台 Vision API 生成 `visual_semantics` block。两者都带 `source_locator / visual_asset / sha256 / provider`，并与 Native block 一起切片，所以视觉内容最终真实存在于 `rag/qmd/evidence/*.md`，不是只保存一张图片路径。

例如流程图会形成类似：

```markdown
## OCR 识别文本
步骤甲 / 步骤乙 / 步骤丙 / 步骤丁

## 视觉语义解析
流程为：步骤甲 → 步骤乙；条件成立进入步骤丙，否则返回步骤甲；完成后结束。
```

`rag/assets/` 保存 Hot 客户端视觉与后台 OCR / API Vision 可复用的图片和页面渲染；`rag/visual_cache/` 按图片 sha 和模型设置缓存后台分析结果，避免每次 `rag_refresh` 重复跑视觉模型。详见 [docs/MULTIMODAL_EVIDENCE_PIPELINE.md](docs/MULTIMODAL_EVIDENCE_PIPELINE.md)。

切片原则：

> **先保留原子 EvidenceBlock，再组合 chunk；绝不为了 chunk 大小静默删除 Native / OCR / Vision block。**

每个 evidence chunk 在 `manifest.json` 中保存 `source_locators / source_block_ids / source_kinds / visual_assets / extracted_source`，因此可以从 QMD 命中反向定位到具体 PDF 页、PPT slide、DOCX 段落/图片和视觉资产。

## 9. rag/qmd 与 QMD

`rag/qmd/core` 和 `rag/qmd/evidence` 是 QMD 的输入语料；执行 `qmd update / qmd embed` 后，QMD 将全文与向量索引写入自己的 SQLite（默认 `~/.cache/qmd/index.sqlite`）。

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

所以 QMD 查 SQLite，不是查询时扫描 `rag/qmd/`；`rag/qmd/` 是可重建的搜索语料和最终 Context 内容源。

## 10. 正式 Company RAG

```text
Query
  ↓
QMD core + evidence
  ↓
BM25 + Vector → RRF → Rerank
  ↓
Primary Candidates
  ├─ Parent / Child
  ├─ Graph neighbor → within-page restricted retrieval → best section
  └─ linked raw source → restricted retrieval → best evidence chunk
  ↓
多实体问句：为每个实体追加独立 vec 腿
  ↓
Adaptive Context Builder
  ├─ diversity
  ├─ balanced
  └─ source-focused
  ↓
[S1] ... [Sn]
  ↓
Agent grounded answer
```

补充说明：

- 多实体比较类问句在 QMD 检索阶段为每个实体追加一条独立 `vec` 检索腿；如果某个实体页在重排后仍然缺席，再由受限 QMD 做 section 兜底注入。
- 云端 rerank 负责排序已有候选；结构扩展负责补父章节、子章节、图邻居和关联原始证据。
- Context Builder 只负责在预算内选择和组织块，不再做无界的上下文拼接。

## 11. Core / Evidence / Hot 的职责

| 层 | 内容 | 主要用途 |
|---|---|---|
| `company-core` | LLM-Wiki 高密度知识 | 概念、实体、方法论、结论 |
| `company-evidence` | 长期 raw 原始证据 | 原话、数字、表格、细节、出处 |
| `rag/hot` | 尚未完成 Wiki/QMD 编译的最近上传文件 | 上传即问、单文档即时检索 |

它们不是三套重复知识库，而是三个生命周期阶段和信息层次。

## 12. 评测

仓库自带可复现的检索评测执行器（`bench/`），指标定义、核心实验和复现方式见 [docs/EVALUATION.md](docs/EVALUATION.md)；如何接自己的语料见 [docs/BYO_CORPUS.md](docs/BYO_CORPUS.md)。

公开仓**不附带真实语料、题集、金标或结果文件**，下表是聚合历史结果，不是开箱即跑的基准；用自己的语料复测后才可横向比较。

| 集合 | page@5 | page@10 | gold@5 | gold@10 | MRR | sel_ratio |
|---|---:|---:|---:|---:|---:|---:|
| 58 自然语言 | .911 | .974 | .911 | .974 | .837 | .983 |
| hard50 | .770 | .790 | .870 | .910 | .803 | .960 |
| 24 字面串 | — | — | .875 | — | — | — |

- 推荐配置：`lex_leg=conditional` + `budget_policy=density`；balanced 20 / deep 16，balanced context 12000。
- 延迟 p50 ≈ 1.22s（含云端重排）。
- 收紧候选 / 上下文后的单轮复核：58 页/金标 `.9195 / .9741`；hard50 页 `.780 / .790`、金标 `.900 / .910`（与上表不是同批）。
- 答案质量（58 案、同一 answer / judge 模型）：Faithfulness `4.879`、Correctness `3.517`（1–5）；这是次级检查，不声明答案层优势。
- 公开仓**不包含外部基线对比实验或对比指标**。

评测指标：

- `page@K`：目标 Wiki 页是否进入 top-K。
- `gold@K`：目标页或目标 raw 是否进入 top-K。
- `MRR`：第一个正确结果的排名倒数均值。
- `sel_ratio`：最终上下文中命中目标的比例。
- `SHR`：池内命中时的最终入选率。
- `latency`：查询 p50 / p95。
- `answer quality`：可选的 1–5 分 LLM 裁判，次级检查。

## 13. 当前边界

- **单租户假设**：当前默认单租户，所有员工共享知识权限；`profile` 只用于 trace 和“最近上传文件”关联，不做 ACL。
- **OCR / Vision 可选**：API 未配置或失败时，系统保留 warning / pending，不会伪造视觉文本。
- **Hot 与长期索引分离**：核心 Hot Document 不调用外部 Vision API；视觉观察由调用方处理。完整 `rag_refresh.py` 才负责 OCR + API Vision 的长期 evidence。
- **DOCX 复杂结构**：脚注、批注、修订历史仍可能需要更专门的 OOXML extractor；原始文件始终保留。
- **不是公开 benchmark**：评测是单语料、小样本；公开仓不带数据，不能把 README 数字当作可复现基准。
- **无外部基线对照**：本项目不发布“普通 RAG vs 本项目”的指标或结论。
- **QMD 外部依赖**：QMD CLI / daemon 需要单独安装，公开仓不包含二进制和索引。

## 14. 文档索引

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — 整体架构与数据流
- [docs/RETRIEVAL_PIPELINE.md](docs/RETRIEVAL_PIPELINE.md) — 检索、扩展与 Context 细节
- [docs/MULTIMODAL_EVIDENCE_PIPELINE.md](docs/MULTIMODAL_EVIDENCE_PIPELINE.md) — OCR / Vision / Evidence
- [docs/HOT_DOCUMENT_PIPELINE.md](docs/HOT_DOCUMENT_PIPELINE.md) — 上传即问
- [docs/EVALUATION.md](docs/EVALUATION.md) — 指标、核心实验、复现
- [docs/BYO_CORPUS.md](docs/BYO_CORPUS.md) — 用自己的语料跑评测
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — 部署
- [docs/INGEST_STATUS.md](docs/INGEST_STATUS.md) — 入库进度状态机
- [docs/V2_1_OPTIMIZATION.md](docs/V2_1_OPTIMIZATION.md) — 检索编排与成本优化
- [bench/README.md](bench/README.md) — 评测脚本索引

## 15. License

当前仓库尚未添加 LICENSE，默认保留所有权利。若希望允许复制、修改和再分发，请补充 MIT 或 Apache-2.0 等许可证后再发布。
