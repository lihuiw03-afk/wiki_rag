#!/usr/bin/env python3
"""编译状态指纹（供 Hermes cron --monitor-script 使用；只读、输出确定性）

输出（无时间戳，保证相同状态出相同字节）：
  CLEAN                                        — 无待编译、无哈希漂移
  WORK <sha16> pending=N processing=N drift=M  — 有工作；指纹变化时唤醒 Agent

注意：本脚本只读，不取编译锁、不改任何文件。归一化规则与 check_pending.py 完全一致。
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check_pending import (  # noqa: E402
    RAW_DIR,
    HASHES_FILE,
    HASHES_LOCK,
    PENDING_FILE,
    PENDING_PROCESSING,
    _rel,
    _locked_load_hashes,
)


def _read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return sorted({ln.strip() for ln in f if ln.strip()})


def main():
    pending = _read_lines(PENDING_FILE)
    processing = _read_lines(PENDING_PROCESSING)
    hashes = _locked_load_hashes(HASHES_FILE, HASHES_LOCK)

    drift = []
    for root, dirs, files in os.walk(RAW_DIR):
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "rb") as f:
                    sha = hashlib.sha256(f.read()).hexdigest()
            except (IOError, OSError):
                continue
            if hashes.get(_rel(fpath)) != sha:
                drift.append(_rel(fpath))
    drift.sort()

    if not pending and not processing and not drift:
        print("CLEAN")
        return

    payload = json.dumps(
        {"p": pending, "c": processing, "d": drift},
        ensure_ascii=False,
        sort_keys=True,
    )
    fp = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    print(f"WORK {fp} pending={len(pending)} processing={len(processing)} drift={len(drift)}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
