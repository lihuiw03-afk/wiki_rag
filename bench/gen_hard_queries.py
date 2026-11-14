# -*- coding: utf-8 -*-
"""bench/gen_hard_queries.py — 生成 50 条困难查询（6 类，含金标标注与机械校验）

类别与配额：
  1. 长文档总结 ×9   2. 多来源融合 ×8   3. 跨章节 ×8
  4. 专有名词 ×8     5. 隐含关系 ×8     6. 原始证据 ×9

输出：bench/dataset/queries_hard50.jsonl
字段：id/category/difficulty/queries[{text,style}]/target_pages/target_sections/target_raws/notes/validated

用法: python bench/gen_hard_queries.py
"""
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
import os  # noqa: E402

os.chdir(ROOT)

from rag_gateway.search import CompanyRAG  # noqa: E402
from llm import chat_json, MODEL  # noqa: E402

OUT = ROOT / "bench/dataset/queries_hard50.jsonl"


def load_pages(rag):
    nodes = rag.manifest["nodes"]
    pages = {}
    for nid, n in nodes.items():
        pr = n.get("page_relpath")
        if not pr:
            continue
        p = pages.setdefault(pr, {"relpath": pr, "title": n.get("page_title") or "", "sections": [], "chars": 0, "content": ""})
        sec = n.get("title") or ""
        if sec == (n.get("page_title") or ""):
            sec = ""
        if sec and sec not in p["sections"]:
            p["sections"].append(sec)
        p["chars"] += len(n.get("content") or "")
        p["content"] += ("\n# " + sec + "\n" + (n.get("content") or "")) if sec else ("\n" + (n.get("content") or ""))
    return pages


def _strip_frontmatter(text):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for i in range(1, min(len(lines), 500)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1:])
    return text


def load_raws(rag, cap_per_raw=4000):
    nodes = rag.manifest["nodes"]
    raws = {}
    proj_root = ROOT / "rag/qmd/evidence"
    for nid, n in nodes.items():
        rp = n.get("raw_path")
        if not rp or n.get("collection") != "company-evidence":
            continue
        r = raws.setdefault(rp, {"raw_path": rp, "chars": 0, "content": ""})
        if r["chars"] >= cap_per_raw:
            continue
        proj = n.get("projection")
        text = ""
        if proj:
            f = proj_root / proj
            if f.exists():
                try:
                    text = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:  # noqa: BLE001
                    text = ""
        if not text:
            text = n.get("content") or ""
        text = _strip_frontmatter(text)
        r["content"] += text + "\n"
        r["chars"] = len(r["content"])
    return raws


def draft(cat, gold_desc, extra=""):
    """LLM 起草一条查询文本。"""
    system = "你是企业知识库评测集的出题人。题目必须能且只能由给定资料回答；用自然的中文提问，不要透露答案。"
    user = f"""类别：{cat}
【资料（golden source）】
{gold_desc[:2200]}

【出题要求】
{extra}

请输出 JSON：{{"query": "一条自然的中文问题", "style": "direct 或 indirect"}}"""
    j = chat_json(system, user, max_tokens=500, validator=lambda d: isinstance(d.get("query"), str) and len(d["query"]) >= 8,
                  keys_hint="query, style")
    return j["query"].strip(), j.get("style", "direct")


def has_any(text, tokens):
    return [t for t in tokens if t and t in text]


def main():
    rag = CompanyRAG(ROOT)
    pages = load_pages(rag)
    raws = load_raws(rag)
    plist = sorted(pages.values(), key=lambda p: -p["chars"])
    print(f"页面 {len(pages)} · 原始来源 {len(raws)} · 模型 {MODEL}")

    items = []
    jobs = []  # (cat, id, payload_builder_fn)
    idx = [0]

    def add(cat, tpages, tsections, traws, notes):
        idx[0] += 1
        qid = f"H{idx[0]:02d}"
        return {"id": qid, "cat": cat, "tpages": tpages, "tsections": tsections, "traws": traws, "notes": notes}

    # ---- 1. 长文档总结 ×9（本语料最大页面档，>=2500 字符）
    for p in [x for x in plist if x["chars"] >= 2500][:9]:
        it = add("长文档总结", [p["relpath"]], p["sections"][:6], [], f"页内容 {p['chars']} 字符（全库最长档）")
        jobs.append(("长文档总结", it, lambda p=p: draft(
            "长文档总结",
            f"页面《{p['title']}》内容摘录：\n{p['content'][:1800]}",
            f"要求：用户请求完整总结《{p['title']}》这一页的核心内容与脉络；问题中必须出现页面标题《{p['title']}》。")))

    # ---- 2. 多来源融合 ×8（找共同提及的页对，伙伴去重保多样性）
    pairs = []
    used_b = set()
    for a in plist[:30]:
        if len(a["title"]) < 4 or a["chars"] < 1200:
            continue
        for b in plist:
            if b is a or b["chars"] < 1200 or b["relpath"] in used_b or b["relpath"] == a["relpath"]:
                continue
            if a["title"] in b["content"]:
                pairs.append((a, b))
                used_b.add(b["relpath"])
                break
        if len(pairs) >= 8:
            break
    chosen = pairs[:8]
    for a, b in chosen:
        it = add("多来源融合", [a["relpath"], b["relpath"]], [], [], f"共现对：{a['title']} × {b['title']}")
        jobs.append(("多来源融合", it, lambda a=a, b=b: draft(
            "多来源融合",
            f"页面A《{a['title']}》：{a['content'][:900]}\n\n页面B《{b['title']}》：{b['content'][:900]}",
            f"要求：问题需要综合《{a['title']}》与《{b['title']}》两页的信息才能完整回答（例如对比、关联、同一主题的两面）；问题中提及这两页的对象。")))

    # ---- 3. 跨章节 ×8
    multif = [p for p in plist if len(p["sections"]) >= 6][:8]
    for p in multif:
        secs = p["sections"]
        it = add("跨章节", [p["relpath"]], secs[:5], [], f"含 {len(secs)} 个章节，需跨章组合")
        jobs.append(("跨章节", it, lambda p=p, secs=secs: draft(
            "跨章节",
            f"页面《{p['title']}》章节：{secs}\n内容摘录：{p['content'][:1600]}",
            f"要求：问题需要同时用到该页多个不同章节的信息（跨章节综合），问题中提及《{p['title']}》。")))

    # ---- 4. 专有名词 ×8（简称/别名）
    alias_jobs = []
    for p in plist:
        m = re.search(r"（简称([^）]{2,10})）|简称[：:]\s*([^\s，。；]{2,10})", p["content"][:6000])
        if not m:
            continue
        alias = (m.group(1) or m.group(2) or "").strip().strip('"\'“”「」【】')
        if not alias or len(alias) < 2 or len(p["title"]) < 6:
            continue
        alias_jobs.append((p, alias))
        if len(alias_jobs) >= 8:
            break
    # 不足时用长标题前缀补齐
    if len(alias_jobs) < 8:
        for p in plist:
            if len(p["title"]) >= 8 and p not in [x[0] for x in alias_jobs]:
                alias = p["title"][:4]
                alias_jobs.append((p, alias))
            if len(alias_jobs) >= 8:
                break
    for p, alias in alias_jobs[:8]:
        it = add("专有名词", [p["relpath"]], [], [], f"别名“{alias}”指向《{p['title']}》")
        jobs.append(("专有名词", it, lambda p=p, alias=alias: draft(
            "专有名词",
            f"页面《{p['title']}》内容摘录：{p['content'][:1200]}",
            f"要求：问题中使用该对象在资料中的简称/别名“{alias}”（不要用全称），询问其关键事实（如定位、成果、构成）。")))

    # ---- 5. 隐含关系 ×8（不含名称的指代式提问）
    implicit_src = [p for p in plist if 4 <= len(p["title"]) <= 16 and p["chars"] >= 1200][:80]
    step = max(1, len(implicit_src) // 8)
    for p in implicit_src[::step][:8]:
        toks = [p["title"]] + [p["title"][i:i + 3] for i in range(len(p["title"]) - 2)]
        it = add("隐含关系", [p["relpath"]], [], [], f"指代《{p['title']}》但不许出现其名称")
        jobs.append(("隐含关系", it, lambda p=p, toks=toks: (
            draft("隐含关系",
                  f"页面《{p['title']}》内容摘录：{p['content'][:1400]}",
                  "要求：写一个用户口吻的指代式问题（例如“那家/那个以……著称的机构，……”），完全不要出现该对象的名称或其任何 3 字以上连续片段；让读者能通过描述唯一锁定该对象。"),
            toks)))

    # ---- 6. 原始证据 ×9
    raw_cand = sorted([r for r in raws.values() if r["chars"] >= 800],
                      key=lambda r: -(len(re.findall(r"\d", r["content"])) / max(1, r["chars"])))
    picked = raw_cand[:9]
    for r in picked:
        tags = [x for x in ("调研", "访谈", "问卷", "记录", "磋商", "报价", "设计", "报告") if x in r["raw_path"] or x in r["content"][:500]]
        label = "/".join(tags[:2]) or "原始文档"
        it = add("原始证据", [], [], [r["raw_path"]], f"{label}，数字密度高")
        jobs.append(("原始证据", it, lambda r=r: draft(
            "原始证据",
            f"原始文档（节选）：{r['content'][:2000]}",
            "要求：问题必须能且只能由该原始文档中的具体数据/原话回答（如数值、比例、原句），不要问需要概括推理的内容。")))

    print(f"共 {len(jobs)} 条任务，开始起草…")
    results = []
    fails = 0

    def work(cat, it, fn):
        nonlocal fails
        try:
            out = fn()
            if isinstance(out, tuple) and len(out) >= 2 and isinstance(out[0], tuple):
                (q, style), toks = out[0], out[1]
            elif isinstance(out, tuple):
                q, style = out[0], out[1]
                toks = out[2] if len(out) > 2 else []
            else:
                q, style, toks = out, "direct", []
            # 校验
            ok, why = True, ""
            if cat == "长文档总结" and it["tpages"][0]:
                title = pages[it["tpages"][0]]["title"]
                if title not in q:
                    ok, why = False, "未包含标题"
            if cat == "多来源融合":
                got = 0
                for rp in it["tpages"]:
                    t = pages[rp]["title"]
                    if t in q or (len(t) >= 4 and t[:4] in q):
                        got += 1
                if got < 2:
                    ok, why = False, "未同时提及两个对象"
            if cat == "跨章节":
                t = pages[it["tpages"][0]]["title"]
                if t not in q:
                    ok, why = False, "未包含标题"
            if cat == "专有名词":
                alias = it["notes"].split("“")[1].split("”")[0].strip('"\'“”') if "“" in it["notes"] else ""
                if alias and alias not in q:
                    ok, why = False, "未使用别名"
            if cat == "隐含关系":
                bad = [x for x in toks if len(x) >= 3 and x in q]
                if bad or pages[it["tpages"][0]]["title"] in q:
                    ok, why = False, f"出现名称片段 {bad[:2]}"
            item = {"id": it["id"], "category": cat, "difficulty": "困难",
                    "queries": [{"text": q, "style": style if cat != "隐含关系" else "indirect"}],
                    "target_pages": it["tpages"], "target_sections": it["tsections"],
                    "target_raws": it["traws"], "notes": it["notes"],
                    "validated": ok, "validator": why}
            return item
        except Exception as e:  # noqa: BLE001
            fails += 1
            return {"id": it["id"], "category": cat, "error": str(e)[:120]}

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(work, c, it, fn) for c, it, fn in jobs]
        for fu in as_completed(futs):
            results.append(fu.result())

    results.sort(key=lambda x: x["id"])
    good = [r for r in results if r.get("validated")]
    bad = [r for r in results if not r.get("validated")]
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in results), encoding="utf-8", newline="\n")
    print(f"\n已写出 {OUT} —— 合格 {len(good)}/{len(results)}（不合格 {len(bad)}，失败 {fails}）")
    for r in results:
        flag = "OK " if r.get("validated") else "!! "
        print(f"  {flag}{r['id']} [{r['category']}] {r.get('queries', [{}])[0].get('text', r.get('error', ''))[:60]}")
    cats = {}
    for r in results:
        cats.setdefault(r["category"], [0, 0])
        cats[r["category"]][0] += 1
        cats[r["category"]][1] += 1 if r.get("validated") else 0
    print("\n分类统计:", {k: f"{v[1]}/{v[0]}" for k, v in cats.items()})


if __name__ == "__main__":
    main()
