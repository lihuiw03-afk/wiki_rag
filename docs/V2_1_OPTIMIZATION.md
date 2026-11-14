# V2.1 Retrieval Optimization Notes

本次升级聚焦运行时检索编排，不改变 `raw/`、`wiki/` 与 QMD 双 collection 的基础架构。

## 1. ACL Removal

业务前提改为所有员工拥有完整 Company Wiki 权限。

- 删除 `config/acl.yaml`。
- `search()` 不再根据 Profile 过滤 collection。
- `profile` 参数保留，仅写入 trace，表示调用者身份。

## 2. Adaptive Context Builder

旧逻辑：所有问题统一 `max_hits_per_raw_source = 2`。

问题：对于“完整总结某一份访谈/报告”，高相关内容可能连续分布在多个 chunk 中，固定 2 个会牺牲 completeness。

新逻辑：

| Policy | 典型问题 | raw/source cap | evidence neighbors | 目标 |
|---|---|---:|---:|---|
| diversity | 综合不同访谈/比较多项目 | 2 | 0 | 来源覆盖 |
| balanced | 普通知识问答 | 3 | 少量 | Precision/Recall 平衡 |
| source-focused | 详细总结这份资料/原文 | 8 | ±1 | 单来源完整性 |

Context Builder 使用 score 与 repeat penalty 的 greedy selection；hard cap 只做最终边界。

## 3. Evidence Neighbor Expansion

检索 chunk 是定位单元，不一定是完整回答单元。

```text
best hit: chunk 7
      ↓
chunk 6 + chunk 7 + chunk 8
```

相邻 chunk 使用折扣分数，只用于补齐连续语境。`diversity` 关闭该功能。

## 4. Graph Neighbor Section Retrieval

旧逻辑：

```text
QMD hit page A
→ graph neighbor page B
→ page B root node
```

问题：Wikilink 只说明 B 与 A 有关系，root section 未必回答当前 Query。

新逻辑：

```text
QMD hit page A
→ graph neighbor pages
→ one auxiliary QMD query on company-core
→ filter results to neighbor pages
→ choose best section in each page
```

如果目标 page 没进入 QMD 返回窗口，则对该页所有 section 做轻量 CJK/Latin lexical fallback，避免盲目 root injection。

## 5. Restricted Source Evidence Retrieval

旧逻辑：

```text
Wiki raw_sources
→ raw_chunks[source][0]
```

它只证明 provenance，不证明 chunk 0 与当前 Query 相关。

新逻辑：

```text
high-confidence Wiki hit
→ linked raw sources
→ one auxiliary QMD query on company-evidence
→ filter results to linked source set
→ choose best matching chunk per source
```

如果主 QMD 检索已经命中了某个 linked raw source，则直接复用主结果，不重复查询。

## 6. Why Auxiliary Search + Filter

当前 QMD 的主要稳定检索作用域是 collection。为了不为每个 raw 文件创建独立 collection，本项目使用：

1. 保留原 query 的 lex / vec / hyde。
2. 额外增加带目标 page title / raw source name 的 lex query。
3. QMD 返回 candidate 后过滤到允许的 page/source node set。
4. 未命中目标集合时使用 deterministic local scorer 兜底。

这样只增加最多少量辅助检索调用，同时保持单 SQLite / 双 collection 架构。

## 7. Tests

当前 9 个单元测试包括：

- Markdown Chapter Tree
- Router Fast/Balanced/Deep
- Context policy classification
- Typed lex/vec query
- Manifest integrity
- Source-focused 允许多个同源 chunk
- Diversity 仍限制同源 chunk
- Graph neighbor 选择非 root section
- Linked raw source 选择相关 chunk 而非 chunk 0

`python scripts/project_validate.py` 也会重新构建 Projection 并检查所有 manifest projection 是否存在。
