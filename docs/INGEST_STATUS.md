# 入库进度状态（KB ingestion status）

## 目标

上传文件后，人和 agent 能回答：

- 文件是否已收到、是否排队
- 当前在提取、写 Wiki、重建投影、更新 QMD，还是 embedding
- 是否失败、失败原因
- 已经用了多久

状态是**观测层**，不改变入库和编译逻辑；状态写入失败不能阻塞上传/编译。

## 状态文件

```text
rag/jobs/<job_id>.json
```

- `job_id = sha1(raw_path + raw_sha256)[:16]`，同一路径不同内容会生成新 job
- `rag/jobs/` 是运行态，已加入 `.gitignore`
- 写入使用 `.scripts/_locklib.py` 的文件锁 + 临时文件 `os.replace`
- 每个任务保留 `history`，可追溯阶段变化

字段：

```json
{
  "job_id": "...",
  "raw_path": "raw/consulting/...",
  "raw_sha256": "...",
  "filename": "...",
  "uploader": "operator",
  "stage": "qmd_embed",
  "stage_label": "正在生成 QMD 向量",
  "stage_index": 9,
  "total_stages": 11,
  "status": "running",
  "created_at": "...",
  "updated_at": "...",
  "started_at": "...",
  "finished_at": null,
  "attempt": 1,
  "detail": {"qmd_pending": 37},
  "error": null,
  "history": []
}
```

## 阶段顺序

```text
uploaded → queued → claimed → extracting → wiki_compiling
→ wiki_written → hashes_updated → projection → qmd_update
→ qmd_embed → done
```

`status`：`running` / `done` / `failed` / `stalled`。

## 自动打点位置

| 入口 | 阶段 |
|---|---|
| `.scripts/append_pending.py` | `queued` |
| `.scripts/check_pending.py` | `claimed` |
| `company-wiki-compile` 技能（提取前） | `extracting` |
| `company-wiki-compile` 技能（写 Wiki 前） | `wiki_compiling` |
| `company-wiki-compile` 技能（页面写完） | `wiki_written` |
| `.scripts/update_hashes.py` | `hashes_updated` |
| `scripts/rag_refresh.py` | `projection` → `qmd_update` → `qmd_embed` → `done` |
| 异常 | `failed` |

`rag_refresh` 启动时会快照当前 active job ID；projection/embedding 期间新上传的任务保持 `queued`，不会被本轮 refresh 误标成 done。

`rag_refresh.py` 使用独立的 `.rag_refresh.lock` 串行化整个 `projection → qmd update → qmd embed`；第二个 refresh 会等待，不会并发写投影或同时发起 embed。

## 查询

```bash
# 最近的活动任务；没有活动任务则最近一条
python scripts/kb_progress.py --json

# 全部任务
python scripts/kb_progress.py --json --all

# 指定任务/文件
python scripts/kb_progress.py --json --job-id <job_id>
python scripts/kb_progress.py --json --raw raw/consulting/xxx.md
```

## 通知

`scripts/kb_notify.py` 只输出**阶段变化**；没有变化时 stdout 为空。

适合 no_agent cron（1 分钟）：

```text
script = kb_notify.py
no_agent = true
```

状态保存在 `rag/jobs/.notified.json`，同一阶段不会重复通知。

## 失败与重试

```bash
python scripts/kb_job.py fail <raw> --error "原因"
```

失败任务重新进入编译时，`kb_job` 会把 `status` 改回 `running` 并递增 `attempt`。

人工确认 refresh 已补齐后，可把失败任务正确收尾（会同时修正 `finished_at`）：

```bash
python scripts/kb_job.py done <raw> --detail '{"qmd":"verified"}'
```

`stalled` 需要外部检查（例如 watchdog 或人工）标记；不会自动删除 raw/pending。

## 回滚

状态层是增量改动。要关闭：

1. 删除/暂停 `kb-progress-notify` cron；
2. 删除 `rag/jobs/`；
3. 回退以下文件的提交：
   - `scripts/kb_job.py`
   - `scripts/kb_progress.py`
   - `scripts/kb_notify.py`
   - `.scripts/{append_pending,check_pending,update_hashes}.py`
   - `scripts/rag_refresh.py`
   - `skills/company-wiki-status/`

即使状态层完全删除，上传、编译、projection、QMD 仍按原逻辑运行。
