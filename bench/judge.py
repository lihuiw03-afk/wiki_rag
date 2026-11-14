# -*- coding: utf-8 -*-
"""bench/judge.py — 裁判层执行器：baseline vs E6-A 的回答质量对比（bench 侧，不改生产代码）

流程: golden query → 参考答案(gen_reference.py 已生成) → 两系统上下文+回答 → judge 打分
指标: Answer Faithfulness / Answer Correctness / Context Precision（LLM 判别 + 金标机械口径）

用法:
  python bench/judge.py run --only C15,E06     # 冒烟
  python bench/judge.py run --start 0 --end 30 # 分批全量（可续跑）
  python bench/judge.py agg                    # 汇总 + 审计样本
"""
import argparse
import json
import random
import re
import statistics
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
import os  # noqa: E402

os.chdir(ROOT)

from rag_gateway.search import CompanyRAG  # noqa: E402
from run_experiments import PolicyRAG  # noqa: E402
from llm import chat, chat_json, MODEL  # noqa: E402

JDIR = ROOT / "bench/results/judge"
JDIR.mkdir(parents=True, exist_ok=True)
REFS = JDIR / "refs"

SYS_ANSWER = (
    "你是企业知识库助手。仅依据【参考资料】回答用户问题；不要编造资料之外的信息；"
    "引用关键事实时标注来源编号 [S#]（资料每条以 [S#] 开头）。"
)
U_ANSWER = "【参考资料】\n{ctx}\n\n【问题】\n{q}\n\n请基于资料作答（350 字以内）。若资料不足以回答，请明确说明。"

SYS_JUDGE = "你是严格、保守的评测裁判，评判务必校准。严格按指定键名输出 JSON，不要添加其他键。"
U_JUDGE = """【问题】
{q}

【参考答案】
{ref}

【待评回答】
{ans}

【待评回答所用的参考资料】
{ctx}

请严格输出且仅输出以下键的 JSON（不要输出 verdict/score 等其他键）：
{{
 "faithfulness": 1到5的整数（回答中的事实性陈述被【参考资料】支持的程度；5=全部有据可查，1=大量无据），
 "unsupported": ["不被资料支持的陈述", "..."],
 "correctness": 1到5的整数（与参考答案相比，正确性与完整性；5=正确且覆盖全部要点，3=方向正确但有缺失，1=错误或严重偏离），
 "missing": ["相对参考答案缺失的要点", "..."],
 "incorrect": ["与参考答案或资料矛盾的陈述", "..."],
 "comment": "一句话评语"
}}"""

SYS_PREC = "你是严格、保守的评测裁判。严格按指定键名输出 JSON。"
U_PREC = """【问题】
{q}

【参考答案（帮助你理解该问题需要什么信息）】
{ref}

【上下文块列表（编号从 0 开始的 B0/B1/...）】
{blocks}

请判断每个块对"回答该问题"是否有帮助（直接或间接提供有用信息）。严格输出且仅输出：
{{"relevant": [有帮助的块号...], "irrelevant": [无帮助的块号...]}}"""


def _iv(v):
    """尽力把值转成 int（容忍 '5'、'5分'、5.0）。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and float(v).is_integer():
        return int(v)
    if isinstance(v, str):
        m = re.search(r"\d+", v)
        return int(m.group(0)) if m else None
    return None


def v_judge(d):
    f, c = _iv(d.get("faithfulness")), _iv(d.get("correctness"))
    if f is None or c is None:
        return False
    d["faithfulness"], d["correctness"] = max(1, min(5, f)), max(1, min(5, c))
    return True


def v_prec(d):
    if not isinstance(d.get("relevant"), list) and not isinstance(d.get("irrelevant"), list):
        return False

    def norm(xs):
        out = []
        for x in xs or []:
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                out.append(int(x))
            elif isinstance(x, str):
                m = re.search(r"\d+", x)
                if m:
                    out.append(int(m.group(0)))
        return out

    d["relevant"], d["irrelevant"] = norm(d.get("relevant")), norm(d.get("irrelevant"))
    return True


_REFUSAL_PAT = ("没有提出具体问题", "尚未提出具体问题", "没有具体问题", "未提出具体问题",
                "请提出具体问题", "请补充", "请告诉我您想了解", "请说明您想了解")


def _looks_refusal(a: str) -> bool:
    head = a[:250]
    return any(p in head for p in _REFUSAL_PAT)


def _gen_answer(ctx, q):
    """生成回答；拒答/过短时逐级加强重试（标准 → 强调 → 问题前置），最多 3 次。"""
    user = U_ANSWER.format(ctx=ctx[:30000], q=q)
    a = chat(SYS_ANSWER, user, temperature=0.0, max_tokens=1000)
    if not _looks_refusal(a) and len(a.strip()) >= 30:
        return a, 1
    nudge = user + "\n\n（注意：用户已提出上述问题，请立即直接作答，不要反问、不要要求用户补充问题。）"
    a2 = chat(SYS_ANSWER, nudge, temperature=0.0, max_tokens=1000)
    if not _looks_refusal(a2) and len(a2.strip()) >= 30:
        return a2, 2
    qfirst = f"【问题】\n{q}\n\n【参考资料】\n{ctx[:30000]}\n\n请直接回答上述问题（350 字以内）。"
    a3 = chat(SYS_ANSWER, qfirst, temperature=0.0, max_tokens=1000)
    return a3, 3


def blocks_text(sel, nodes, snip=320):
    rows = []
    for i, it in enumerate(sel):
        n = nodes.get(it.get("node_id")) or {}
        title = f"{n.get('page_title') or ''} > {n.get('section_title') or ''}"
        content = (it.get("content") or "").replace("\n", " ")
        rows.append(f"B{i}: [{it.get('collection')}] {title} | {content[:snip]}")
    return "\n".join(rows)


def gold_precision(sel, nodes, target_pages):
    if not sel:
        return 0.0
    hit = sum(1 for it in sel if (nodes.get(it.get("node_id")) or {}).get("page_relpath") in target_pages)
    return hit / len(sel)


def judge_call(qtext, ref, ans, ctx):
    """judge 调用（含降级链：全上下文 → 缩短上下文+更大 token）。"""
    try:
        return chat_json(SYS_JUDGE, U_JUDGE.format(q=qtext, ref=ref, ans=ans, ctx=ctx[:30000]), max_tokens=1400,
                         validator=v_judge, retries=3,
                         keys_hint="faithfulness, unsupported, correctness, missing, incorrect, comment")
    except RuntimeError:
        return chat_json(SYS_JUDGE + " 不要输出思考过程，直接输出JSON对象。",
                         U_JUDGE.format(q=qtext, ref=ref, ans=ans, ctx=ctx[:9000]), max_tokens=2400,
                         validator=v_judge, retries=2,
                         keys_hint="faithfulness, unsupported, correctness, missing, incorrect, comment")


def case_work(row, q, rc, ra, nodes, jdir, refs):
    rid, style = row["id"], q["style"]
    ref_f = refs / f"{rid}_{style}.json"
    ref = json.loads(ref_f.read_text(encoding="utf-8"))["ref"]
    ctx_b, sel_b = rc.get("context") or "", rc.get("selected") or []
    ctx_e, sel_e = ra.get("context") or "", ra.get("selected") or []

    ans_b, ab = _gen_answer(ctx_b, q["text"])
    ans_e, ae = _gen_answer(ctx_e, q["text"])

    jb = judge_call(q["text"], ref, ans_b, ctx_b)
    je = judge_call(q["text"], ref, ans_e, ctx_e)

    def prec(sel):
        if not sel:
            return 0.0, 0, {}
        p = chat_json(SYS_PREC, U_PREC.format(q=q["text"], ref=ref, blocks=blocks_text(sel, nodes)), max_tokens=1200,
                      validator=v_prec, retries=3, keys_hint="relevant, irrelevant")
        n = len(sel)
        rel = len([x for x in (p.get("relevant") or []) if 0 <= x < n])
        return rel / n, rel, p

    pb, relb, praw_b = prec(sel_b)
    pe, rele, praw_e = prec(sel_e)

    case = {
        "id": rid, "style": style, "category": row.get("category"), "question": q["text"],
        "activated": ctx_b != ctx_e,
        "ref": ref,
        "base": {"ans": ans_b, "judge": jb, "prec_llm": pb, "prec_gold": gold_precision(sel_b, nodes, set(row["target_pages"])),
                 "n_blocks": len(sel_b), "n_relevant": relb, "prec_raw": praw_b, "gen_attempts": ab},
        "e6a": {"ans": ans_e, "judge": je, "prec_llm": pe, "prec_gold": gold_precision(sel_e, nodes, set(row["target_pages"])),
                "n_blocks": len(sel_e), "n_relevant": rele, "prec_raw": praw_e, "gen_attempts": ae},
        "model": MODEL,
    }
    (jdir / f"case_{rid}_{style}.json").write_text(json.dumps(case, ensure_ascii=False, indent=1),
                                                   encoding="utf-8", newline="\n")
    return f"{rid}_{style} ok | faith {jb.get('faithfulness')}/{je.get('faithfulness')} corr {jb.get('correctness')}/{je.get('correctness')} prec {pb:.2f}/{pe:.2f}"


def _load_rows(file_arg):
    rows = []
    src = file_arg or "bench/dataset/queries.jsonl"
    for line in (ROOT / src).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def cmd_run(args):
    jdir = JDIR / "h50" if args.file else JDIR
    refs = jdir / "refs"
    jdir.mkdir(parents=True, exist_ok=True)
    rows = _load_rows(args.file)
    if args.only:
        want = {s.strip() for s in args.only.split(",")}
        rows = [r for r in rows if r["id"] in want]
    jobs = [(r, q) for r in rows for q in r["queries"]]

    rag_c = CompanyRAG(ROOT)
    rag_a = PolicyRAG(ROOT)
    rag_a.budget_policy = "A"
    nodes = rag_c.manifest["nodes"]

    todo = []
    for idx, (row, q) in enumerate(jobs):
        if idx < args.start or idx >= args.end:
            continue
        f = jdir / f"case_{row['id']}_{q['style']}.json"
        if f.exists():
            print(f"skip {row['id']}_{q['style']}")
            continue
        todo.append((row, q))
    print(f"模型: {MODEL} | 待处理 {len(todo)} 条 | 检索阶段（顺序）…")

    prepared = []
    for row, q in todo:
        rc = rag_c.search(q["text"], mode="auto", profile="wu01")
        ra = rag_a.search(q["text"], mode="auto", profile="wu01")
        prepared.append((row, q, rc, ra))
    print("检索完成，进入 API 阶段（并行）…")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(case_work, row, q, rc, ra, nodes, jdir, refs) for row, q, rc, ra in prepared]
        done = 0
        for fu in as_completed(futs):
            done += 1
            try:
                print(f"[{done}/{len(futs)}] {fu.result()}")
            except Exception:  # noqa: BLE001
                print(f"[{done}/{len(futs)}] ERROR:\n{traceback.format_exc(limit=3)}")


def _mean(xs):
    return round(statistics.mean(xs), 3) if xs else None


def cmd_agg(args):
    out = Path(args.dir) if getattr(args, "dir", None) else JDIR
    cases = [json.loads(f.read_text(encoding="utf-8")) for f in sorted(out.glob("case_*.json"))]
    complete = [c for c in cases if isinstance(c.get("base", {}).get("judge"), dict) and isinstance(c.get("e6a", {}).get("judge"), dict)]
    n = len(complete)
    act = [c for c in complete if c["activated"]]
    unc = [c for c in complete if not c["activated"]]

    def stats(sub):
        def col(side, key):
            return [c[side]["judge"][key] for c in sub if isinstance(c[side]["judge"].get(key), int)]
        return {
            "n": len(sub),
            "base": {
                "faith": _mean(col("base", "faithfulness")),
                "corr": _mean(col("base", "correctness")),
                "prec_llm": _mean([c["base"]["prec_llm"] for c in sub]),
                "prec_gold": _mean([c["base"]["prec_gold"] for c in sub]),
            },
            "e6a": {
                "faith": _mean(col("e6a", "faithfulness")),
                "corr": _mean(col("e6a", "correctness")),
                "prec_llm": _mean([c["e6a"]["prec_llm"] for c in sub]),
                "prec_gold": _mean([c["e6a"]["prec_gold"] for c in sub]),
            },
        }

    agg = {"n_total_cases": len(cases), "n_complete": n,
           "all": stats(complete), "activated": stats(act), "unchanged": stats(unc)}
    for scope in ("all", "activated", "unchanged"):
        a = agg[scope]
        if a["n"]:
            a["delta"] = {k: (round(a["e6a"][k] - a["base"][k], 3) if (a["e6a"][k] is not None and a["base"][k] is not None) else None)
                          for k in ("faith", "corr", "prec_llm", "prec_gold")}
    (out / "AGG.json").write_text(json.dumps(agg, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")

    # 逐案简表
    lines = ["# 裁判层报告：Baseline vs E6-A（bench 侧）", "",
             f"- 模型: {complete[0]['model'] if complete else MODEL} · 完整案例 {n}/{len(cases)} · 激活集(上下文不同) {len(act)} · 未变集 {len(unc)}", ""]
    lines.append("| 范围 | 系统 | Faithfulness | Correctness | ContextPrec(LLM) | ContextPrec(gold) |")
    lines.append("|---|---|---|---|---|---|")
    for scope in ("all", "activated", "unchanged"):
        a = agg[scope]
        if not a["n"]:
            continue
        for side, name in (("base", "baseline"), ("e6a", "E6-A"), (None, "Δ")):
            if side:
                v = a[side]
                lines.append(f"| {scope} | {name} | {v['faith']} | {v['corr']} | {v['prec_llm']} | {v['prec_gold']} |")
            else:
                d = a["delta"]
                lines.append(f"| {scope} | **Δ** | **{d['faith']}** | **{d['corr']}** | **{d['prec_llm']}** | **{d['prec_gold']}** |")
    lines.append("")
    lines.append("## 逐案")
    lines.append("| case | activated | faith b/e | corr b/e | prec b/e | 备注 |")
    lines.append("|---|---|---|---|---|---|")
    for c in complete:
        b, e = c["base"], c["e6a"]
        note = "" if not c["activated"] else "★激活"
        jb, je = b["judge"].get("faithfulness"), e["judge"].get("faithfulness")
        cb, ce = b["judge"].get("correctness"), e["judge"].get("correctness")
        lines.append(f"| {c['id']}·{c['style']} | {note} | {jb}/{je} | {cb}/{ce} | {b['prec_llm']:.2f}/{e['prec_llm']:.2f} | |")
    (out / "JUDGE_REPORT.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")

    # 审计样本（随机 10 条，seed=42）
    rng = random.Random(42)
    sample = rng.sample(complete, min(10, len(complete)))
    out_lines = ["# 审计样本（随机 10 条，供人工核对 judge 可靠性）", ""]
    for c in sample:
        out_lines.append(f"## {c['id']}·{c['style']}（activated={c['activated']}）")
        out_lines.append(f"**问题**: {c['question']}")
        out_lines.append(f"**参考答案**: {c['ref']}")
        for side, name in (("base", "Baseline"), ("e6a", "E6-A")):
            s = c[side]
            out_lines.append(f"**{name} 回答**: {s['ans']}")
            out_lines.append(f"**{name} judge**: {json.dumps(s['judge'], ensure_ascii=False)}")
            out_lines.append(f"**{name} 块数/判相关数**: {s['n_blocks']}/{s['n_relevant']} · gold精率 {s['prec_gold']:.2f}")
        out_lines.append("")
    (out / "AUDIT_SAMPLE.md").write_text("\n".join(out_lines), encoding="utf-8", newline="\n")
    print(json.dumps(agg, ensure_ascii=False, indent=1))
    print("已写出 JUDGE_REPORT.md / AGG.json / AUDIT_SAMPLE.md")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--only", default=None)
    r.add_argument("--start", type=int, default=0)
    r.add_argument("--end", type=int, default=10**9)
    r.add_argument("--workers", type=int, default=6)
    r.add_argument("--file", default=None)
    s = sub.add_parser("agg")
    s.add_argument("--dir", default=None)
    args = ap.parse_args()
    if args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "agg":
        cmd_agg(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
