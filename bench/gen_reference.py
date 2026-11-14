# -*- coding: utf-8 -*-
"""bench/gen_reference.py — 从金标页生成参考答案（bench 侧，不改生产代码）

流程：金标页全文（manifest 拼接）→ LLM → 参考答案 → bench/results/judge/refs/
可续跑；--only 过滤；--start/--end 分批。

用法:
  python bench/gen_reference.py                # 全量
  python bench/gen_reference.py --only C15,E06 # 冒烟
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os  # noqa: E402
os.chdir(ROOT)

from rag_gateway.search import CompanyRAG  # noqa: E402
from bench.llm import chat, MODEL  # noqa: E402

OUT = ROOT / "bench/results/judge/refs"
OUT.mkdir(parents=True, exist_ok=True)

REF_SYS = (
    "你是企业知识库的参考答案生成器。你只能依据【资料】作答，不得引入资料之外的信息；"
    "如资料不足以完整回答，明确指出缺失部分。"
)
REF_USER = "【资料】\n{gold}\n\n【问题】\n{q}\n\n请基于资料写一段参考答案（不超过 350 字）。"


def gold_text(rag, row, cap=26000):
    nodes = rag.manifest["nodes"]
    pages = set(row["target_pages"])
    chunks = []
    for nid, n in nodes.items():
        if n.get("page_relpath") in pages:
            chunks.append(f"# {n.get('page_relpath')} > {n.get('section_title') or ''}\n{n.get('content') or ''}")
    return "\n\n".join(chunks)[:cap]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=10**9)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--file", default=None)
    args = ap.parse_args()

    out_dir = OUT if not args.file else ROOT / "bench/results/judge/h50/refs"
    out_dir.mkdir(parents=True, exist_ok=True)
    src = args.file or "bench/dataset/queries.jsonl"
    rows = []
    for line in (ROOT / src).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        rows = [r for r in rows if r["id"] in want]

    jobs = []
    for row in rows:
        for q in row["queries"]:
            jobs.append((row, q))
    jobs = jobs[args.start:args.end]

    rag = CompanyRAG(ROOT)
    print(f"模型: {MODEL} | 任务: {len(jobs)} 条参考答案")

    def work(row, q):
        f = out_dir / f"{row['id']}_{q['style']}.json"
        if f.exists():
            return f"{row['id']}_{q['style']}: skip(已存在)"
        gold = gold_text(rag, row)
        if not gold.strip():
            return f"{row['id']}_{q['style']}: ERROR 金标为空"
        ref = chat(REF_SYS, REF_USER.format(gold=gold, q=q["text"]), temperature=0.1)
        f.write_text(json.dumps({"id": row["id"], "style": q["style"], "text": q["text"],
                                 "ref": ref, "gold_chars": len(gold)}, ensure_ascii=False, indent=1),
                     encoding="utf-8", newline="\n")
        return f"{row['id']}_{q['style']}: ok({len(ref)}字)"

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, r, q) for r, q in jobs]
        done = 0
        for fu in as_completed(futs):
            done += 1
            try:
                print(f"[{done}/{len(jobs)}] {fu.result()}")
            except Exception as e:  # noqa: BLE001
                print(f"[{done}/{len(jobs)}] ERROR: {str(e)[:150]}")


if __name__ == "__main__":
    main()
