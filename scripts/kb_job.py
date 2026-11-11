#!/usr/bin/env python3
"""Per-file knowledge-base ingestion status.

Runtime state lives in rag/jobs/<job_id>.json. Writes are locked and atomic;
status failures are always non-fatal for the upload/compile pipeline.

CLI examples:
  python scripts/kb_job.py create <raw-path> --uploader operator
  python scripts/kb_job.py stage <raw-path> wiki_written
  python scripts/kb_job.py stage <raw-path> extracting
  python scripts/kb_job.py active-stage projection
  python scripts/kb_job.py fail <raw-path> --error "..."
  python scripts/kb_job.py active-done
  python scripts/kb_job.py list --json
  python scripts/kb_job.py show <raw-path>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = ROOT / "rag" / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
JOBS_LOCK = JOBS_DIR / ".jobs.lock"
sys.path.insert(0, str(ROOT / ".scripts"))
from _locklib import file_lock  # noqa: E402

STAGES = [
    ("uploaded", "已收到附件"),
    ("queued", "已排队等待编译"),
    ("claimed", "已取得编译锁"),
    ("extracting", "正在提取文本 / OCR / Vision"),
    ("wiki_compiling", "正在写 Wiki 页面"),
    ("wiki_written", "Wiki 页面已完成"),
    ("hashes_updated", "raw hash 已更新"),
    ("projection", "正在重建 core / extracted / evidence"),
    ("qmd_update", "正在更新 QMD 文档"),
    ("qmd_embed", "正在生成 QMD 向量"),
    ("done", "全部完成，可正式检索"),
]
STAGE_INDEX = {name: i for i, (name, _) in enumerate(STAGES)}
STAGE_LABEL = dict(STAGES)
ACTIVE_STATUSES = {"running"}
TERMINAL_STATUSES = {"done", "failed", "stalled"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _raw_rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except Exception:
        return path.resolve().as_posix()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _job_id(raw_rel: str, sha: str) -> str:
    return hashlib.sha1(f"{raw_rel}\0{sha}".encode("utf-8")).hexdigest()[:16]


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _load_all() -> list[dict]:
    if not JOBS_DIR.exists():
        return []
    out = []
    for p in sorted(JOBS_DIR.glob("*.json")):
        if p.name.startswith("."):
            continue
        d = _read(p)
        if d and d.get("job_id"):
            out.append(d)
    return out


def _meta_uploader(raw_path: Path) -> str | None:
    meta_path = Path(str(raw_path) + ".meta.json")
    if not meta_path.exists():
        return None
    try:
        d = json.loads(meta_path.read_text(encoding="utf-8"))
        return str(d.get("uploaded_by") or "") or None
    except Exception:
        return None


def _new_job(raw_path: Path, uploader: str | None, stage: str = "queued", detail: dict | None = None) -> dict:
    raw_path = raw_path.resolve()
    sha = _sha256(raw_path)
    raw_rel = _raw_rel(raw_path)
    jid = _job_id(raw_rel, sha)
    ts = now_iso()
    return {
        "version": 1,
        "job_id": jid,
        "raw_path": raw_rel,
        "raw_sha256": sha,
        "filename": raw_path.name,
        "uploader": uploader or _meta_uploader(raw_path),
        "stage": stage,
        "stage_label": STAGE_LABEL.get(stage, stage),
        "stage_index": STAGE_INDEX.get(stage, 0),
        "total_stages": len(STAGES),
        "status": "running",
        "created_at": ts,
        "updated_at": ts,
        "started_at": ts,
        "finished_at": None,
        "attempt": 1,
        "detail": detail or {},
        "error": None,
        "history": [{"stage": stage, "at": ts}],
    }


def _advance(job: dict, stage: str, detail: dict | None = None, status: str | None = None) -> dict:
    old = job.get("stage")
    idx = STAGE_INDEX.get(stage, job.get("stage_index", 0))
    # Do not move a running job backwards; failed/stalled jobs may be retried.
    if status is None and job.get("status") not in {"failed", "stalled"} and idx < job.get("stage_index", 0):
        return job
    if status is None and job.get("status") in {"failed", "stalled"}:
        job["status"] = "running"
        job["error"] = None
        job["finished_at"] = None
        job["attempt"] = int(job.get("attempt", 1)) + 1
    job["stage"] = stage
    job["stage_label"] = STAGE_LABEL.get(stage, stage)
    job["stage_index"] = idx
    job["total_stages"] = len(STAGES)
    job["updated_at"] = now_iso()
    if status:
        job["status"] = status
    if detail:
        job.setdefault("detail", {}).update(detail)
    if stage != old:
        job.setdefault("history", []).append({"stage": stage, "at": job["updated_at"]})
    if stage == "done" or status == "done":
        # A failed run may already have a failure timestamp; completion must
        # overwrite it with the actual done time.
        job["finished_at"] = job["updated_at"]
    elif job.get("status") in TERMINAL_STATUSES:
        job["finished_at"] = job.get("finished_at") or job["updated_at"]
    return job


def create_job(raw_path: str | Path, uploader: str | None = None,
               stage: str = "queued", detail: dict | None = None) -> dict:
    path = Path(raw_path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(str(path))
    raw_rel = _raw_rel(path)
    sha = _sha256(path)
    jid = _job_id(raw_rel, sha)
    with file_lock(JOBS_LOCK):
        existing = _read(_job_path(jid))
        if existing:
            # Same path+content: reuse the job. A failed/stalled job can be
            # re-queued; a done job stays done unless its content changes.
            if existing.get("status") in {"failed", "stalled"}:
                existing["status"] = "running"
                existing["error"] = None
                existing["finished_at"] = None
                existing["attempt"] = int(existing.get("attempt", 1)) + 1
                existing = _advance(existing, "queued", detail=detail, status="running")
            else:
                existing = _advance(
                    existing,
                    stage if STAGE_INDEX.get(stage, 0) >= existing.get("stage_index", 0) else existing.get("stage", stage),
                    detail=detail,
                )
            _write_atomic(_job_path(jid), existing)
            return existing
        job = _new_job(path, uploader, stage=stage, detail=detail)
        _write_atomic(_job_path(jid), job)
        return job


def _find_latest(raw_rel: str) -> dict | None:
    candidates = [j for j in _load_all() if j.get("raw_path") == raw_rel]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x.get("updated_at", ""))


def update_by_raw(raw_path: str | Path, stage: str, detail: dict | None = None,
                  status: str | None = None) -> dict | None:
    path = Path(raw_path)
    if not path.exists():
        return None
    raw_rel = _raw_rel(path)
    with file_lock(JOBS_LOCK):
        job = _find_latest(raw_rel)
        if job is None:
            job = _new_job(path, None, stage="queued")
            _write_atomic(_job_path(job["job_id"]), job)
        # A raw edit produces a different sha; start a new job for the new version.
        try:
            current_sha = _sha256(path)
        except Exception:
            current_sha = job.get("raw_sha256")
        if current_sha != job.get("raw_sha256"):
            job = _new_job(path, job.get("uploader"), stage="queued")
            _write_atomic(_job_path(job["job_id"]), job)
        job = _advance(job, stage, detail=detail, status=status)
        _write_atomic(_job_path(job["job_id"]), job)
        return job


def fail_by_raw(raw_path: str | Path, error: str) -> dict | None:
    path = Path(raw_path)
    if not path.exists():
        return None
    raw_rel = _raw_rel(path)
    with file_lock(JOBS_LOCK):
        job = _find_latest(raw_rel)
        if job is None:
            return None
        job["status"] = "failed"
        job["error"] = str(error)[:2000]
        job["updated_at"] = now_iso()
        job["finished_at"] = job.get("finished_at") or job["updated_at"]
        job.setdefault("history", []).append({"stage": "failed", "at": job["updated_at"]})
        _write_atomic(_job_path(job["job_id"]), job)
        return job


def finish_by_raw(raw_path: str | Path, detail: dict | None = None) -> dict | None:
    """Mark a job done even after a failed refresh (operator recovery)."""
    path = Path(raw_path)
    if not path.exists():
        return None
    raw_rel = _raw_rel(path)
    with file_lock(JOBS_LOCK):
        job = _find_latest(raw_rel)
        if job is None:
            return None
        job = _advance(job, "done", detail=detail, status="done")
        _write_atomic(_job_path(job["job_id"]), job)
        return job


def update_active_jobs(stage: str, detail: dict | None = None, job_ids: set[str] | None = None) -> int:
    """Advance currently-running jobs; optionally restrict to a compile batch."""
    count = 0
    with file_lock(JOBS_LOCK):
        for job in _load_all():
            if job.get("status") == "running" and (job_ids is None or job.get("job_id") in job_ids):
                job = _advance(job, stage, detail=detail)
                _write_atomic(_job_path(job["job_id"]), job)
                count += 1
    return count


def finish_active_jobs(detail: dict | None = None, job_ids: set[str] | None = None) -> int:
    count = 0
    with file_lock(JOBS_LOCK):
        for job in _load_all():
            if job.get("status") == "running" and (job_ids is None or job.get("job_id") in job_ids):
                job = _advance(job, "done", detail=detail, status="done")
                _write_atomic(_job_path(job["job_id"]), job)
                count += 1
    return count


def fail_active_jobs(error: str, job_ids: set[str] | None = None) -> int:
    count = 0
    with file_lock(JOBS_LOCK):
        for job in _load_all():
            if job.get("status") == "running" and (job_ids is None or job.get("job_id") in job_ids):
                job = _advance(job, job.get("stage", "failed"), status="failed")
                job["error"] = str(error)[:2000]
                job["updated_at"] = now_iso()
                job["finished_at"] = job.get("finished_at") or job["updated_at"]
                job.setdefault("history", []).append({"stage": "failed", "at": job["updated_at"]})
                _write_atomic(_job_path(job["job_id"]), job)
                count += 1
    return count


def list_jobs() -> list[dict]:
    return sorted(_load_all(), key=lambda x: x.get("updated_at", ""), reverse=True)


def get_job(raw_path: str | Path | None = None, job_id: str | None = None) -> dict | None:
    if job_id:
        return _read(_job_path(job_id))
    if raw_path:
        return _find_latest(_raw_rel(Path(raw_path)))
    jobs = list_jobs()
    return jobs[0] if jobs else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create")
    p.add_argument("path")
    p.add_argument("--uploader")
    p.add_argument("--stage", default="queued")
    p.add_argument("--detail", default="{}")
    p = sub.add_parser("stage")
    p.add_argument("path")
    p.add_argument("stage", choices=[s for s, _ in STAGES if s not in {"done"}])
    p.add_argument("--detail", default="{}")
    p = sub.add_parser("active-stage")
    p.add_argument("stage", choices=[s for s, _ in STAGES if s not in {"done"}])
    p.add_argument("--detail", default="{}")
    p = sub.add_parser("fail")
    p.add_argument("path", nargs="?")
    p.add_argument("--error", required=True)
    p = sub.add_parser("done")
    p.add_argument("path")
    p.add_argument("--detail", default="{}")
    p = sub.add_parser("active-done")
    p.add_argument("--detail", default="{}")
    p = sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show")
    p.add_argument("path", nargs="?")
    p.add_argument("--job-id")
    p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        detail = json.loads(getattr(args, "detail", "{}") or "{}")
        if not isinstance(detail, dict):
            raise ValueError("detail must be a JSON object")
    except Exception as exc:
        print(f"invalid --detail: {exc}", file=sys.stderr)
        return 2

    if args.cmd == "create":
        job = create_job(args.path, args.uploader, args.stage, detail)
        print(json.dumps(job, ensure_ascii=False, indent=2))
    elif args.cmd == "stage":
        job = update_by_raw(args.path, args.stage, detail)
        print(json.dumps(job, ensure_ascii=False, indent=2) if job else "job not found")
    elif args.cmd == "active-stage":
        print(update_active_jobs(args.stage, detail))
    elif args.cmd == "fail":
        if args.path:
            job = fail_by_raw(args.path, args.error)
            if job:
                print(json.dumps(job, ensure_ascii=False, indent=2))
                return 0
        print(fail_active_jobs(args.error))
    elif args.cmd == "done":
        job = finish_by_raw(args.path, detail)
        print(json.dumps(job, ensure_ascii=False, indent=2) if job else "job not found")
    elif args.cmd == "active-done":
        print(finish_active_jobs(detail))
    elif args.cmd == "list":
        jobs = list_jobs()
        if args.json:
            print(json.dumps(jobs, ensure_ascii=False, indent=2))
        else:
            for j in jobs:
                print(f"{j.get('job_id')}  {j.get('stage', ''):16} {j.get('status', ''):8} {j.get('raw_path', '')}")
    elif args.cmd == "show":
        job = get_job(args.path, args.job_id)
        if not job:
            print("job not found")
            return 1
        print(json.dumps(job, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
