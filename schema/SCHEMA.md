# Company Wiki Schema — 企业知识库规范

## Domain
企业知识资产：访谈笔录、咨询作品（调研报告、企业文化体系、品牌体系、执行方案、项目文档）、行业情报、同行资料。覆盖公司全部知识沉淀。

## 多模型分工
| 任务 | 模型 | 触发方式 |
|------|------|---------|
| 知识编译 / 交叉引用 | deepseek-v4-pro | 文件上传/修改后即时 |
| 深度综合分析 / 对比 | deepseek-v4-pro | 查询触发 |
| 图片识别（文件内含图） | qwen-vl-plus (auxiliary vision) | 编译时自动检测 |
| Lint 健康检查 | qwen3.7-plus | 每 3 天定时 |
| 知识图谱生成 | qwen3.7-plus | 每周定时 |

## Conventions
- 文件名: 小写英文 + 连字符, 不用空格 (如 `industry-report-2026-q1.md`)
- 每个 wiki 页面以 YAML frontmatter 开头 (见下方模板)
- 使用 `[[wikilinks]]` 做页面间链接 (每页至少 2 条出链)
- 更新页面时必须更新 frontmatter 中的 `updated` 日期
- 每个新建页面必须加入 `index.md` 对应分类
- 每次操作必须追加到 `log.md`
- 来源标注: 合成 3+ 来源的页面, 在段落末尾追加 `^[raw/consulting/source.md]` 标注出处
- **文件可修改**: raw/ 中的源文件允许用户编辑更新，编译系统通过 sha256 检测变化后自动重编译

## raw/ Frontmatter
原始来源文件需存入时附带 frontmatter：

```yaml
---
source_url: https://example.com/page    # 原始 URL（如适用）
uploaded_by: profile-name               # 上传员工
uploaded_at: YYYY-MM-DD HH:MM           # 上传时间
ingested: YYYY-MM-DD                    # 首次编译日期
sha256: <正文的 hex 摘要>               # 用于检测文件变更
file_type: interview | report | industry | competitor | document
---
```

**sha256 规则:** 编译完成后计算并写入。下次扫描时对比，不一致则触发重编译。

## Wiki Page Frontmatter
```yaml
---
title: 页面标题
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | source | synthesis | finding | comparison
tags: [来自下方标签体系]
sources: [raw/consulting/source-name.md]    # 追溯原始来源
compiled_by: profile-name                   # 编译者
confidence: high | medium | low
contested: true
contradictions: [other-page-slug]
---
```

## Tag Taxonomy
- **内容类型**: interview, report, industry-analysis, competitor-analysis, case-study, proposal
- **领域**: brand, culture, strategy, execution, organization, market
- **方法**: methodology, framework, model, benchmark
- **质量**: insight, trend, prediction, risk, opportunity
- **元数据**: comparison, synthesis, overview, pitfall, reference

规则: 页面上每个 tag 必须来自此体系。需要新 tag 时先加到这里, 再使用。

## 子目录自动扩展规则
`raw/` 下的子目录可根据上传文件的实际内容自动创建。新子目录创建后，需同步更新本 SCHEMA 中的目录列表和 index.md 的分类结构。

当前子目录:
- `raw/interviews/` — 访谈笔录
- `raw/consulting/` — 咨询作品（调研报告、文化体系、品牌体系、执行方案、项目文档）
- `raw/industry/` — 行业情报
- `raw/competitor/` — 同行资料
- `raw/assets/` — 图片、PDF 等附件

## Page Thresholds
- **创建新页面** 当实体/概念在 2+ 来源中出现 OR 是某一来源的核心
- **追加到现有页面** 当来源提到已有内容的相关信息
- **不创建页面** 对于一笔带过的提及、次要细节
- **拆分页面** 超过 ~200 行时 — 拆为子主题并互链
- **归档页面** 内容完全被替代时 — 移到 `_archive/`, 从 index 中移除

## Page Types

### Source Pages (wiki/sources/)
每个 raw 文件编译后对应一个 source 页面。包含:
- 来源概述（1-2 段总结）
- 关键信息提取（列表/表格）
- 编译出的实体和概念（[[wikilinks]]）
- 原文链接: `^[raw/xxx/xxx.md]`

### Entity Pages (wiki/entities/)
每个值得记录的实体一页（公司、产品、人物、项目）。包含:
- 概述 / 是什么
- 关键事实和日期
- 与其他实体的关系 ([[wikilinks]])
- 来源引用

### Concept Pages (wiki/concepts/)
概念、方法论、框架、术语。包含:
- 定义 / 解释
- 当前知识状态
- 相关概念 ([[wikilinks]])
- 来源引用

### Synthesis Pages (wiki/synthesis/)
跨文档的综合分析报告。包含:
- 主题 / 问题
- 多来源交叉分析
- 结论或建议
- 所有引用来源

### Finding Pages (wiki/findings/)
关键发现、洞察、趋势判断。包含:
- 发现内容
- 支撑证据（来源引用）
- 置信度
- 相关页面 ([[wikilinks]])

### Comparison Pages (wiki/comparisons/)
并列对比分析。包含:
- 对比什么、为什么
- 对比维度 (优先用表格)
- 结论或综合
- 来源

### overview.md
全局概览，定期自动更新。包含:
- 当前知识库统计数据
- 最近新增/更新的页面
- 知识热点（被引用最多的实体/概念）
- 待解决问题

## Update Policy
当新信息与现有内容冲突时:
1. 比较日期 — 新来源一般取代旧来源
2. 如果确实矛盾, 同时记录两种观点, 标注日期和来源
3. 在 frontmatter 中标记: `contradictions: [page-name]`
4. 在 lint 报告中标记待用户审核

## 中英文规范
- wiki 页面内容使用中文书写, 便于阅读
- 文件名、tag、frontmatter 字段名、wikilink 路径使用英文
- 技术/行业术语保留原词，必要时括号标注中文
- 命令和代码块原文保留, 不翻译

## 编译触发机制
1. 员工上传文件 → 对应 skill 存入 raw/ → 写入 `.pending` 文件
2. 后台的 Compile cron（每分钟）检查 `.pending` + 对比 `.hashes.json` 中的 sha256
3. 发现新文件或变更 → 触发 deepseek-v4-pro 编译
4. 编译完成 → 更新 `.hashes.json` → 清空 `.pending` → 写 log.md
5. Lint cron（每 3 天）自动检查知识库健康状态

## RAG V2 派生索引层

RAG 不直接修改本 Schema 下的 Wiki 知识资产。`rag/` 为可重建派生层：Wiki 标题层级被投影为 Chapter-aware 节点，raw 文件被确定性抽取为 evidence chunks，并交由 QMD 索引。详细规范见 `schema/RAG_SCHEMA.md`。

每次 后台 Compile Cron 成功完成 Wiki 编译后，追加执行：

```bash
python3 /home/demo/company-wiki/scripts/rag_refresh.py
```

该步骤不改变 raw/wiki 的事实与知识语义，只刷新检索表示。
