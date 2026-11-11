#!/usr/bin/env python3
"""跨平台文件锁：POSIX 用 fcntl.flock，Windows 用 msvcrt 字节区间锁。

用法:
    from _locklib import file_lock
    with file_lock(lock_path):
        ...临界区...

两种实现语义一致：进程退出时锁自动释放（不会遗留死锁）。
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

if os.name == "nt":
    import msvcrt

    def _acquire(fd: int) -> None:
        # msvcrt.locking(LK_LOCK) 内部重试约 10 秒后抛 OSError；
        # 外层再包一层重试，总等待上限约 60 秒。
        for _ in range(6):
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(1)
        raise TimeoutError("file_lock: 等待锁超时 (60s)")

    def _release(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _acquire(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _release(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def file_lock(path):
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
    try:
        _acquire(fd)
        try:
            yield
        finally:
            _release(fd)
    finally:
        os.close(fd)
