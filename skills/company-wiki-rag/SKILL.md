---
name: company-wiki-rag
description: "Company Wiki RAG V2.4 统一知识网关；自动路由 Direct / Hot Document / QMD Hybrid Graph-RAG"
version: 2.4.1
author: Hermes Agent
---

# Company Wiki RAG（Windows 工作副本 cwr24）

> 本机路径约定：项目根 = `C:/Users/demo/Projects/cwr24`；Python 一律用项目 venv：
> `PY="C:/Users/demo/Projects/cwr24/.venv/Scripts/python.exe"`（以下命令中 `"$PY"` 均指该解释器）。

这是统一 Knowledge Gateway Skill。不要让模型仅凭感觉决定是否跑 RAG：优先调用 `rag_ask.py`，由 deterministic Retrieval Gate 在 `direct / hot-document / hot+company / company-rag` 之间路由。

## 核心原则

1. 不直接遍历整个 `wiki/` 目录猜答案；先走 RAG。
2. 最终回答只能依据检索返回的 `[S#]` 证据，关键事实标注 `[S#]`。
3. `raw evidence` 与 Wiki 结论冲突时，优先说明冲突并引用更直接/更新的原始来源。
4. 证据不足时明确回答“当前知识库没有足够证据”，不要用模型常识补企业内部事实。
5. 默认 `--mode auto --context-policy auto`。
6. 所有 Profile 拥有完整知识库权限；`--profile` 仅用于 trace 标识调用者。

7. 正式 `company-evidence` 中可能同时包含 Native Text、`visual_ocr` 和由后台模型 API 生成的 `visual_semantics`。对于**当前刚上传附件**，若 `rag_ask.py` 返回 `hermes_vision.required=true`，必须先调用 Hermes 内置 Vision 查看 `hermes_vision.assets`，不要等待后台 API。

## 统一调用方式

```bash
PY="C:/Users/demo/Projects/cwr24/.venv/Scripts/python.exe"
"$PY" C:/Users/demo/Projects/cwr24/scripts/rag_ask.py \
  "<用户问题>" --profile "$PROFILE" --json
```

如果当前/最近一轮有上传文件，务必传入附件路径：
```bash
"$PY" C:/Users/demo/Projects/cwr24/scripts/rag_ask.py \
  "<用户问题>" --attachment "C:/Users/demo/Projects/cwr24/raw/.../file.docx" \
  --profile "$PROFILE" --json
```

`route=direct`：无需知识库，正常回答。
`route=hot-document`：刚上传文件尚未编译，直接走 Hot Document Lane。
`route=hot+company`：当前文件和历史 Company Wiki 一起检索。
`route=company-rag`：走正式 QMD + Chapter/Graph/Evidence RAG。

### Hot Hermes Vision 契约

当结果包含：

```json
{"hermes_vision":{"required":true,"assets":[...]}}
```

必须执行以下顺序：

1. 先读取返回的 `[S#]` Native Text Context；
2. 使用 Hermes 内置 Vision 查看 `assets[].path` 对应图片/渲染页；
3. 视觉分析围绕当前用户 Query，仅提取可见文字、表格/图表、箭头、流程、层级/空间关系与不确定项；
4. 把 Hermes Vision 观察与 `[S#]` 证据合并回答；
5. Hot 路径**不得调用后台 Vision API**，也不要把本轮 Hermes Vision 临时描述直接写入 `rag/qmd/evidence`。


## Retrieval Gate 规则

1. 有当前附件路径 → 默认 `hot-document`。
2. 当前附件 + “对比以往/历史项目/其他客户” → `hot+company`。
3. 问题命中 Company Wiki 实体/术语、客户/项目/访谈等内部知识意图 → `company-rag`。
4. 单纯润色、翻译、写邮件、代码、一般创作且不依赖内部事实 → `direct`。
5. “这份/这个文件/刚上传/原文/报告中”等追问，且存在最近 Hot Document → `hot-document`。

Hot Document 是编译前的临时原文检索层，不替代 LLM-Wiki。编译完成后正式 `company-evidence` 与 `company-core` 接管长期检索。

## Retrieval 模式

- **fast**：名称、编号、全称、定义、定位文件等精确查询。Lex/BM25 为主，不运行 Reranker。
- **balanced**：默认。Lex + Vector + RRF + QMD Reranker，并有限注入父子章节、Wiki 关系和 raw evidence。
- **deep**：综合、比较、跨文档分析。扩大候选池，可选 HyDE。

## Context Policy

- **diversity**：综合不同访谈/不同项目/不同管理层。强调跨来源覆盖，同一 raw source 最多保留少量 chunk。
- **balanced**：普通问答。兼顾知识密度、原始证据和来源多样性。
- **source-focused**：用户明确要求深挖某份访谈、报告、PPT、原文或文件时使用；允许更多同源 chunk，并补前后相邻 chunk。

自动判断通常足够。若用户明确要求“完整总结这份访谈/报告”，建议显式使用：

```bash
"$PY" C:/Users/demo/Projects/cwr24/scripts/rag_ask.py \
  "<问题>" --mode deep --context-policy source-focused --profile "$PROFILE" --json
```

## HyDE 使用规则

不要对每个问题都用 HyDE。仅在问题是抽象总结/答案型表达、而原文可能没有相同词面时使用。先由当前 Agent 生成 1-2 句“可能出现在相关材料中的证据描述”，再传入：

```bash
"$PY" C:/Users/demo/Projects/cwr24/scripts/rag_ask.py \
  "<问题>" --mode deep --hyde "<假设性证据文本>" --profile "$PROFILE" --json
```

人名、公司名、编号、缩写、精确术语查询禁止 HyDE，避免语义漂移。

## 回答格式

- 先直接回答问题。
- 每个关键企业内部事实在句末引用 `[S1]`、`[S2]`。
- 多来源综合时尽量覆盖不同 source。
- 单文档深挖时不要为了“来源多样性”强行引入其他材料。
- 如果检索结果主要来自 Wiki，而问题要求精确原话/数字，应优先使用结果中的 raw evidence。

