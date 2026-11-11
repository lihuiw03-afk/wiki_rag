# 后台 Compile Cron 需要追加的 V2 步骤

原有 Compile Cron 仍负责：

1. `.pending` / hash 变化检测
2. 读取 raw 文件
3. LLM 编译 Wiki Entity / Concept / Source
4. 更新 `.hashes.json` / `log.md`

在“Wiki 编译成功并落盘”之后追加：

```bash
python3 /home/demo/company-wiki/scripts/rag_refresh.py
```

严格顺序：

```text
raw change
  -> LLM-Wiki compile
  -> Wiki atomic/complete state
  -> rag_refresh
  -> QMD update
  -> QMD embed changed chunks
```

不要在 Wiki 尚未写完时触发 QMD refresh，否则可能把半成品页面索引进去。

如果 QMD 临时不可用，Wiki 编译本身不应回滚；记录 RAG refresh 失败，并在服务恢复后单独执行 `python3 scripts/rag_refresh.py` 即可，因为 `rag/` 是派生层。

## V2.4 multimodal note

`rag_refresh.py` 现在除了 Chapter/Evidence projection，还会对新出现或视觉内容变化的 asset 做 OCR + 外部 Vision API。`rag/visual_cache` 会避免旧图片重复调用模型，但一份新的大型 PPT/PDF 第一次 full refresh 仍可能明显比纯文本投影慢。Hot Document 不等待这条链路，视觉问题直接使用 Hermes 内置 Vision。

因此生产环境建议保证：

1. Wiki 文件先原子写完；
2. Compile lock 不要覆盖整个长耗时 OCR/API-Vision 周期，或把 RAG refresh 放到独立 maintenance worker/systemd timer；
3. RAG refresh 失败不回滚 Wiki；
4. 员工在此期间仍走 Hot Document Lane，不受长期 full visual enrichment 影响。

这样知识编译和多模态索引之间是 eventually consistent，而不是把员工请求同步阻塞在 OCR/外部模型 API 上。
