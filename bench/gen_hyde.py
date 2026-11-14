# -*- coding: utf-8 -*-
"""bench/gen_hyde.py — 生成 HyDE 缓存（bench 侧，不改生产代码）

为 58 基础集 + 困难集 50 的每条查询文本生成一段"假设性资料段落"，
写入 bench/results/hyde_cache.json（{query_text: hypothetical}），供
run_experiments.py 的 v2/hyde 变体通过既有 hyde 通道注入（router 的 hyde 向量腿）。

用法: python bench/gen_hyde.py
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
import os  # noqa: E402

os.chdir(ROOT)

from llm import chat, MODEL  # noqa: E402

OUT = ROOT / "bench/results/hyde_cache.json"
SYS = ("你是企业知识库检索辅助器。请针对用户问题，凭先验写一段“假设性的内部资料段落”（250 字以内），"
       "风格尽量像企业内部文档或访谈记录（含可能的关键词与实体名，数字可用占位）。"
       "这只是用于向量检索的假想文档，允许与真实资料不完全一致；只输出段落本身，不要解释。")
USER = "【问题】\n{q}\n\n请写假设性资料段落。"


def load_texts():
    texts = []
    for f in ("bench/dataset/queries.jsonl", "bench/dataset/queries_hard50.jsonl"):
        for line in (ROOT / f).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            for q in row["queries"]:
                if q["text"] not in texts:
                    texts.append(q["text"])
    return texts


def main():
    cache = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    texts = [t for t in load_texts() if t not in cache]
    print(f"模型: {MODEL} | 待生成 {len(texts)} 条（已有 {len(cache)}）")

    def work(t):
        h = chat(SYS, USER.format(q=t), temperature=0.3, max_tokens=500)
        return t, h.strip()

    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(work, t) for t in texts]
        done = 0
        for fu in as_completed(futs):
            done += 1
            try:
                t, h = fu.result()
                cache[t] = h
                print(f"[{done}/{len(futs)}] ok({len(h)}字) {t[:40]}")
            except Exception as e:  # noqa: BLE001
                print(f"[{done}/{len(futs)}] ERROR: {str(e)[:120]}")
    OUT.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(f"已写出 {OUT}：{len(cache)} 条")


if __name__ == "__main__":
    main()
