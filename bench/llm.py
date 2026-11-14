# -*- coding: utf-8 -*-
"""bench/llm.py — bench 侧 LLM 客户端（DashScope compatible-mode，密钥读仓库外 vision.env）

- 密钥/端点/模型来自 C:/Users/HP/.config/company-wiki/vision.env（与生产 API 调用同源）
- 可用环境变量 BENCH_LLM_MODEL 覆盖模型（默认取 vision.env 的 COMPANY_WIKI_VISION_MODEL）
- 仅 bench 使用；不修改项目代码；密钥永不打印
"""
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

_ENV_PATH = Path("C:/Users/HP/.config/company-wiki/vision.env")


def _load_env():
    d = {}
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
    return d


_ENV = _load_env()
BASE_URL = _ENV.get("COMPANY_WIKI_VISION_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL = _ENV.get("BENCH_LLM_MODEL") or _ENV.get("COMPANY_WIKI_VISION_MODEL", "qwen3.8-flash")
_API_KEY = _ENV.get("COMPANY_WIKI_VISION_API_KEY", "")


def chat(system: str, user: str, temperature: float = 0.1, max_tokens: int = 1200, retries: int = 3,
         json_mode: bool = False) -> str:
    """一次 chat 调用，返回文本；失败重试（指数退避）。json_mode=True 时要求 JSON 输出。"""
    if not _API_KEY:
        raise RuntimeError("vision.env 缺少 COMPANY_WIKI_VISION_API_KEY")
    last_err = None
    body = ""
    use_json_mode = json_mode
    for attempt in range(retries):
        payload = {
            "model": MODEL,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        body_bytes = json.dumps(payload).encode("utf-8")
        try:
            req = urllib.request.Request(
                BASE_URL.rstrip("/") + "/chat/completions",
                data=body_bytes,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {_API_KEY}"},
            )
            with urllib.request.urlopen(req, timeout=150) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            last_err = e
            try:
                body = e.read().decode("utf-8", errors="ignore")[:300]
            except Exception:  # noqa: BLE001
                body = ""
            if use_json_mode and e.code == 400 and "response_format" in body:
                use_json_mode = False  # 端点不支持 response_format 时回退
            time.sleep(2.0 * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败: {last_err} | body: {body[:200]}")


def _extract_objs(text: str):
    """扫描文本中所有顶层 {...} 片段（容忍字符串内花括号/转义）。"""
    objs, depth, start, instr, esc = [], 0, None, False, False
    for i, ch in enumerate(text):
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
            continue
        if ch == '"':
            instr = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    objs.append(text[start:i + 1])
                    start = None
    return objs


def chat_json(system: str, user: str, temperature: float = 0.0, max_tokens: int = 1200, retries: int = 2,
              validator=None, keys_hint: str = None):
    """要求返回 JSON；多级容错（扫描/合并片段对象），validator(dict)->bool 校验，失败重试。"""
    prompt = user
    raw = ""
    for attempt in range(retries):
        raw = chat(system, prompt, temperature=temperature, max_tokens=max_tokens, json_mode=True)
        candidates = _extract_objs(raw)
        if not candidates:
            m = re.search(r"\{.*\}", raw, re.S)
            candidates = [m.group(0)] if m else []
        for c in candidates:
            try:
                obj = json.loads(c)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and (validator is None or validator(obj)):
                return obj
        merged = {}
        for c in candidates:
            try:
                d = json.loads(c)
                if isinstance(d, dict):
                    merged.update(d)
            except json.JSONDecodeError:
                pass
        if merged and (validator is None or validator(merged)):
            return merged
        hint = f"且必须包含以下键：{keys_hint}，不要输出任何其他键" if keys_hint else ""
        prompt = user + f"\n\n（注意：上次输出不合要求。请只输出一个合法 JSON 对象{hint}。）"
    raise RuntimeError(f"JSON 解析/校验失败，最后输出: {raw[:300]}")
