#!/usr/bin/env bash
# Company Wiki QMD daemon 控制（Windows/Linux 跨平台版）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
HOST=127.0.0.1
PORT=8181

health() { curl -s --max-time 3 "http://$HOST:$PORT/health" >/dev/null 2>&1; }

case "${1:-start}" in
  start)
    qmd mcp --http --host "$HOST" --port "$PORT" --daemon
    ;;
  stop)
    qmd mcp stop || true
    sleep 1
    if health; then
      # qmd mcp stop 在 Windows 下不可靠：按端口定位 PID 后强杀
      PID=""
      if command -v netstat >/dev/null 2>&1; then
        PID="$(netstat -ano 2>/dev/null | grep ":$PORT " | grep -i listening | awk '{print $NF}' | head -1 || true)"
      fi
      if [ -n "$PID" ] && [ "$PID" != "0" ]; then
        if command -v taskkill >/dev/null 2>&1; then
          # Windows/MSYS：禁用参数路径转换（旧写法 //PID 在转换关闭的环境会被 taskkill 判为无效参数）
          MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' taskkill /PID "$PID" /F || true
        else
          kill "$PID" 2>/dev/null || true
        fi
      fi
    fi
    ;;
  status)
    if health; then
      echo "qmd daemon 运行中: http://$HOST:$PORT"
    else
      echo "qmd daemon 未运行"
    fi
    qmd status
    ;;
  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;
  *)
    echo "usage: $0 {start|stop|status|restart}"
    exit 2
    ;;
esac
