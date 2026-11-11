#!/usr/bin/env python3
from pathlib import Path
import json, sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from rag_gateway.config import load_config
from rag_gateway.projection import build_projection
from rag_gateway.router import choose_mode

stats = build_projection(ROOT, load_config(ROOT))
manifest = json.loads((ROOT / "rag/manifest.json").read_text(encoding="utf-8"))
graph = json.loads((ROOT / "rag/graph.json").read_text(encoding="utf-8"))
errors = []
for nid, n in manifest["nodes"].items():
    base = ROOT / ("rag/qmd/core" if n["collection"] == "company-core" else "rag/qmd/evidence")
    if not (base / n["projection"]).exists():
        errors.append(f"missing projection {nid}: {n['projection']}")
for raw, meta in manifest.get("raw_extracted", {}).items():
    ep = ROOT / "rag/extracted" / meta.get("path", "")
    if not ep.exists():
        errors.append(f"missing extracted source {raw}: {ep}")
for nid, n in manifest.get("nodes", {}).items():
    for asset in n.get("visual_assets") or []:
        ap = ROOT / asset
        if not ap.exists():
            errors.append(f"missing visual asset {nid}: {asset}")
for rel, nid in manifest["page_roots"].items():
    if nid not in manifest["nodes"]:
        errors.append(f"bad root {rel}: {nid}")
assert choose_mode("示例公司A是什么") == "fast"
assert choose_mode("综合比较示例公司A和示例制造的文化建设问题") == "deep"
print(json.dumps({"stats": stats, "graph_pages": len(graph["pages"]), "errors": errors[:20]}, ensure_ascii=False, indent=2))
raise SystemExit(1 if errors else 0)
