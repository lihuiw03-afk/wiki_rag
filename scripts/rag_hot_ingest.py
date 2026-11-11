#!/usr/bin/env python3
from pathlib import Path
import argparse,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rag_gateway.config import load_config
from rag_gateway.hot import HotDocumentStore
p=argparse.ArgumentParser();p.add_argument("file");p.add_argument("--profile",default=None);args=p.parse_args()
root=Path(__file__).resolve().parents[1]
print(json.dumps(HotDocumentStore(root,load_config(root)).ingest(Path(args.file),args.profile),ensure_ascii=False,indent=2))
