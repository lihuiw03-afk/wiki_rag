#!/usr/bin/env python3
from pathlib import Path
from urllib import request
import json, shutil, sys
ROOT = Path(__file__).resolve().parents[1]

checks = {}
checks['qmd_cli'] = bool(shutil.which('qmd'))
checks['manifest'] = (ROOT / 'rag/manifest.json').exists()
checks['graph'] = (ROOT / 'rag/graph.json').exists()
checks['core_docs'] = sum(1 for _ in (ROOT / 'rag/qmd/core').rglob('*.md')) if (ROOT / 'rag/qmd/core').exists() else 0
checks['evidence_docs'] = sum(1 for _ in (ROOT / 'rag/qmd/evidence').rglob('*.md')) if (ROOT / 'rag/qmd/evidence').exists() else 0
try:
    with request.urlopen('http://127.0.0.1:8181/health', timeout=2) as r:
        checks['qmd_http'] = 200 <= r.status < 300
except Exception:
    checks['qmd_http'] = False
try:
    # 探针查询：验证 daemon 服务的索引有真实命中（防"空索引 daemon"——错误目录启动的典型症状）
    body = json.dumps({
        'searches': [{'type': 'lex', 'query': '企业'}],
        'collections': ['company-core'], 'limit': 1, 'candidateLimit': 5,
        'minScore': 0.0, 'rerank': False, 'intent': '',
    }).encode('utf-8')
    req = request.Request('http://127.0.0.1:8181/query', data=body,
                          headers={'Content-Type': 'application/json'}, method='POST')
    with request.urlopen(req, timeout=10) as r:
        checks['qmd_probe'] = b'"file"' in r.read()
except Exception:
    checks['qmd_probe'] = False

print(json.dumps(checks, ensure_ascii=False, indent=2))
raise SystemExit(0 if checks['manifest'] and checks['graph'] else 2)
