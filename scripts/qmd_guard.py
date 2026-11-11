# -*- coding: utf-8 -*-
"""qmd daemon 守卫（Windows 运维工具；由 Hermes cron 看门狗 qmd_watchdog.sh 调用）。

背景（2026-10-09 事故复盘）：
- 机器重启后 daemon 被从错误目录拉起（System32），指向 ~/.cache/qmd 空索引，全部查询静默返回空；
- 旧看门狗只查 /health（空索引 daemon 也返回 ok），无法发现；
- git-bash 下 curl 传中文参数会被编码损坏（ASCII 正常、中文恒空），导致探针恒失败、误判重启。
  本守卫因此改用 Python urllib 探针 + 绝对路径工具 + 轮询自愈，消除 PATH/编码两类环境依赖。

用法：
  python qmd_guard.py check        # 0=健康（health+探针） 1=异常
  python qmd_guard.py wait 90      # 每 3s 重试直到健康或超过预算秒数（容忍冷模型加载/忙态）
  python qmd_guard.py heal         # 杀现有实例 → 按正确目录重启 → 最长 120s 轮询；0=已恢复
"""
from __future__ import annotations
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = r'C:/Users/demo/Projects/qmdapp/node/node.exe'
QMD_JS = r'C:/Users/demo/Projects/qmdapp/node/node_modules/@tobilu/qmd/dist/cli/qmd.js'
BASE = 'http://127.0.0.1:8181'
PORT = 8181
PROBE_BODY = {
    'searches': [{'type': 'lex', 'query': '企业'}],
    'collections': ['company-core'], 'limit': 1, 'candidateLimit': 5,
    'minScore': 0.0, 'rerank': False, 'intent': '',
}


def _http(url: str, data: dict | None = None, timeout: int = 15) -> bytes | None:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8') if data is not None else None,
            headers={'Content-Type': 'application/json'},
            method='POST' if data is not None else 'GET')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def healthy(verbose: bool = False) -> bool:
    h = _http(BASE + '/health')
    if h is None:
        if verbose:
            print('health: FAIL')
        return False
    b = _http(BASE + '/query', PROBE_BODY)
    ok = b is not None and b'"file"' in b
    if verbose:
        print(f"health: OK | probe: {'OK' if ok else 'FAIL'}")
    return ok


def _port_busy() -> bool:
    try:
        with socket.create_connection(('127.0.0.1', PORT), timeout=1):
            return True
    except Exception:
        return False


def kill_listener() -> list[int]:
    killed: list[int] = []
    try:
        import psutil
        out = subprocess.run(['netstat', '-ano'], capture_output=True, text=True,
                             errors='replace').stdout or ''
        for line in out.splitlines():
            if f':{PORT}' in line and 'LISTENING' in line.upper():
                try:
                    pid = int(line.split()[-1])
                    psutil.Process(pid).kill()
                    killed.append(pid)
                except Exception:
                    pass
    except Exception as e:
        print('kill_listener error:', e, flush=True)
    return killed


def start_daemon() -> None:
    env = dict(os.environ)
    env['INDEX_PATH'] = str(ROOT / '.qmd' / 'index.sqlite')
    kw = {}
    if os.name == 'nt':
        try:
            kw['creationflags'] = subprocess.CREATE_NO_WINDOW
        except Exception:
            pass
    subprocess.Popen(
        [NODE, QMD_JS, 'mcp', '--http', '--host', '127.0.0.1', '--port', str(PORT), '--daemon'],
        cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if mode == 'check':
        sys.exit(0 if healthy(verbose=True) else 1)
    if mode == 'wait':
        budget = int(sys.argv[2]) if len(sys.argv) > 2 else 90
        deadline = time.time() + budget
        while True:
            if healthy():
                sys.exit(0)
            if time.time() >= deadline:
                sys.exit(1)
            time.sleep(3)
    if mode == 'heal':
        print('heal: killing current listener (if any)...', flush=True)
        for pid in kill_listener():
            print('  killed', pid, flush=True)
        for _ in range(15):
            if not _port_busy():
                break
            time.sleep(1)
        print('heal: starting daemon from', str(ROOT), flush=True)
        start_daemon()
        deadline = time.time() + 120
        while time.time() < deadline:
            if healthy():
                print('heal: OK — daemon healthy (probe passed)', flush=True)
                sys.exit(0)
            time.sleep(3)
        print('heal: FAILED — daemon not healthy after 120s', flush=True)
        sys.exit(1)
    print('usage: qmd_guard.py check|wait <sec>|heal')
    sys.exit(2)


if __name__ == '__main__':
    main()
