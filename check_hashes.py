#!/usr/bin/env python3
"""Hash drift check - compare .hashes.json with actual files on disk (跨平台版)

.hashes.json 的 key 支持：相对路径（推荐）、历史 Linux 绝对路径、Windows 绝对路径；
并对 #UXXXX 导出安全文件名做解码后与磁盘文件匹配（与 .scripts/check_pending.py 同规则）。
"""
import json
import hashlib
import os
import re

WIKI_ROOT = os.path.dirname(os.path.abspath(__file__))

_U_ESCAPE = re.compile(r"#U([0-9A-Fa-f]{4})")


def _decode_u(s):
    return _U_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), s)


def _norm(key):
    """key → 相对项目根的 POSIX 路径（#U 已解码）"""
    k = str(key).replace("\\", "/")
    if k.startswith("/home/demo/company-wiki/"):
        k = k[len("/home/demo/company-wiki/"):]
    elif k.startswith("/") or (len(k) > 1 and k[1] == ":"):
        try:
            from pathlib import Path as _P
            k = _P(k).resolve().relative_to(_P(WIKI_ROOT).resolve()).as_posix()
        except Exception:
            pass
    return _decode_u(k.lstrip("./"))


def _disk_key(path):
    """磁盘路径 → 相对项目根的 POSIX 路径（#U 已解码）"""
    return _decode_u(os.path.relpath(path, WIKI_ROOT).replace("\\", "/"))


with open(os.path.join(WIKI_ROOT, ".hashes.json"), "r", encoding="utf-8") as f:
    stored = json.load(f)

stored_norm = {_norm(k): v for k, v in stored.items()}

# 磁盘实际文件表：解码归一化 key → 实际路径（磁盘文件名可能是 #UXXXX 编码形式）
disk_map = {}
_raw_root = os.path.join(WIKI_ROOT, "raw")
for _root, _dirs, _files in os.walk(_raw_root):
    for _fname in _files:
        _p = os.path.join(_root, _fname)
        disk_map[_disk_key(_p)] = _p

print("=== Hash Drift Check ===")
print(f"Entries in .hashes.json: {len(stored)}")

drift = []
missing_files = []
ok_count = 0

for rel_key, expected_hash in stored_norm.items():
    filepath = disk_map.get(rel_key)
    if filepath is None:
        missing_files.append(rel_key)
        continue

    with open(filepath, "rb") as f:
        actual_hash = hashlib.sha256(f.read()).hexdigest()

    if actual_hash != expected_hash:
        drift.append({
            "file": rel_key,
            "expected": expected_hash[:16] + "...",
            "actual": actual_hash[:16] + "...",
        })
    else:
        ok_count += 1

print(f"Hash matches OK: {ok_count}")
print(f"Hash DRIFT (file exists but hash changed): {len(drift)}")
for d in drift:
    print(f"  DRIFT: {d['file']}")
    print(f"    stored:  {d['expected']}")
    print(f"    current: {d['actual']}")

print(f"Stale entries (file no longer on disk): {len(missing_files)}")
for m in missing_files:
    print(f"  STALE: {m}")

# Also check: are there raw files NOT in .hashes.json?
print()
print("=== Untracked raw files ===")
raw_dir = os.path.join(WIKI_ROOT, "raw")
untracked = []
for root, dirs, files in os.walk(raw_dir):
    for fname in files:
        fpath = os.path.join(root, fname)
        if _disk_key(fpath) not in stored_norm:
            untracked.append(fpath)

if untracked:
    for u in untracked:
        print(f"  UNTRACKED: {u}")
else:
    print("  All raw files tracked in .hashes.json")
