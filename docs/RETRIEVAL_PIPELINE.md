# Retrieval Pipeline

## 1. RAG Projection

`wiki/*.md` 不直接为搜索而改写。`rag_build.py` 将每个 H1-H6 章节变成独立 Markdown 检索节点，并附加：

- Page Type / Page Title
- Breadcrumb
- Parent / Children
- Tags
- Wikilinks
- Raw Sources
- Deterministic section preview
- Search keywords

这相当于 Contextual Chunking，但上下文来自已经存在的 Wiki 结构，不需要额外 LLM 成本。

## 2. Raw Evidence Projection

原始 DOCX/PPTX/PDF 被确定性抽取后切成约 3.2k 字符、带 overlap 的 evidence chunk。Wiki 负责“知识密度”，Evidence 负责“长尾召回与事实核对”。

## 3. Adaptive Retrieval

### Fast

- `company-core`
- `lex` only
- candidate 12 / top 6
- no rerank
- no graph expansion
- 适合实体、编号、定义、文件定位

### Balanced

- `company-core + company-evidence`
- `lex + vec`
- QMD RRF + rerank
- candidate 30 / top 10
- hierarchy + graph section retrieval + raw evidence support

### Deep

- `company-core + company-evidence`
- `lex + vec`，可选 HyDE
- candidate 40 / top 14
- 适合跨文档综合、比较和分析

所有员工都拥有完整知识库访问权限；`profile` 仅作为调用者身份写入 trace，不参与 collection 过滤。

## 4. 为什么 HyDE 默认不是必选

HyDE 增强语义召回，但对企业内部缩写、人名、专有术语和精确事实可能引入 query drift。因此默认 Balanced 使用显式 lex+vec；Deep 只有当 Hermes 判断为抽象/答案型问题时才传 `--hyde`。

## 5. Structural Augmentation

QMD rerank 后才进行结构增强：

### 5.1 Parent / Child

- 高分 child → parent context
- 高分 parent → 最多 2 个 children

### 5.2 Graph Neighbor → Best Section

不再把 Wikilink 邻居页的 root node 直接塞进 Context。

```text
QMD high-confidence Wiki hit
        ↓
1-hop graph neighbor pages
        ↓
restricted QMD pass on company-core
        ↓
filter to candidate neighbor pages
        ↓
best section per neighbor page
        ↓
wiki-graph-section
```

当前 QMD 主要支持 collection 作用域，Gateway 因此采用“辅助检索 + 目标 page 过滤”。如果目标页未进入 QMD 返回窗口，再使用轻量本地 CJK/Latin lexical scorer 做兜底，保证图扩展不会退回盲目 root 注入。

### 5.3 Source Evidence → Best Chunk

Wiki 页 frontmatter 中的 `raw_sources` 只负责 provenance，不再默认取关联 source 的第一个 chunk。

```text
high-confidence Wiki hit
        ↓
linked raw sources
        ↓
restricted QMD pass on company-evidence
        ↓
filter to linked sources
        ↓
best matching evidence chunk
        ↓
source-evidence-restricted
```

如果该 raw source 已经由主 QMD 检索直接命中，则直接复用主检索结果，避免不必要的辅助调用。

## 6. Adaptive Context Builder

Context Builder 不再固定“每个 raw source 最多 2 chunk”，而是先判断当前问题需要哪种上下文策略。

### diversity

适合：`综合不同访谈`、`比较多个项目`、`不同管理层共同观点`。

- 每个 raw source 最多 2 chunk
- 每个 Wiki page 最多 2 section
- 强 repeat penalty
- 不做 evidence neighbor expansion
- 目标：覆盖更多不同来源

### balanced

适合普通知识问答。

- 每个 raw source 最多 3 chunk
- 每个 Wiki page 最多 3 section
- core/evidence 都保留一定预算
- 对最强 evidence hit 可补少量相邻 chunk

### source-focused

适合：`详细总结这份资料`、`原文怎么说`、`这份报告具体列了哪些内容`。

- 每个 raw source 最多 8 chunk
- evidence reserve 提高到 6
- 同源 repeat penalty = 1.0
- 对 top evidence hit 自动补前后相邻 chunk
- Context budget 提升到 24k chars
- 目标：完整深挖单一来源，而不是强行多样化

Context Builder 使用“相关度 × 重复来源惩罚”的 greedy selection，并同时受 hard cap 与总字符预算约束。

## 7. Evidence Neighbor Expansion

Raw chunk 是检索单元，但答案可能跨越连续 chunk。因此在 `balanced/source-focused` 中：

```text
QMD hit: chunk 7
        ↓
chunk 6 + chunk 7 + chunk 8
```

相邻 chunk 的 score 会折扣，只作为上下文补全，不会取代原始高分命中。`diversity` 模式关闭该行为，避免同一 source 占满 Context。

## 8. Final Context

最终 Context：

- 先选择候选，再按 score 排序展示
- 每项生成本轮局部 citation `[S1]...[Sn]`
- `[S#]` 只对当前 Query 有效，不是全局文档 ID
- 总字符预算由 context policy 动态决定

生成模型只允许依据 `[S#]` 回答；证据不足时明确说明。

## V2.4 pre-retrieval gate

Before QMD, the unified gateway selects one route:

```text
current attachment? ─ yes ─┬─ cross-corpus comparison → hot+company
                            └─ otherwise → hot-document
no attachment ─┬─ recent-doc follow-up → hot-document
               ├─ internal entity/term/project intent → company-rag
               └─ generic transformation/generation → direct
```

This avoids paying vector/rerank cost for tasks that do not need company knowledge, while eliminating the compilation-lag gap after uploads.

## Hot document retrieval

A small uploaded document that fits the configured context budget is used as full normalized text. For a large document, local retrieval operates over structural chunks and adds neighboring chunks. Broad summary questions sample/cover the document rather than overfitting to lexical hotspots.

This path does not claim vector-search quality; its engineering goal is immediate availability and very high single-document recall until formal QMD indexing is available.


## V2.4 evidence ingestion before retrieval

正式 retrieval 之前，`company-evidence` 的构建已经完成多模态转换：

```text
Native text ───────┐
OCR text ──────────┼→ structural chunks → company-evidence → QMD SQLite
Vision semantics ──┘
```

因此用户问“这个流程图审核失败后走哪里”时，QMD 搜到的是流程图的 `visual_semantics` 文本证据，而不是等最终回答阶段再临时看图片。对于刚上传文件，Hot Lane 使用有预算的 multimodal enrichment；长期 `rag_refresh` 使用 full enrichment。


## Hot visual branch

Hot Document 搜索返回文本 `[S#]` 之外，还可能返回 `hermes_vision.assets`。这些资产不经过 QMD：Hermes 在当前 turn 内用原生 Vision 查看，并与文本 Context 合并。长期 `company-evidence` 中的 `visual_semantics` 则由后台外部模型 API 生成。
