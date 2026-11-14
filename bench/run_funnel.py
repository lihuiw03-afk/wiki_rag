# -*- coding: utf-8 -*-
"""bench/run_funnel.py — 漏斗分析：从评测结果 JSON 分解 A/B/C/D 瓶颈（只读，不改系统）

用法：.venv/Scripts/python.exe bench/run_funnel.py [results_json]
默认读取最新的 retrieval_eval_*_multifix.json。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
default = sorted((ROOT / 'bench/results').glob('retrieval_eval_*_multifix.json'))
path = Path(sys.argv[1]) if len(sys.argv) > 1 else (default[-1] if default else None)
if not path:
    raise SystemExit('未找到评测结果 JSON')
d = json.loads(path.read_text(encoding='utf-8'))
qs = d['per_query']


def f(x):
    return bool(x is not None and float(x) > 1e-9)


n = len(qs)
never = [q for q in qs if not f(q.get('mrr'))]
in10 = [q for q in qs if f(q.get('page_cov@10'))]
not_sel = [q for q in in10 if not f(q.get('page_in_selected'))]
partial = [q for q in in10 if f(q.get('page_cov@10')) and float(q.get('page_in_selected') or 0) < 0.999]
soft_rank = [q for q in qs if f(q.get('page_cov@10')) and not f(q.get('page_cov@5'))]

print(f'来源: {path.name} | n={n}')
print(f'[A] 池外(mrr=0): {len(never)} -> ' + ', '.join(f"{q['id']}/{q['style']}" for q in never))
print(f'[B] top10 有、上下文全丢: {len(not_sel)} -> ' + ', '.join(f"{q['id']}/{q['style']}" for q in not_sel))
print(f'[B*] 部分覆盖: {len([q for q in partial if not f(q.get("page_in_selected")) or float(q.get("page_in_selected"))<0.999])} 条（含上述）')
print(f'[D] 仅排 6-10 位: {len(soft_rank)} -> ' + ', '.join(f"{q['id']}/{q['style']}" for q in soft_rank))
pool_has = n - len(never)
p10 = len(in10)
psel = len([q for q in qs if f(q.get('page_in_selected'))])
print(f'漏斗: 池 {pool_has}/{n}={pool_has/n:.1%} | top10 {p10}/{n}={p10/n:.1%} | 上下文 {psel}/{n}={psel/n:.1%}')
