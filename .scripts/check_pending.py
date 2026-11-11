#!/usr/bin/env python3
"""Company Wiki 编译检测脚本（Windows/Linux 跨平台版）

由 compile cron 每分钟调用一次。
使用文件锁 + .pending.processing 防止并发丢失任务。
使用 .compile.lock 防止多个 Agent 同时编译。

流程：
  1. 尝试获取编译锁（O_EXCL 原子创建，防并发）
  2. 加锁消费 .pending → 移至 .pending.processing
  3. 扫描 raw/ 检测 sha256 变化
  4. 输出 JSON（wakeAgent + context）

输出格式：
  编译进行中: {"wakeAgent": false, "reason": "compile_in_progress"}
  无变化:     {"wakeAgent": false}
  有变化:     {"wakeAgent": true, "context": {"pending_files": [...], "compile_lock": "...", ...}}
"""

import json
import hashlib
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _locklib import file_lock  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
try:
    from kb_job import update_by_raw as update_job  # noqa: E402
except Exception:
    update_job = None

# 项目根 = 本脚本所在 .scripts/ 的上一级（跨平台，不依赖部署位置）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI = ROOT
HASHES_FILE = os.path.join(WIKI, ".hashes.json")
HASHES_LOCK = os.path.join(WIKI, ".hashes.lock")
PENDING_FILE = os.path.join(WIKI, ".pending")
PENDING_LOCK = os.path.join(WIKI, ".pending.lock")
PENDING_PROCESSING = os.path.join(WIKI, ".pending.processing")
COMPILE_LOCK = os.path.join(WIKI, ".compile.lock")
RAW_DIR = os.path.join(WIKI, "raw")

# 编译锁超时：如果锁文件存在超过此时间（秒），视为过期
COMPILE_LOCK_TIMEOUT = 600  # 10 分钟

# 历史部署路径前缀（把旧 Linux 绝对路径 key 归一化为相对路径）
_LEGACY_PREFIXES = ("/home/demo/company-wiki/",)

# 导出安全文件名：#UXXXX 序列（与 rag_gateway.projection.decode_u_escapes 同规则）
_U_ESCAPE = re.compile(r"#U([0-9A-Fa-f]{4})")


def _decode_u(value):
    """解码 #UXXXX 序列，使 key 与磁盘上的编码文件名、或解码名可比较。"""
    return _U_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), value)


def _norm_key(key):
    """把 .hashes.json 的 key 归一化为「相对项目根」的 POSIX 路径（且解码 #U 序列）。

    兼容三类写法：相对路径（推荐）、历史 Linux 绝对路径、Windows 绝对路径。
    """
    k = str(key).replace("\\", "/")
    for prefix in _LEGACY_PREFIXES:
        if k.startswith(prefix):
            k = k[len(prefix):]
            break
    if k.startswith("/") or (len(k) > 1 and k[1] == ":"):
        try:
            from pathlib import Path
            k = Path(k).resolve().relative_to(Path(ROOT).resolve()).as_posix()
        except Exception:
            pass
    return _decode_u(k.lstrip("./"))


def _rel(path):
    """本地路径 → 相对项目根的 POSIX 路径（.hashes.json 的 key 格式，#U 序列已解码）"""
    return _decode_u(os.path.relpath(path, WIKI).replace("\\", "/"))


def _locked_atomic_consume(filepath, lockpath, processing_path):
    """加锁 → 读取全部行 → mv 到 processing → 解锁"""
    paths = set()
    with file_lock(lockpath):
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8") as f:
                for line in f:
                    p = line.strip()
                    if p:
                        paths.add(p)
            os.replace(filepath, processing_path)
    return paths


def _try_acquire_compile_lock():
    """尝试获取编译锁。返回 True 表示获取成功，可以继续编译。

    使用 O_CREAT | O_EXCL 原子创建锁文件：
    - 文件不存在 → 创建成功，获取锁
    - 文件已存在 → 检查是否过期（>10min），过期则删除后重试
    """
    try:
        fd = os.open(COMPILE_LOCK, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
        os.write(fd, json.dumps({
            "acquired_at": time.time(),
            "pid": os.getpid()
        }).encode())
        os.close(fd)
        return True
    except FileExistsError:
        # 锁文件已存在，检查是否过期
        try:
            mtime = os.path.getmtime(COMPILE_LOCK)
            age = time.time() - mtime
            if age > COMPILE_LOCK_TIMEOUT:
                # 过期锁，删除后重试
                os.remove(COMPILE_LOCK)
                fd = os.open(COMPILE_LOCK, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o644)
                os.write(fd, json.dumps({
                    "acquired_at": time.time(),
                    "pid": os.getpid(),
                    "recovered_from_stale": True,
                    "stale_age_seconds": age
                }).encode())
                os.close(fd)
                return True
        except OSError:
            pass
        return False


def _release_compile_lock():
    """释放编译锁"""
    try:
        os.remove(COMPILE_LOCK)
    except FileNotFoundError:
        pass


def _locked_load_hashes(filepath, lockpath):
    """加锁读取 .hashes.json，归一化所有 key 为相对项目根路径"""
    with file_lock(lockpath):
        hashes = {}
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8") as f:
                raw = json.load(f)
            for k, v in raw.items():
                hashes[_norm_key(k)] = v
        return hashes


def main():
    # 0. 尝试获取编译锁（防并发编译）
    if not _try_acquire_compile_lock():
        print(json.dumps({
            "wakeAgent": False,
            "reason": "compile_in_progress"
        }, ensure_ascii=False))
        return

    # 1. 原子消费 .pending → .pending.processing
    pending_paths = _locked_atomic_consume(PENDING_FILE, PENDING_LOCK, PENDING_PROCESSING)

    # 2. 加载哈希
    hashes = _locked_load_hashes(HASHES_FILE, HASHES_LOCK)

    # 3. 合并 .pending 中的有效路径
    to_compile = []
    checked_paths = set()

    for p in sorted(pending_paths):
        p = os.path.normpath(p)
        if os.path.exists(p) and p.startswith(RAW_DIR):
            to_compile.append(p)
            checked_paths.add(p)

    # 4. 扫描 raw/，检测 sha256 变化（用户直接编辑的文件）
    for root, dirs, files in os.walk(RAW_DIR):
        for fname in sorted(files):
            if fname.startswith("."):
                continue
            fpath = os.path.join(root, fname)
            if fpath in checked_paths:
                continue
            try:
                with open(fpath, "rb") as f:
                    content = f.read()
                sha = hashlib.sha256(content).hexdigest()
                old_sha = hashes.get(_rel(fpath))
                if old_sha != sha:
                    to_compile.append(fpath)
            except (IOError, OSError):
                pass

    # 5. 无变化 → 释放编译锁，不唤醒模型，清理 processing 文件
    if not to_compile:
        _release_compile_lock()
        if os.path.exists(PENDING_PROCESSING):
            os.remove(PENDING_PROCESSING)
        print(json.dumps({"wakeAgent": False}, ensure_ascii=False))
        return

    # 6. 有变化 → 保持编译锁，输出上下文（Agent 编译完成后必须 rm .compile.lock）
    if update_job is not None:
        for p in to_compile:
            try:
                update_job(p, "claimed")
            except Exception:
                pass
    output = {
        "wakeAgent": True,
        "context": {
            "pending_files": to_compile,
            "wiki_root": WIKI,
            "raw_dir": RAW_DIR,
            "processing_file": PENDING_PROCESSING,
            "hashes_file": HASHES_FILE,
            "hashes_lock": HASHES_LOCK,
            "pending_file": PENDING_FILE,
            "pending_lock": PENDING_LOCK,
            "compile_lock": COMPILE_LOCK,
        }
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
