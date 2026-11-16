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

## 投影切换安全

`rag_refresh` 不在开头删除 live 投影，而是先构建到 `rag/.projection-staging-*`，验证成功后再切换：

```text
build staging/core + evidence + extracted + manifest/graph/lexicon
→ 设置 rag/.projection_switch
→ 旧 live 的 core/evidence/extracted 移到 rag/.projection-prev-<ts>-<pid>
→ 新 staging 的 core/evidence/extracted 切入 live
→ 移除 switch 标记
→ qmd update + qmd embed
→ 成功后安装新的 graph/lexicon/manifest（manifest 最后）
```

- 构建期间 live `rag/qmd/core`、`evidence`、`extracted` 仍可读，查询不会看到“投影已删除”。
- 构建失败：live 投影完全不动。
- 投影目录切换失败：`swap_projection` 会尝试恢复旧 live 投影。
- `qmd update/embed` 期间：**继续使用旧 manifest + 旧 QMD**；新 projection 目录已在新路径，但新 graph/lexicon/manifest 还没有暴露。
- QMD 更新成功后：才安装新 manifest；sidecar 在请求前检查 manifest mtime/size，自动 reload，不需要重启。
- 旧投影保留在 `rag/.projection-prev-*`，可用于人工回滚；它不参与 QMD 索引。
- QMD 更新失败：旧 manifest 不被替换，旧查询继续可用；新页面暂不可见，任务标记失败。
- `.projection_switch` 只是切换窗口的观测标记，不阻塞查询；旧 manifest 指向的文件已被移走时，`CompanyRAG` 从最近的 `rag/.projection-prev-*` 读取旧块作为 fallback。

## 重建期间查询

| 阶段 | 查询行为 |
|---|---|
| staging 构建中 | live 投影仍完整；`company-rag` 继续读旧 projection + 旧 manifest + 旧 QMD 索引，看到的是**上一版知识快照**；`direct` / `hot-document` 不受影响 |
| live swap 窗口 | 正式 RAG 不阻塞：旧 manifest 的块从 `rag/.projection-prev-*` fallback；新请求在 manifest 切换后读取新投影；如果 swap 失败，旧投影已恢复 |
| qmd update / embed | 旧 manifest + 旧 QMD 继续服务；新 projection 目录已经就位，但新 metadata/manifest 还没安装；新页面暂时查不到，旧查询正常 |
| 新上传文件 | `rag_hot_ingest` 和 Hot 路径立即可用；正式 Wiki/QMD 索引等下一次 refresh |

关键语义：

- 重建期间不是“停止 RAG”，而是**读旧快照**。
- 新编译内容在切换前不可见，这是有意的延迟，不是丢失。
- 如果业务要求“重建期间不能看到旧数据”，需要在接入层返回数据版本/延迟提示，或改用双 generation + 双 QMD 索引；当前实现优先保证可用性和不返回空上下文。

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
