"""bench/ds.py — DashScope（百炼）小工具：从 vision.env 读密钥，兼容模式聊天调用。

密钥仓库外（沿用 vision.env）；不打印密钥、不落盘。
"""
from __future__ import annotations
from pathlib import Path
import json
from urllib import request, error

ENV_FILE = Path(r"C:/Users/HP/.config/company-wiki/vision.env")


def _load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def chat(prompt: str, system: str | None = None, model: str | None = None,
         temperature: float = 0.2, timeout: int = 90, retries: int = 2) -> str:
    env = _load_env()
    base = env.get("COMPANY_WIKI_VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    key = env.get("COMPANY_WIKI_VISION_API_KEY", "")
    model = model or env.get("COMPANY_WIKI_VISION_MODEL", "qwen3.8-flash")
    if not key:
        raise RuntimeError("COMPANY_WIKI_VISION_API_KEY missing")
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    payload = {"model": model, "messages": messages, "temperature": temperature}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            req = request.Request(base + "/chat/completions", data=data,
                                  headers={"Content-Type": "application/json",
                                           "Authorization": f"Bearer {key}"}, method="POST")
            with request.urlopen(req, timeout=timeout) as r:
                parsed = json.loads(r.read().decode("utf-8"))
            return parsed["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001 — 评测工具，重试后抛出
            last = e
    raise RuntimeError(f"chat failed: {last}")
