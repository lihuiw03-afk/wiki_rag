# Hot Document & Retrieval Gate — V2.4

## 1. 目标

员工在飞书/微信上传文件后通常会立即追问。Hot Document Lane 的目标是：**不等待 LLM-Wiki Compile、QMD embedding 或外部 Vision API，就让当前附件马上可问。**

```text
上传附件
  ├─→ Hot: Native Text + 视觉资产物化 → Hermes Vision → 立即回答
  └─→ Background: LLM-Wiki + OCR + Vision API + QMD → 长期知识库
```

## 2. Hot ingest 做什么

`rag_hot_ingest.py` 只做低延迟、确定性工作：

- 原生文本/表格/标题/页码等结构化抽取；
- 把扫描 PDF 页、PPT slide、DOCX 页面、嵌入图片等物化到 `rag/assets/`；
- 保存 `source_locator / breadcrumb / sha256 / visual_asset path`；
- 生成 `rag/hot/<doc_id>/source.md` 与结构化 chunks；
- **不调用后台 OCR，不调用外部 Vision API。**

因此外部 API 网络抖动、限流或超时不会阻塞“上传后立刻问”。

## 3. Hot 视觉问题怎么回答

`rag_ask.py --attachment ...` 先返回 Native Text Context。若当前问题涉及图片/扫描页/流程图/图表，或者当前选中 chunk 本身包含视觉 gap，返回结果会附带：

```json
{
  "hermes_vision": {
    "required": true,
    "strategy": "hermes-native",
    "assets": [
      {
        "path": "/home/demo/company-wiki/rag/assets/.../page-0008.png",
        "source_locator": "pdf:page:8"
      }
    ]
  }
}
```

Hermes Skill 随后用**Hermes 内置 Vision**查看这些资产，再把视觉观察与 `[S#]` 文本证据合并回答。Hot 路径禁止调用后台 Vision API。

视觉资产选择不是无脑把整个 100 页 PDF 都交给 Vision：

- 用户明确说“第 8 页/第 5 张幻灯片”时优先定位指定页；
- 文本检索已经命中某页时优先选择同页视觉资产；
- 用户明确问“流程图/图片/图表/箭头”等时启用视觉资产；
- 扫描件几乎没有 Native Text 时自动要求 Hermes Vision；
- 每轮默认最多选择少量视觉资产，受 `max_hermes_vision_assets_per_query` 控制。

## 4. 为什么 Hot 不把 Hermes Vision 结果写进长期 evidence

Hermes Vision 是**当前会话即时观察**，适合低延迟回答；长期知识库需要更稳定的缓存、模型版本、可重复构建和 provenance。

因此：

```text
Hermes Vision output
→ 当前回答
→ 不直接污染长期 QMD index

Background Vision API output
→ visual_semantics EvidenceBlock
→ rag/qmd/evidence
→ QMD SQLite
```

这样把“会话临时观察”和“长期知识资产”解耦。

## 5. Retrieval Gate

```text
Query
  ├─ 普通改写/翻译/代码 → direct
  ├─ 当前/最近附件 → hot-document
  ├─ 当前附件 + 历史知识对比 → hot+company
  └─ 公司实体/项目/内部知识 → company-rag
```

`hot+company` 同样会把 `hermes_vision` 请求透传给 Hermes，同时 Company RAG 走正式 QMD。

## 6. 生命周期

```text
T0 上传
  → Hot Native Text Ready
  → Visual Assets Ready
  → Hermes Vision 可即时回答

后台
  → OCR
  → 外部 Vision API
  → rag/qmd/evidence
  → qmd update/embed
  → LLM-Wiki core
```

因此“刚上传就问”与“长期高质量检索”不再互相阻塞。
