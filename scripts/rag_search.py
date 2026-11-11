#!/usr/bin/env python3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rag_gateway.cli import main

root = Path(__file__).resolve().parents[1]
raise SystemExit(main(["--root", str(root), "search", *sys.argv[1:]]))
