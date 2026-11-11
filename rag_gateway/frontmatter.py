from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml


def parse_frontmatter_text(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + 5:]
    try:
        meta = yaml.safe_load(raw) or {}
    except Exception:
        meta = {}
    return meta, body


def read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    return parse_frontmatter_text(path.read_text(encoding="utf-8", errors="replace"))


def dump_frontmatter(meta: dict[str, Any]) -> str:
    payload = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=120).strip()
    return f"---\n{payload}\n---\n"
