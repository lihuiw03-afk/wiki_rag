from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
from urllib import request, error
import json, shutil, subprocess


class QMDClientError(RuntimeError):
    pass


@dataclass
class QMDHit:
    file: str
    title: str
    score: float
    snippet: str = ""
    docid: str = ""
    collection: str = ""
    raw: dict | None = None

    def to_dict(self):
        d = asdict(self)
        d.pop("raw", None)
        return d


class QMDClient:
    def __init__(self, base_url: str, timeout: int = 30, cli_fallback: bool = True):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cli_fallback = cli_fallback

    def health(self) -> bool:
        try:
            with request.urlopen(self.base_url + "/health", timeout=2) as r:
                return 200 <= r.status < 300
        except Exception:
            return False

    def query(self, *, searches: list[dict], collections: list[str], limit: int, candidate_limit: int,
              min_score: float, rerank: bool, intent: str = "") -> list[QMDHit]:
        payload = {
            "searches": searches,
            "collections": collections,
            "limit": limit,
            "candidateLimit": candidate_limit,
            "minScore": min_score,
            "rerank": rerank,
            "intent": intent,
        }
        try:
            return self._http_query(payload)
        except Exception as exc:
            if not self.cli_fallback:
                raise QMDClientError(str(exc)) from exc
            return self._cli_query(payload, exc)

    def _http_query(self, payload: dict) -> list[QMDHit]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(self.base_url + "/query", data=data, headers={"Content-Type": "application/json"}, method="POST")
        with request.urlopen(req, timeout=self.timeout) as r:
            body = r.read().decode("utf-8")
        parsed = json.loads(body)
        return self._normalize(parsed)

    def _cli_query(self, payload: dict, cause: Exception) -> list[QMDHit]:
        # CLI fallback is primarily for development. A shared HTTP daemon is the production path.
        # Windows 注意：subprocess 不会解析 .cmd/.bat（CreateProcess 只认 .exe）→ 用 shutil.which 显式解析
        qmd_exe = shutil.which("qmd") or shutil.which("qmd.cmd")
        if qmd_exe is None:
            raise QMDClientError(
                f"QMD daemon unavailable ({cause}) and qmd CLI not found in PATH. Run scripts/qmd_bootstrap.sh first."
            )
        typed = "\n".join(f"{x['type']}: {x['query']}" for x in payload["searches"])
        all_hits: dict[str, QMDHit] = {}
        for collection in payload["collections"]:
            cmd = [qmd_exe, "query", "--json", "-n", str(payload["limit"]), "-C", str(payload["candidateLimit"]),
                   "--min-score", str(payload["minScore"]), "-c", collection]
            if not payload["rerank"]:
                cmd.append("--no-rerank")
            if payload.get("intent"):
                cmd += ["--intent", payload["intent"]]
            cmd.append(typed)
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=max(self.timeout, 300), check=True)
            except FileNotFoundError as e:
                raise QMDClientError(
                    f"QMD daemon unavailable ({cause}) and qmd CLI is not installed. Run scripts/qmd_bootstrap.sh first."
                ) from e
            except subprocess.CalledProcessError as e:
                raise QMDClientError(f"QMD CLI failed: {e.stderr.strip()}") from e
            for hit in self._normalize(json.loads(p.stdout)):
                prev = all_hits.get(hit.file)
                if prev is None or hit.score > prev.score:
                    all_hits[hit.file] = hit
        return sorted(all_hits.values(), key=lambda x: x.score, reverse=True)[: payload["limit"]]

    @staticmethod
    def _normalize(payload: Any) -> list[QMDHit]:
        if isinstance(payload, dict):
            items = payload.get("results") or payload.get("data") or payload.get("items") or []
            if isinstance(items, dict):
                items = items.get("results") or []
        elif isinstance(payload, list):
            items = payload
        else:
            items = []
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            file = str(item.get("file") or item.get("path") or item.get("uri") or "")
            score = item.get("score", item.get("finalScore", item.get("rerankScore", 0)))
            try:
                score = float(score)
            except Exception:
                score = 0.0
            collection = ""
            if file.startswith("qmd://"):
                rest = file[6:]
                collection = rest.split("/", 1)[0]
            out.append(QMDHit(
                file=file,
                title=str(item.get("title") or ""),
                score=score,
                snippet=str(item.get("snippet") or item.get("text") or item.get("content") or ""),
                docid=str(item.get("docid") or item.get("id") or ""),
                collection=collection,
                raw=item,
            ))
        return out
