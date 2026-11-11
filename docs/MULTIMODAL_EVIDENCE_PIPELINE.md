# Multimodal Evidence Pipeline — V2.4

V2.4 将视觉处理拆成两条路径：

1. **Hot / 当前会话**：Hermes 内置 Vision；
2. **Full / 长期索引**：OCR + 外部多模态模型 API。

二者共用 `rag/assets/` 和 source locator，但职责不同。

## 1. 总体链路

```text
raw/file.pdf|pptx|docx|xlsx|png
        │
        ├── Native extractor ───────────────────────────────┐
        │                                                   │
        └── Visual asset collector                          │
               │                                           │
               ├──────── HOT ───────→ Hermes native Vision │
               │                      (current answer only) │
               │                                           │
               └──────── FULL ──┬──→ OCR ── visual_ocr ───┤
                                └──→ Model API ─ visual_semantics
                                                        │
                                                        ▼
                                               merged EvidenceBlocks
                                                        │
                                                rag/extracted/
                                                        │
                                                rag/qmd/evidence/
                                                        │
                                                     QMD SQLite
```

## 2. OCR 与 Vision API 的职责

- OCR：恢复扫描页/截图中的可见文字；
- Vision API：恢复箭头、流程、空间布局、父子层级、图表趋势、组织架构等非文本语义。

最终长期 evidence 同时保留 `visual_ocr` 与 `visual_semantics`，并且二者都带精确 `source_locator`。

## 3. Visual Asset 层

视觉资产写到：

```text
rag/assets/<source-key>/...
```

常见 locator：

```text
pdf:page:17
pptx:slide:8
pptx:slide:8:shape:3
docx:p:31:image:1
docx:render:page:4
image:file
```

Hot Hermes Vision 和后台 API Vision 都使用这些统一资产，因此同一个视觉对象有稳定 provenance。

## 4. Full/background Vision API

后台 `rag_refresh.py` 使用 `mode=full`。Vision backend 通过**外部多模态模型 API**调用，不要求本机部署 VLM。当前适配层使用 OpenAI-compatible `chat/completions` 图像输入 schema。

配置只保存环境变量名，不建议把密钥写进仓库：

```yaml
vision:
  provider: model_api
  base_url: ""
  model: ""
  base_url_env: COMPANY_WIKI_VISION_BASE_URL
  model_env: COMPANY_WIKI_VISION_MODEL
  api_key_env: COMPANY_WIKI_VISION_API_KEY
```

部署时：

```bash
export COMPANY_WIKI_VISION_BASE_URL='https://<your-provider>/v1'
export COMPANY_WIKI_VISION_MODEL='<your-multimodal-model>'
export COMPANY_WIKI_VISION_API_KEY='<secret>'
```

不同供应商只要兼容该请求 schema 即可；若供应商协议不同，只需要新增一个 Vision backend adapter，不改 Evidence/Projection/QMD 主链路。

## 5. Hot Hermes Vision

Hot Ingest **不调用 OCR/API Vision**。它只负责把可能需要看的页/slide/image 渲染成稳定本地资产。`rag_ask.py` 根据当前 query 和已命中的文本位置挑选少量相关资产并返回 `hermes_vision.assets`。

Hermes 调用自身 Vision 后直接回答当前用户，不把这次即时视觉描述直接写进 `rag/qmd/evidence`。后台 full refresh 会用统一 API 模型重新生成正式、可缓存的长期 `visual_semantics`。

## 6. 文件格式

- PDF：保留文字层；扫描页/图片页/vector drawings 页面渲染为 PNG。
- PPTX：抽取文本/表格/图表数据/备注/嵌入图；使用 LibreOffice 整页渲染恢复 SmartArt、箭头和组合布局。
- DOCX：保留正文/表格/标题，同时抽取嵌入图片并可页面级渲染。
- XLSX：保留单元格/公式/缓存值，并抽取嵌入图片。
- PNG/JPG/WebP/TIFF：图片自身作为一等 raw source。

## 7. Cache

后台 OCR/API Vision 使用：

```text
rag/visual_cache/<hash>.json
```

Cache key 包含图片 SHA、OCR 配置和 API Vision provider/model。Hot Hermes Vision 不依赖该 cache，因为它属于当前会话即时推理。

## 8. 长期 Evidence 示例

```markdown
## OCR 识别文本
步骤甲
步骤乙
步骤丙
步骤丁

## 视觉语义解析
流程为：步骤甲 → 步骤乙；条件成立 → 步骤丙 → 结束；否则 → 返回修改。
```

这些内容进入 `rag/qmd/evidence` 后，BM25 可以利用 OCR 词面，Vector/Reranker 可以利用 Vision API 生成的关系语义。

## 9. 失败策略

- API Vision 未配置/失败：Native Text 和 OCR 仍可进入 evidence，Vision 部分保持 pending；
- OCR 失败：API Vision 仍可补视觉语义；
- Hot 阶段：不依赖上述服务，Hermes native Vision 仍可处理当前附件；
- `raw/` 永远是 Source of Truth，任何 OCR/Vision 输出都只是派生证据。
