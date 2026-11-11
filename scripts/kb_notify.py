#!/usr/bin/env python3
"""Stage-change notifier for knowledge-base jobs (cron no_agent friendly).

Prints one line only when a job's stage/status changes; prints nothing otherwise,
so an empty stdout run stays silent. State is kept in rag/jobs/.notified.json.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / ".scripts"))
from kb_job import list_jobs, JOBS_DIR, JOBS_LOCK  # noqa: E402
from _locklib import file_lock  # noqa: E402

STATE = JOBS_DIR / ".notified.json"
IGNORE_STAGES = {"uploaded", "queued"}


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, STATE)


def main() -> int:
    changed = []
    with file_lock(JOBS_LOCK):
        state = load_state()
        for job in list_jobs():
            jid = job.get("job_id")
            if not jid:
                continue
            stage = job.get("stage")
            status = job.get("status")
            key = f"{stage}:{status}"
            prev = state.get(jid)
            if prev == key:
                continue
            state[jid] = key
            if stage in IGNORE_STAGES:
                continue
            changed.append((job, prev))
        save_state(state)

    for job, prev in sorted(changed, key=lambda x: x[0].get("updated_at", "")):
        status = job.get("status")
        line = (
            f"[KB] {job.get('filename')}: {job.get('stage_label')} "
            f"({job.get('stage_index', 0) + 1}/{job.get('total_stages', 0)}) "
            f"status={status}"
        )
        detail = job.get("detail") or {}
        if detail:
            line += f" detail={json.dumps(detail, ensure_ascii=False, separators=(',', ':'))}"
        if job.get("error"):
            line += f" error={job['error']}"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
