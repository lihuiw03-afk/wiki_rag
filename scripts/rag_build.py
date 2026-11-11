#!/usr/bin/env python3
from pathlib import Path
import json, sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag_gateway.config import load_config
from rag_gateway.projection import build_projection

root = Path(__file__).resolve().parents[1]
stats = build_projection(root, load_config(root))
print(json.dumps(stats, ensure_ascii=False, indent=2))
