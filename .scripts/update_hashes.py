#!/usr/bin/env python3
"""更新 .hashes.json：为重编译后的文件重算 sha256 并写入。

用法:
  python update_hashes.py <path> [<path> ...]

规则:
  - 路径可为绝对或相对；相对路径按项目根解析。
  - 传入 raw/.../x.docx 时，若存在 x.docx.meta.json 会自动一并更新。
  - 所有已存在的 key 会顺便归一化为「相对项目根、#U 已解码」的标准格式
    （与 .scripts/check_pending.py / compile_state.py 同规则）。
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _locklib import file_lock  # noqa: E402
from check_pending import ROOT, HASHES_FILE, HASHES_LOCK, _norm_key  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    from kb_job import update_by_raw as update_job  # noqa: E402
except Exception:
    update_job = None


def _rel_disk(path):
    return _norm_key(os.path.relpath(os.path.abspath(path), ROOT).replace("\\", "/"))


def main(argv):
    if not argv:
        print("Usage: update_hashes.py <path> [<path> ...]", file=sys.stderr)
        return 1

    files = set()
    for a in argv:
        p = os.path.abspath(a)
        files.add(p)
        side = p + ".meta.json"
        if os.path.exists(side):
            files.add(side)

    updates = {}
    for p in sorted(files):
        if not os.path.exists(p):
            print(f"SKIP (not found): {p}", file=sys.stderr)
            continue
        with open(p, "rb") as f:
            updates[_rel_disk(p)] = hashlib.sha256(f.read()).hexdigest()

    with file_lock(HASHES_LOCK):
        data = {}
        if os.path.exists(HASHES_FILE):
            with open(HASHES_FILE, encoding="utf-8") as f:
                data = json.load(f)
        new_data = {}
        for k, v in data.items():
            new_data[_norm_key(k)] = v
        for k, v in updates.items():
            new_data[k] = v
        with open(HASHES_FILE, "w", encoding="utf-8", newline="\n") as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
            f.write("\n")

    if update_job is not None:
        for a in argv:
            p = os.path.abspath(a)
            if p.endswith(".meta.json"):
                continue
            try:
                update_job(p, "hashes_updated")
            except Exception:
                pass

    for k, v in sorted(updates.items()):
        print(f"UPDATED {k} {v[:16]}...")
    print(f"TOTAL {len(updates)} entries updated; .hashes.json entries: {len(new_data)}")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main(sys.argv[1:]))
