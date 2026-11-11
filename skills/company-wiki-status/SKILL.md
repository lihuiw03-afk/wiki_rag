---
name: company-wiki-status
description: Use when reporting knowledge-base ingestion progress.
version: 1.0.0
author: Hermes Agent
license: UNLICENSED
metadata:
  hermes:
    tags: [company-wiki, rag, ingestion, progress, status]
    related_skills: [company-wiki-upload, company-wiki-query]
---

# Company Wiki 入库进度

## When to Use

- 用户问“文件编译到哪一步了 / 入库进度 / 知识库更新完成了吗”
- 上传后需要确认是否已排队、是否在 embedding、是否失败
- 不要根据 raw 文件 mtime、`compile_state.py` 的 `CLEAN` 或 `qmd status` 单独猜测完整阶段

## Command

```bash
PY="C:/Users/demo/Projects/cwr24/.venv/Scripts/python.exe"
"$PY" "C:/Users/demo/Projects/cwr24/scripts/kb_progress.py" --json
```

Linux 生产副本：

```bash
cd /home/demo/company-wiki
python3 scripts/kb_progress.py --json
```

指定任务：

```bash
"$PY" .../scripts/kb_progress.py --json --job-id <job_id>
"$PY" .../scripts/kb_progress.py --json --raw "C:/Users/demo/Projects/cwr24/raw/..."
"$PY" .../scripts/kb_progress.py --json --all
```

## 阶段

```text
uploaded → queued → claimed → extracting → wiki_compiling
→ wiki_written → hashes_updated → projection → qmd_update
→ qmd_embed → done
```

失败时 `status=failed`；超时未更新时可能是 `stalled`。

## 回答规则

1. 先报告文件名、当前 stage_label、stage_index/total、status、elapsed_seconds。
2. 有 `detail` 就带上关键数字：Wiki 页数、manifest nodes、qmd_pending。
3. `status=failed` 时给出 `error`，不要把它说成“正在处理”。
4. 没有活动任务时，报告最近一条已完成/失败任务；不要编造进度。
5. 不要为了查进度启动编译、删除锁或修改 raw。
6. `qmd status` 的 `Pending` 只能作为 `qmd_embed` 阶段的辅助信息，不能替代 job 状态。

## 示例回答

```text
文件：20261110_operator_结构扩展和上下文.md
阶段：QMD 向量索引（10/11）
已用：11 分钟
Wiki：已完成，4 个页面
索引：41 个 chunk 待嵌入
```

## 参考

- `scripts/kb_job.py` — 状态写入（编译/上传脚本调用）
- `scripts/kb_progress.py` — 只读查询
- `rag/jobs/<job_id>.json` — 每个任务的状态文件

## 通知

`scripts/kb_notify.py` 只输出**阶段变化**；没有变化时 stdout 为空。当前已配置 no-agent cron：

```text
name = kb-progress-notify
script = kb_notify.py
no_agent = true
every 1m
deliver = origin
```

状态保存在 `rag/jobs/.notified.json`，同一阶段不会重复通知。
