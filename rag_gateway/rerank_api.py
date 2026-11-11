from __future__ import annotations
from pathlib import Path
import json
import os
from urllib import request, error


class RerankApiError(RuntimeError):
    pass


def load_env_value(key_var: str, key_file: str | None = None) -> str | None:
    """Resolve a secret: environment first, then a KEY=VALUE file (out-of-repo, never logged)."""
    v = os.environ.get(key_var)
    if v and v.strip():
        return v.strip()
    if key_file:
        p = Path(key_file)
        try:
            if p.exists():
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, val = line.partition("=")
                    if k.strip() == key_var:
                        val = val.strip().strip('"').strip("'")
                        return val or None
        except OSError:
            return None
    return None


class ApiReranker:
    """Cloud rerank (DashScope qwen3-rerank, OpenAI-style batch scoring).

    One API call scores ALL candidates at once (~0.2-1s for our pool sizes) instead of the
    local per-candidate GGUF rerank (~0.5-2.8s x N on the T1000). Fail-soft by design: callers
    catch RerankApiError and fall back to local rerank / RRF ordering.
    """

    def __init__(self, cfg: dict | None):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", False))
        self.base_url = str(cfg.get("base_url", "")).rstrip("/")
        self.path = str(cfg.get("path", "/api/v1/services/rerank/text-rerank/text-rerank"))
        self.model = str(cfg.get("model", "qwen3-rerank"))
        self.key_var = str(cfg.get("key_var", "COMPANY_WIKI_VISION_API_KEY"))
        self.key_file = cfg.get("key_file")
        self.timeout = int(cfg.get("timeout_seconds", 20))
        self.max_doc_chars = int(cfg.get("max_doc_chars", 4000))
        self.fetch_min_score = float(cfg.get("fetch_min_score", 0.05))
        self.fallback = str(cfg.get("fallback", "local"))

    def available(self) -> bool:
        return bool(self.enabled and self.base_url and load_env_value(self.key_var, self.key_file))

    def _post(self, payload: dict) -> dict:
        key = load_env_value(self.key_var, self.key_file)
        if not key:
            raise RerankApiError(f"api key {self.key_var} not found")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.base_url + self.path,
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read().decode("utf-8")
        except error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            raise RerankApiError(f"HTTP {e.code}: {detail}") from e
        except Exception as e:  # URLError / timeout / socket errors
            raise RerankApiError(f"{type(e).__name__}: {e}") from e
        try:
            return json.loads(raw)
        except ValueError as e:
            raise RerankApiError(f"bad json: {raw[:200]}") from e

    def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[dict]:
        """Score all documents in one call.

        Returns [{'index': int, 'relevance_score': float}] sorted by score desc. Accepts both
        DashScope response shapes: {"output": {"results": ...}} and {"results": ...}.
        """
        docs = [(d or "").strip()[: self.max_doc_chars] for d in documents][:128]
        if not docs:
            return []
        docs = [d or "（空）" for d in docs]
        payload = {
            "model": self.model,
            "input": {"query": query, "documents": docs},
            "parameters": {"return_documents": False},
        }
        if top_n:
            payload["parameters"]["top_n"] = int(top_n)
        parsed = self._post(payload)
        results = []
        if isinstance(parsed, dict):
            results = ((parsed.get("output") or {}).get("results")) or parsed.get("results") or []
        out: list[dict] = []
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            score = item.get("relevance_score", item.get("score"))
            try:
                if idx is None or score is None:
                    continue
                out.append({"index": int(idx), "relevance_score": float(score)})
            except (TypeError, ValueError):
                continue
        out.sort(key=lambda x: x["relevance_score"], reverse=True)
        return out
