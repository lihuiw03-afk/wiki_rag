---
name: company-wiki-upload
description: "员工收到文件时将其存入企业知识库 raw/ 目录并触发编译"
version: 2.4.3
author: Hermes Agent
---

# Company Wiki — 员工文件上传（Windows 单操作员模式）

> 本机路径约定：项目根 = `C:/Users/demo/Projects/cwr24`；Python 一律用项目 venv：
> `PY="C:/Users/demo/Projects/cwr24/.venv/Scripts/python.exe"`（以下 `"$PY"` 均指该解释器）。
> 上传可由 operator 等 profile 执行；编译统一由 Hermes cron 任务 `company-wiki-compile` 处理。

当员工通过飞书/桌面发送文件时，执行以下流程；附件默认自动入库。

## 触发条件

- **消息附带文件即自动入库**（PDF、Word、Excel、PPT、图片、文本、Markdown 等），不需要用户说“入库/上传”。
- 自动入库先于回答文件内容：先归档 + Hot Ingest + pending，再读文件回答。
- 只有用户明确说“这次不要入库 / 只是临时看看”时，才跳过入库。
- 不触发：普通文字消息、对话、闲聊、代码片段。

## ⛔ 绝对禁止（违反必错）

1. **上传流程不直接触发编译**：只需执行 `append_pending.py` 写入待编译队列；编译由 Hermes cron 任务 `company-wiki-compile` 自动检测并执行。不要在上传流程中手动跑编译（运维级手动触发请走 company-wiki-compile 技能）。
2. **允许且要求 Hot Multimodal Ingest**：上传后立即运行 `rag_hot_ingest.py`。它不等待 Wiki Compile/QMD embedding，只做 Native Text 抽取和视觉资产物化/渲染；Hot 路径**禁止调用外部 OCR/Vision API**。操作员马上问到图片/扫描页/流程图时，使用 Hermes 内置 Vision。后台 full `rag_refresh` 再做 OCR + 外部 Vision API 并写入长期 evidence。
3. **禁止安装工具**：缺少 tesseract/pandoc/python-docx 时不要 `pip install` 或 `apt install`。这不是上传流程的责任。

## 操作步骤

### ⓪ 查重（强制，避免重复入库）

先算上传文件的 sha256，并在 `raw/` 中查找同 hash 文件（排除 `.meta.json`）：

```bash
HASH=$(sha256sum "<上传文件>" | awk '{print $1}')
find C:/Users/demo/Projects/cwr24/raw -type f ! -name '*.meta.json' -exec sha256sum {} + | grep "^${HASH} "
```

- **找到同 hash**：复用已有 raw 路径，不复制、不重复建 meta；仍运行一次 Hot Ingest 刷新临时视图；如果该文件尚未编译（hash 不在 `.hashes.json` 或与记录不一致），对已有路径补一次 `append_pending.py`；然后直接回答文件内容。
- **没有找到**：继续 ①，正常归档。

重复上传同一份文件，不允许产生多个 raw 副本。

### ① 确定子目录

根据文件内容选择 raw/ 子目录：

| 文件内容 | 存储路径 |
|---------|---------|
| 访谈记录、会议纪要 | `raw/interviews/` |
| 调研报告、咨询方案、项目文档 | `raw/consulting/` |
| 行业报告、市场分析 | `raw/industry/` |
| 竞品资料、同行信息 | `raw/competitor/` |
| 图片、截图、附件 | `raw/assets/` |

不确定时默认存入 `raw/consulting/`。

### ② 保存文件

```bash
# 当前 profile（例如 operator；不要硬编码为 operator）
PROFILE="<当前 profile>"
# 文件名格式: {YYYYMMDD}_{profile}_{原文件名}
cp <上传文件> C:/Users/demo/Projects/cwr24/raw/{子目录}/$(date +%Y%m%d)_${PROFILE}_<原文件名>
```

### ③ 添加 frontmatter

文件是纯文本/markdown：在文件开头插入 frontmatter。
文件是 PDF/Word/图片：在同目录创建同名 `.meta.json` 文件。

Frontmatter 格式：
```yaml
---
uploaded_by: <profile_name>
uploaded_at: YYYY-MM-DD HH:MM
file_type: interview | report | industry | competitor | document
---
```

### ④ 建立 Hot Document Lane（立即可问）

```bash
"$PY" C:/Users/demo/Projects/cwr24/scripts/rag_hot_ingest.py \
  "C:/Users/demo/Projects/cwr24/raw/{子目录}/{文件名}" --profile "$PROFILE"
```

这一步建立刚上传文件的临时证据视图；**不等待 Wiki 编译，也不要求 QMD embedding 完成**。Native Text 立即抽取，扫描页/图片/流程图只物化为 `rag/assets` 中的可定位视觉资产，不调用后台 Vision API。小文档可直接完整进入文本上下文，大文档使用结构感知局部检索；视觉问题由 Hermes 内置 Vision 当场查看相关资产。

### ⑤ 通知用户并继续回答

```
✅ 文件已自动入库 → raw/{子目录}/{文件名}
后台编译已排队，完成后会追加通知。

你可以继续问这份文件的内容；我会先读原文回答。
```

**关键原则：**
- **附件默认自动入库，不先征求“是否入库”**；只有用户明确说“这次不要入库 / 只是临时看看”才跳过。
- 上传时执行有预算的 Hot Ingest；不要启动 LLM-Wiki 编译。Hot 只物化有限数量视觉资产，完整 OCR + API Vision 多模态投影交给后台 `rag_refresh`。
- 同一份文件按 sha256 去重，不重复复制 raw。
- 不要尝试安装工具、修复环境或进行 lengthy troubleshooting。
- 入库后如果用户问文件内容，优先用 Hermes 原生 `read_file` 直接读 raw 路径；原生读取失败、文件太大或需要跨文件/引用/知识库检索时，才使用 `rag_ask.py --attachment <刚上传路径>`。当返回 `hermes_vision.required=true` 时，必须使用 Hermes 原生 Vision 查看 `hermes_vision.assets`。

### ⑥ 触发编译

**安全写入待编译队列（带文件锁，防止并发丢任务）**：
```bash
"$PY" C:/Users/demo/Projects/cwr24/.scripts/append_pending.py "C:/Users/demo/Projects/cwr24/raw/{子目录}/{文件名}"
```
禁止使用裸 `echo >> .pending`，必须用此脚本确保原子写入。编译由 cron 自动接手（也可由操作员手动运行编译任务）。

`append_pending.py` 会自动创建任务状态；查看进度：

```bash
"$PY" C:/Users/demo/Projects/cwr24/scripts/kb_progress.py --json
```

### ⑦ 员工追问文件内容（原生读取优先）

如果员工在文件上传后立即追问内容：

- **不要等待编译完成。**
- **单文件内容问题优先使用 Hermes 原生读取**：对第 ② 步保存后的 raw 路径直接调用 `read_file`，把文件正文加入当前会话上下文；图片/扫描页用 `vision_analyze` 查看 raw 文件或 `rag/assets` 里对应的视觉资产。原生读取不依赖 Hot/QMD/编译，也不需要先走 Knowledge Gateway。
- 只有以下情况才 fallback 到统一 Knowledge Gateway：
  - 原生读取失败、格式不支持或文件太大；
  - 问题需要跨文件、历史项目、公司知识库检索；
  - 需要稳定引用、原文定位、Hot chunk 局部检索或视觉资产清单。
- fallback 时把刚上传文件路径显式传入：
```bash
"$PY" C:/Users/demo/Projects/cwr24/scripts/rag_ask.py \
  "<用户问题>" --attachment "C:/Users/demo/Projects/cwr24/raw/{子目录}/{文件名}" \
  --profile "$PROFILE" --json
```
- Gateway 会自动选择 `hot-document`；若用户要求“和之前项目/历史知识对比”，自动选择 `hot+company`。
- Hot Ingest 不生成 OCR/API-Vision evidence。若 `rag_ask.py` 返回 `hermes_vision.required=true`，直接用 Hermes 原生 Vision 查看给出的视觉资产；无需等待后台 OCR/API Vision。
- 回复中可说明“基于刚上传原始文件，知识库编译仍在后台进行”。

**优先级：**

```text
单文件内容问题：Hermes 原生读取 > Hot Gateway > 等待编译（不要等）
跨文件/历史/引用：Knowledge Gateway
```

原生读取是当前会话的即时上下文，不是知识库检索；不要把“能读一份文件”误认为“能替代公司知识库”。


## 不需要做的事

- ❌ 不要自己编译 wiki 页面（留给 company-wiki-compile 流程）
- ❌ 不要删除 raw/ 中的任何文件
- ❌ 不要直接修改 `.hashes.json`（留给编译流程）
- ❌ 不要用裸 echo 写 `.pending`

## 参考文档

- `references/architecture.md` — 完整系统架构、Cron 任务、回滚步骤（注：该文件当前不在本工作副本中）

## 常见陷阱

- **附件默认自动入库**：不要问“是否入库”，除非用户明确说“这次不要入库 / 只是临时看看”。
- **重复上传**：先按 sha256 查重，复用已有 raw 路径，不产生多个副本。
- **文件分类错误**：不确定时默认 `raw/consulting/`，子目录可根据需要后期扩展
- **必须用 append_pending.py**：禁止使用裸 `echo >> .pending`。并发写入时必须走文件锁脚本。
- **上传不触发编译**：编译由 Hermes cron（`company-wiki-compile`）自动检测 `.pending` 与哈希漂移后执行；上传流程只负责存文件 + append_pending。
- **编译期间追问**：员工追问刚上传单份文件时，优先 Hermes 原生读取；需要跨文件/历史/引用/知识库检索时走 Hot Document Lane 或 company-rag；不等待 Wiki 编译。文本走原生读取或 Hot Native Context，视觉内容优先由 Hermes 内置 Vision 即时回答。
- **raw/ 可编辑**：用户可直接修改 raw/ 中文件，sha256 变化会自动触发重编译，无需手动操作
- **Hot Ingest 不是 Wiki Compile**：允许立即做结构化抽取和视觉资产物化；禁止在上传主链路中调用外部 OCR/Vision API 或启动 LLM-Wiki 全量知识编译。
- **视觉内容分两条路径**：Hot 当前会话由 Hermes Vision 看；后台 full refresh 才通过 OCR + 外部 Vision API 转成长期 evidence 并进入 QMD。
- **不要安装工具或修复环境**：如果缺少 OCR、pandoc 等工具，不要尝试安装。直接告知用户文件已存档，询问他们需要什么帮助。
