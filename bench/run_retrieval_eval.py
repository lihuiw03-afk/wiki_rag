# -*- coding: utf-8 -*-
"""bench/run_retrieval_eval.py — 检索质量评测 v2（双口径：全链路含 gate / 检索层）

用法（从 cwr24 根目录）：
  .venv/Scripts/python.exe bench/run_retrieval_eval.py [tag]
输出：
  bench/results/retrieval_eval_<date>[_<tag>].json / .md

口径说明：
- 全链路（e2e）：只用 gateway 自动路由的真实结果（不重试）——反映用户实际体验；
  gate 误判 direct 的查询按 0 计。
- 检索层（retrieval）：若自动路由非 company-rag，则强制 route=company-rag 重跑——
  独立衡量"检索器本身"在这些问题上的能力。
"""
from __future__ import annotations
import hashlib, json, os, statistics, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from rag_gateway.gateway import CompanyKnowledgeGateway

DS = ROOT / "bench/dataset/queries.jsonl"
KS = (5, 10)
TODAY = time.strftime("%Y%m%d")
TAG = sys.argv[1] if len(sys.argv) > 1 else "run"


def pct(x):
    return f"{100 * x:.0f}%"


def agg(values):
    return statistics.mean(values) if values else 0.0


def main():
    rows = [json.loads(l) for l in DS.read_text(encoding="utf-8").splitlines() if l.strip()]
    ds_hash = hashlib.sha256(DS.read_bytes()).hexdigest()[:12]
    m = json.loads((ROOT / "rag/manifest.json").read_text(encoding="utf-8"))
    nodes = m["nodes"]
    g = CompanyKnowledgeGateway(ROOT)

    def metas(items):
        pages, raws = set(), set()
        for it in items:
            n = nodes.get(it.get("node_id")) or {}
            if n.get("page_relpath"):
                pages.add(n["page_relpath"])
            if n.get("raw_path"):
                raws.add(n["raw_path"])
        return pages, raws

    def metrics_from(r, gold_pages, gold_raws):
        out = {}
        retr = sorted(r.get("retrieved") or [], key=lambda x: x.get("score") or 0, reverse=True)
        sel = r.get("selected") or []
        for k in KS:
            rp, rr = metas(retr[:k])
            out[f"page_cov@{k}"] = len(gold_pages & rp) / max(1, len(gold_pages))
            out[f"raw_hit@{k}"] = 1.0 if (gold_raws & rr) else 0.0
        sp, sr = metas(sel)
        out["page_in_selected"] = len(gold_pages & sp) / max(1, len(gold_pages))
        out["raw_in_selected"] = 1.0 if (gold_raws & sr) else 0.0
        rank = next((i + 1 for i, it in enumerate(retr)
                     if ((nodes.get(it.get("node_id")) or {}).get("page_relpath") in gold_pages)), None)
        out["mrr"] = (1.0 / rank) if rank else 0.0
        return out

    results = []
    total = sum(len(r["queries"]) for r in rows)
    n_done = 0
    for row in rows:
        gold_pages = set(row["target_pages"])
        gold_raws = set(row["target_raws"])
        for q in row["queries"]:
            n_done += 1
            rec = {"id": row["id"], "category": row["category"], "style": q["style"],
                   "query": q["text"], "n_gold_pages": len(gold_pages)}
            try:
                r_auto = g.ask(q["text"], profile="wu01")
                rec["auto_route"] = r_auto.get("route")
                rec["mode"] = r_auto.get("mode")
                rec["escalated_from"] = r_auto.get("escalated_from")
                rec["elapsed_ms"] = r_auto.get("elapsed_ms") or 0
                rec.update({f"e2e_{k}": v for k, v in metrics_from(r_auto, gold_pages, gold_raws).items()})
                r_ret = r_auto
                if r_auto.get("route") != "company-rag":
                    try:
                        r_ret = g.ask(q["text"], profile="wu01", route="company-rag")
                        rec["forced"] = True
                        rec["mode"] = r_ret.get("mode")
                        rec["escalated_from"] = r_ret.get("escalated_from")
                    except Exception as e:
                        rec["forced_error"] = str(e)[:120]
                rec.update(metrics_from(r_ret, gold_pages, gold_raws))
                rec["selected_n"] = len(r_ret.get("selected") or [])
            except Exception as e:
                rec["error"] = str(e)[:160]
            results.append(rec)
            print(f"[{n_done}/{total}] {rec['id']}/{rec['style']}: e2e@5={rec.get('e2e_page_cov@5', 0):.2f} "
                  f"ret@5={rec.get('page_cov@5', 0):.2f} ret@10={rec.get('page_cov@10', 0):.2f} "
                  f"route={rec.get('auto_route')} esc={rec.get('escalated_from')} {rec.get('elapsed_ms', 0):.0f}ms")

    lat2 = []
    for row in rows:
        for q in row["queries"]:
            try:
                r = g.ask(q["text"], profile="wu01")
                lat2.append(r.get("elapsed_ms") or 0)
            except Exception:
                pass

    e2e_keys = ["e2e_page_cov@5", "e2e_page_cov@10", "e2e_page_in_selected", "e2e_raw_in_selected"]
    ret_keys = ["page_cov@5", "page_cov@10", "raw_hit@5", "raw_hit@10",
                "page_in_selected", "raw_in_selected", "mrr"]

    def summarize(sub):
        s = {"n": len(sub),
             "gate_rate": agg([1.0 if x.get("auto_route") == "company-rag" else 0.0 for x in sub]),
             "escalations": sum(1 for x in sub if x.get("escalated_from") == "fast")}
        for k in e2e_keys + ret_keys:
            s[k] = agg([x.get(k, 0) for x in sub])
        return s

    cats = sorted({x["category"] for x in results})
    styles = sorted({x["style"] for x in results})
    lat1 = [x.get("elapsed_ms", 0) for x in results if x.get("elapsed_ms")]
    summary = {
        "dataset_hash": ds_hash, "date": TODAY, "tag": TAG,
        "n_rows": len(rows), "n_queries": len(results),
        "overall": summarize(results),
        "by_category": {c: summarize([x for x in results if x["category"] == c]) for c in cats},
        "by_style": {s: summarize([x for x in results if x["style"] == s]) for s in styles},
        "latency_ms": {
            "pass1_fresh": {"mean": agg(lat1),
                            "p50": statistics.median(lat1) if lat1 else 0,
                            "p90": sorted(lat1)[min(len(lat1) - 1, int(len(lat1) * 0.9))] if lat1 else 0},
            "pass2_repeat": {"mean": agg(lat2),
                             "p50": statistics.median(lat2) if lat2 else 0,
                             "p90": sorted(lat2)[min(len(lat2) - 1, int(len(lat2) * 0.9))] if lat2 else 0},
        },
    }

    out_dir = ROOT / "bench/results"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"retrieval_eval_{TODAY}_{TAG}"
    (out_dir / f"{base}.json").write_text(
        json.dumps({"summary": summary, "per_query": results}, ensure_ascii=False, indent=1),
        encoding="utf-8", newline="\n")

    lines = [f"# 检索质量评测（{TODAY} / {TAG}）", "",
             f"数据集: {summary['n_rows']} 目标 / {summary['n_queries']} 查询 | hash `{ds_hash}`", "",
             "| 分组 | n | 全链路@5 | 全链路入选 | 检索层@5 | 检索层@10 | 来源@10 | 页入选 | MRR | gate |",
             "|---|---|---|---|---|---|---|---|---|---|"]

    def ln(name, s):
        return (f"| {name} | {s['n']} | {pct(s['e2e_page_cov@5'])} | {pct(s['e2e_page_in_selected'])} | "
                f"{pct(s['page_cov@5'])} | {pct(s['page_cov@10'])} | {pct(s['raw_hit@10'])} | "
                f"{pct(s['page_in_selected'])} | {s['mrr']:.2f} | {pct(s['gate_rate'])} |")

    lines.append(ln("**总体**", summary["overall"]))
    for c in cats:
        lines.append(ln(f"cat: {c}", summary["by_category"][c]))
    for s in styles:
        lines.append(ln(f"style: {s}", summary["by_style"][s]))
    lat = summary["latency_ms"]
    lines += ["", "## 延迟（服务内 ms）", "",
              f"- 首过（fresh）: mean {lat['pass1_fresh']['mean']:.0f} / p50 {lat['pass1_fresh']['p50']:.0f} / p90 {lat['pass1_fresh']['p90']:.0f}",
              f"- 重复（repeat）: mean {lat['pass2_repeat']['mean']:.0f} / p50 {lat['pass2_repeat']['p50']:.0f} / p90 {lat['pass2_repeat']['p90']:.0f}",
              f"- fast 零命中自动升级：{summary['overall']['escalations']} 例"]
    (out_dir / f"{base}.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
