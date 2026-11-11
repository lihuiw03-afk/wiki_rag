from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def load_config(root: Path) -> dict[str, Any]:
    cfg = load_yaml(root / "config" / "rag.yaml")
    cfg["_root"] = str(root.resolve())
    return cfg


def resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p
