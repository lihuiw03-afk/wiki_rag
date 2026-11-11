#!/usr/bin/env python3
from pathlib import Path
import argparse,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rag_gateway.gateway import CompanyKnowledgeGateway
p=argparse.ArgumentParser(description="Unified Company Knowledge Gateway")
p.add_argument("query");p.add_argument("--profile",default=None);p.add_argument("--attachment",default=None)
p.add_argument("--route",choices=["auto","direct","company-rag","hot-document","hot+company"],default="auto")
p.add_argument("--mode",choices=["auto","fast","balanced","deep"],default="auto")
p.add_argument("--context-policy",choices=["auto","balanced","diversity","source-focused"],default="auto")
p.add_argument("--hyde",default=None);p.add_argument("--json",action="store_true");p.add_argument("--context-only",action="store_true")
a=p.parse_args();root=Path(__file__).resolve().parents[1]
r=CompanyKnowledgeGateway(root).ask(a.query,a.profile,a.attachment,a.route,a.mode,a.context_policy,a.hyde)
if a.context_only: print(r.get("context",""))
elif a.json: print(json.dumps(r,ensure_ascii=False,indent=2))
else:
    print(f"route={r.get('route')} elapsed={r.get('elapsed_ms')}ms")
    print(r.get("context","") or r.get("answer_policy",""))
