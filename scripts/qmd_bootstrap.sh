#!/usr/bin/env bash
# Company Wiki QMD 引导（Windows/Linux 跨平台版）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 选一个可用的 Python（优先项目 venv：Windows 或 POSIX 布局）
if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
  PY="$ROOT/.venv/Scripts/python.exe"
elif [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
elif command -v python >/dev/null 2>&1; then
  PY="python"
else
  echo "Python 3 未找到（建议在项目内创建 .venv 并安装 requirements.txt）" >&2
  exit 2
fi

# qmd 可用性（自包含安装优先；否则要求 Node>=22 并全局安装）
if ! command -v qmd >/dev/null 2>&1; then
  if command -v node >/dev/null 2>&1; then
    NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
    if [ "$NODE_MAJOR" -lt 22 ]; then
      echo "Node.js >=22 is required; current: $(node --version)" >&2
      exit 2
    fi
    npm install -g @tobilu/qmd
  else
    echo "Node.js >=22 (or an existing qmd) is required by current QMD." >&2
    exit 2
  fi
fi

echo "[multimodal status]"
"$PY" scripts/rag_multimodal_status.py || true
"$PY" scripts/rag_build.py
# Project-local .qmd/index.yml is intentionally versioned with this project.
# First run may ask to trust local project config depending on QMD version.
qmd update
qmd embed
# 显式绑定 IPv4 回环：Windows 下 qmd 默认只绑 IPv6 [::1]，会让 127.0.0.1 客户端连不上
qmd mcp --http --host 127.0.0.1 --port 8181 --daemon
qmd status

echo "QMD ready at http://127.0.0.1:8181/mcp"
