#!/usr/bin/env python3
"""安全追加路径到 .pending（带文件锁，跨平台）

用法:
  python append_pending.py "<绝对路径>"
  echo "<路径>" | python append_pending.py

由员工 profile 在收到文件后调用，替代裸 echo >> .pending。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _locklib import file_lock  # noqa: E402

# 项目根 = 本脚本所在 .scripts/ 的上一级（跨平台，不依赖部署位置）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PENDING_FILE = os.path.join(ROOT, ".pending")
PENDING_LOCK = os.path.join(ROOT, ".pending.lock")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    from kb_job import create_job  # noqa: E402
except Exception:
    create_job = None

path = None
if len(sys.argv) > 1:
    path = sys.argv[1].strip()
else:
    path = sys.stdin.read().strip()

if not path:
    print("Usage: append_pending.py <absolute_path>", file=sys.stderr)
    sys.exit(1)

with file_lock(PENDING_LOCK):
    with open(PENDING_FILE, "a", encoding="utf-8") as f:
        f.write(path + "\n")
        f.flush()
        os.fsync(f.fileno())

# Optional progress record; status tracking must never block ingestion.
if create_job is not None:
    try:
        create_job(path, stage="queued")
    except Exception as exc:
        print(f"WARN kb_job: {exc}", file=sys.stderr)

print(f"OK {path}")
