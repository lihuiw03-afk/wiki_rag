#!/usr/bin/env python3
"""Read-only knowledge-base ingestion progress for agents.

Usage:
  python scripts/kb_progress.py --json
  python scripts/kb_progress.py --json --all
  python scripts/kb_progress.py --json --job-id <id>
  python scripts/kb_progress.py --json --raw <raw-path>
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from kb_job import list_jobs, _raw_rel  # noqa: E402


def _elapsed(job: dict) -> int:
    start = job.get("started_at") or job.get("created_at")
    end = job.get("finished_at") or datetime.now().astimezone().isoformat(timespec="seconds")
    if not start:
        return 0
    try:
        return max(0, int((datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))).total_seconds()))
    except Exception:
        return 0


def _view(job: dict, active_ids: set[str]) -> dict:
    updated = job.get("updated_at") or job.get("created_at")
    age = 0
    if updated:
        try:
            age = max(0, int((datetime.now().astimezone() - datetime.fromisoformat(str(updated))).total_seconds()))
        except Exception:
            age = 0
    return {
        "job_id": job.get("job_id"),
        "raw_path": job.get("raw_path"),
        "filename": job.get("filename"),
        "uploader": job.get("uploader"),
        "stage": job.get("stage"),
        "stage_label": job.get("stage_label"),
        "stage_index": job.get("stage_index"),
        "total_stages": job.get("total_stages"),
        "status": job.get("status"),
        "active": job.get("job_id") in active_ids,
        "stalled": job.get("status") == "running" and age > 1800,
        "stalled_seconds": age if job.get("status") == "running" and age > 1800 else 0,
        "elapsed_seconds": _elapsed(job),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "finished_at": job.get("finished_at"),
        "detail": job.get("detail") or {},
        "error": job.get("error"),
        "history": job.get("history") or [],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--all", action="store_true")
    p.add_argument("--job-id")
    p.add_argument("--raw")
    args = p.parse_args(argv)

    jobs = list_jobs()
    active = [j for j in jobs if j.get("status") == "running"]
    active_ids = {j.get("job_id") for j in active if j.get("job_id")}

    if args.job_id:
        jobs = [j for j in jobs if j.get("job_id") == args.job_id]
    elif args.raw:
        raw = _raw_rel(Path(args.raw)) if Path(args.raw).exists() else args.raw.replace("\\", "/").lstrip("./")
        jobs = [j for j in jobs if j.get("raw_path") == raw]
    elif not args.all:
        jobs = active[:1] or jobs[:1]

    views = [_view(j, active_ids) for j in jobs]
    payload = {
        "active": bool(active),
        "active_count": len(active),
        "jobs": views,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not views:
        print("没有找到入库任务")
        return 0
    for v in views:
        print(f"文件: {v['filename']} ({v['raw_path']})")
        print(f"阶段: {v['stage_label']} ({v['stage_index'] + 1}/{v['total_stages']})")
        print(f"状态: {v['status']}  已用: {v['elapsed_seconds']}s")
        if v.get("stalled"):
            print(f"警告: 已 {v['stalled_seconds']}s 未更新，可能已卡住")
        if v.get("detail"):
            print("详情: " + json.dumps(v["detail"], ensure_ascii=False))
        if v.get("error"):
            print(f"错误: {v['error']}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
