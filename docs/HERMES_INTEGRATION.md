# Hermes Integration

## 两个 Skill 的职责

### company-wiki-upload

保持原职责：员工上传文件 → `raw/` → `.pending`，不在上传 Profile 内做编译。

### company-wiki-rag

负责查询：员工问题 → `rag_search.py` → QMD → hierarchy/graph/evidence → Adaptive Context Builder → Hermes 生成答案。

## Profile 权限

当前所有员工/Profile 都拥有完整 Company Wiki 权限。Gateway 不按 Profile 过滤 collection；`--profile` 参数只用于 trace caller identity。

## 推荐 Profile 配置

所有需要使用企业知识库的 Profile 安装 `skills/company-wiki-rag/SKILL.md`。

Profile 只调用一个 RAG Gateway CLI，不应该直接：

- 自己遍历 `wiki/`
- 自己调用多次 qmd 并拼接
- 自己无预算地展开 Wikilink

这样检索策略集中在一个地方，可以统一升级。

## QMD MCP

QMD daemon 同时提供 `http://127.0.0.1:8181/mcp`。Hermes 若已有原生 QMD MCP 集成可以继续连接，但企业知识问答建议通过 Company RAG Gateway，因为后者额外负责 Chapter Tree、Wiki Graph、Graph section retrieval、restricted raw evidence retrieval 和 Adaptive Context Builder。

## Context Policy

默认 `--context-policy auto`：

- 跨来源综合/比较 → `diversity`
- 指定某份访谈/报告/原文深挖 → `source-focused`
- 其他 → `balanced`

也可以人工覆盖：

```bash
python3 scripts/rag_search.py "详细总结这份资料" \
  --mode deep --context-policy source-focused --profile "$PROFILE" --json
```

## 编译链路

```text
员工 Profile upload
  → raw
  → .pending
  → 后台 compile cron
  → wiki 更新
  → scripts/rag_refresh.py
  → rag projection
  → qmd update/embed
```
