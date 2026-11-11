# Deployment

## 环境

- Linux
- Python 3.10+
- Node.js 22+
- QMD current release

## 1. 放置目录

目标路径建议保持：

```text
/home/demo/company-wiki/
```

## 2. Python 依赖

```bash
cd /home/demo/company-wiki
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 3. 初始化投影与 QMD

```bash
bash scripts/qmd_bootstrap.sh
```

脚本会：

1. 检查 Node.js 22+
2. 安装 QMD（若不存在）
3. 构建 `rag/` 投影
4. `qmd update`
5. `qmd embed`
6. 启动共享 `qmd mcp --http --daemon`

生产环境让所有 Hermes Profile 共用这个 daemon，不要每个 Profile 各启动一份模型。

## 4. 编译后的增量刷新

在现有后台 Compile Cron 完成 LLM-Wiki 编译之后增加：

```bash
python3 /home/demo/company-wiki/scripts/rag_refresh.py
```

Projection 会重建，但 QMD 自身 `update/embed` 是增量处理。当前 Wiki 规模很小，这种“确定性全投影 + 增量索引”的方案比维护复杂的局部依赖状态更可靠。

## 5. 搜索烟雾测试

```bash
python3 scripts/rag_search.py "示例公司A文化建设面临哪些核心问题？" --mode balanced --json
```

## 6. 中文 Embedding

`.qmd/index.yml` 默认将 embedding 改为 Qwen3-Embedding-0.6B，以提升 CJK/中文语义召回。机器内存/显存不足时可以删除 `models.embed` 配置恢复 QMD 默认模型；更换模型后必须 `qmd embed -f`。

## 7. 安全

QMD HTTP daemon 默认只绑定 localhost。不要直接把无认证的 `/mcp` 或 `/query` 暴露到外网。当前部署约定所有员工拥有完整知识库权限，因此 Gateway 不做 ACL 过滤；Profile 仅作为 trace caller identity。

## 8. V2.4 多模态 OCR / Vision 部署

### 8.1 OCR（长期 evidence）

推荐生产环境安装 PaddleOCR；未安装时可回退 Tesseract。Hot Document 不等待 OCR，因此 OCR 环境异常不会阻塞员工刚上传文件后的即时问答。

```bash
pip install -r requirements-vision.txt
# 可选 fallback
sudo apt install tesseract-ocr tesseract-ocr-chi-sim
```

### 8.2 Office 页面渲染

PPT SmartArt、箭头、组合形状和 DOCX Drawing Canvas 需要页面级渲染：

```bash
sudo apt install libreoffice
```

渲染后的图片统一进入 `rag/assets/`。Hot Hermes Vision 与后台 API Vision 都复用这些资产。

### 8.3 外部 Vision 模型 API（长期 evidence）

V2.4 不要求本机部署 VLM。后台 `rag_refresh.py` 通过多模态模型 API 生成 `visual_semantics`。当前 adapter 使用 OpenAI-compatible chat-completions 图像 schema。

不要把密钥写进仓库，部署时配置环境变量：

```bash
export COMPANY_WIKI_VISION_BASE_URL='https://<your-provider>/v1'
export COMPANY_WIKI_VISION_MODEL='<your-multimodal-model>'
export COMPANY_WIKI_VISION_API_KEY='<secret>'
```

然后检查：

```bash
python3 scripts/rag_multimodal_status.py
```

重点字段：

```text
vision_api_configured = true
hot_vision_strategy  = hermes_native
```

`probe_on_start` 默认关闭，因为不同托管供应商对 `/models` 的支持并不一致；真正 full ingestion 请求会验证连接和凭据。

### 8.4 Hot Document 使用 Hermes 内置 Vision

Hot 上传链路不调用外部 Vision API，也不等待 OCR。它只生成 Native Text 和视觉资产。

```bash
python3 scripts/rag_hot_ingest.py /path/to/new.pdf --profile "$PROFILE"
python3 scripts/rag_ask.py '第8页流程图是什么意思？' --attachment /path/to/new.pdf --profile "$PROFILE" --json
```

若返回：

```json
{
  "hermes_vision": {
    "required": true,
    "assets": [{"path": "/home/demo/company-wiki/rag/assets/.../page-0008.png"}]
  }
}
```

Hermes Skill 必须使用自身 Vision 查看这些 asset，再与 `[S#]` Native Text 合并回答。**不要从 Hot 路径调用后台 Vision API。**

### 8.5 后台长期刷新

```bash
python3 scripts/rag_refresh.py
```

Full refresh 执行：

```text
Native Text
+ OCR
+ external Vision API
→ rag/extracted
→ rag/qmd/evidence
→ qmd update/embed
→ index.sqlite
```

如果 API 暂时不可用，已有 Native/OCR evidence 仍保留，Vision 语义保持 unresolved；服务恢复后重新运行 refresh 会补全。
