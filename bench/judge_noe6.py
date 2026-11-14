# -*- coding: utf-8 -*-
"""bench/judge_noe6.py — V2-NoE6 质量抽查：control vs 机制（在微变/激活行上）

复用 judge.py 的加固调用（_gen_answer / judge_call）；control 侧优先复用已有案例文件，
缺失时现场生成。产物：bench/results/judge/noe6/{set}_{variant}_{key}.json（可续跑）。
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

from rag_gateway.search import CompanyRAG  # noqa: E402
from run_experiments import PolicyRAG, E4RAG  # noqa: E402
from judge import _gen_answer, judge_call  # noqa: E402
from gen_reference import gold_text, REF_SYS, REF_USER  # noqa: E402
from llm import chat, MODEL  # noqa: E402

R = ROOT / "bench/results"
J = R / "judge"
JN = J / "noe6"
JN.mkdir(parents=True, exist_ok=True)
HYDE = json.loads((R / "hyde_cache.json").read_text(encoding="utf-8"))


def load(p):
    return json.loads((R / p).read_text(encoding="utf-8"))


def snap(per):
    return {q["id"] + "|" + q["style"]: q for q in per}


DATASETS = {}
for f in ("bench/dataset/queries.jsonl", "bench/dataset/queries_hard50.jsonl"):
    for line in (ROOT / f).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            for q in r["queries"]:
                DATASETS[r["id"] + "|" + q["style"]] = (r, q)

SETS = [("58集", "exp_e0_control_r4.json", "exp_v2c_{v}_r1.json"),
        ("困难集", "exp_h50_control_r3.json", "exp_v2hc_{v}_r1.json")]
NEED = {"e4": 2, "res2": 1, "res6": 2, "aug8": 1}
HYDE_ROWS = {"58集": ["E06|indirect"], "困难集": ["H10|direct", "H13|direct", "H39|indirect", "H41|indirect"]}

picks = []
for name, cf, pat in SETS:
    C = snap(load(cf)["per_query"])
    for v, n in NEED.items():
        V = snap(load(pat.format(v=v))["per_query"])
        micro = [k for k in C if (V[k].get("blocks"), V[k].get("chars")) != (C[k].get("blocks"), C[k].get("chars"))]
        micro.sort(key=lambda k: -abs(V[k].get("chars", 0) - C[k].get("chars", 0)))
        for k in micro[:n]:
            picks.append((name, v, k))
    for k in HYDE_ROWS[name]:
        picks.append((name, "hyde", k))

print(f"抽查（{len(picks)} 行）：")
for p in picks:
    print("  ", p)


def make_rag(variant):
    rag = PolicyRAG(ROOT)
    rag.budget_policy = "control"
    if variant == "e4":
        rag = E4RAG(ROOT)
        rag.budget_policy = "control"
    elif variant.startswith("res"):
        v = int(variant[3:])
        for pol in rag.config["context"]["policies"].values():
            pol["reserve_evidence_hits"] = v
    elif variant.startswith("aug"):
        rag.config["structure"]["max_augmented_hits"] = int(variant[3:])
    return rag


def ref_for(key, setname):
    rid, style = key.split("|")
    for d in (J / "refs", J / "h50" / "refs"):
        f = d / f"{rid}_{style}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))["ref"]
    r, q = DATASETS[key]
    gold = gold_text(CompanyRAG(ROOT), r)
    ref = chat(REF_SYS, REF_USER.format(gold=gold, q=q["text"]), temperature=0.1)
    outd = J / "h50" / "refs" if setname == "困难集" else J / "refs"
    outd.mkdir(parents=True, exist_ok=True)
    (outd / f"{rid}_{style}.json").write_text(
        json.dumps({"id": rid, "style": style, "text": q["text"], "ref": ref, "gold_chars": len(gold)},
                   ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return ref


def control_side(key):
    rid, style = key.split("|")
    for d in (J, J / "h50"):
        f = d / f"case_{rid}_{style}.json"
        if f.exists():
            c = json.loads(f.read_text(encoding="utf-8"))
            b = c.get("base") or {}
            if isinstance(b.get("judge"), dict) and b["judge"]:
                return {"ans": b.get("ans"), "judge": b["judge"], "reused": True}
    return None


# 阶段 1：顺序检索（变体侧 ctx；control 侧复用或现场检索）
rag_cache = {}
plan = []
for name, cf, pat in SETS:
    for setname, variant, key in [p for p in picks if p[0] == name]:
        r, q = DATASETS[key]
        safe_key = key.replace("|", "_")
        if (JN / f"{setname}_{variant}_{safe_key}.json").exists():
            print(f"skip {setname}_{variant}_{key}")
            continue
        vk = variant
        if vk not in rag_cache:
            rag_cache[vk] = make_rag(vk)
        rag = rag_cache[vk]
        kwargs = {}
        if variant == "hyde":
            kwargs["hyde"] = HYDE.get(q["text"])
        res = rag.search(q["text"], mode="auto", profile="wu01", **kwargs)
        ctx_v = res.get("context") or ""
        ctrl = control_side(key)
        ctx_c = None
        if ctrl is None:
            if "crag" not in rag_cache:
                crag = PolicyRAG(ROOT)
                crag.budget_policy = "control"
                rag_cache["crag"] = crag
            rc = rag_cache["crag"].search(q["text"], mode="auto", profile="wu01")
            ctx_c = rc.get("context") or ""
        plan.append({"set": setname, "variant": variant, "key": key, "q": q["text"], "ctx_v": ctx_v,
                     "ctrl": ctrl, "ctx_c": ctx_c})

print(f"检索完成，进入 API 阶段（{len(plan)} 行）…")


def work(item):
    ref = ref_for(item["key"], item["set"])
    ans_v, _ = _gen_answer(item["ctx_v"], item["q"])
    j_v = judge_call(item["q"], ref, ans_v, item["ctx_v"])
    out = {"set": item["set"], "mech": item["variant"], "key": item["key"],
           "result": {"ans": ans_v, "judge": j_v}}
    if item["ctrl"] is not None:
        out["control"] = item["ctrl"]
    else:
        ans_c, _ = _gen_answer(item["ctx_c"], item["q"])
        j_c = judge_call(item["q"], ref, ans_c, item["ctx_c"])
        out["control"] = {"ans": ans_c, "judge": j_c, "reused": False}
    safe_key = item["key"].replace("|", "_")
    (JN / f"{item['set']}_{item['variant']}_{safe_key}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    return f"{item['set']} {item['variant']} {item['key']}: control {out['control']['judge'].get('correctness')} -> variant {j_v.get('correctness')}"


with ThreadPoolExecutor(max_workers=4) as ex:
    futs = [ex.submit(work, it) for it in plan]
    for fu in as_completed(futs):
        try:
            print("[ok]", fu.result())
        except Exception as e:  # noqa: BLE001
            print("[ERR]", str(e)[:160])

# 汇总
print("\n=== 抽查汇总（faith / corr）===")
import statistics  # noqa: E402
agg = {}
for f in sorted(JN.glob("*.json")):
    d = json.loads(f.read_text(encoding="utf-8"))
    cj, vj = d["control"]["judge"], d["result"]["judge"]
    k = (d["set"], d["mech"])
    agg.setdefault(k, []).append((cj.get("faithfulness"), cj.get("correctness"), vj.get("faithfulness"), vj.get("correctness")))
for (s, v), rows in sorted(agg.items()):
    fc = statistics.mean([r[0] for r in rows if r[0] is not None])
    cc = statistics.mean([r[1] for r in rows if r[1] is not None])
    fv = statistics.mean([r[2] for r in rows if r[2] is not None])
    cv = statistics.mean([r[3] for r in rows if r[3] is not None])
    print(f"  {s:4s} {v:6s} n={len(rows):2d} | control {fc:.2f}/{cc:.2f} -> 机制 {fv:.2f}/{cv:.2f}")
print("模型:", MODEL)
