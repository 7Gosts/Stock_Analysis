#!/usr/bin/env bash
# 重启本仓库的 Agent API（uvicorn）并启动飞书机器人（前台）。
# 用法（在任意目录）:
# bash scripts/feishu_dev.sh
# 或先 chmod +x 后:
#   ./scripts/feishu_dev.sh
#
# 环境变量:
#   FEISHU_API_HOST  默认 127.0.0.1
#   FEISHU_API_PORT  默认 8000
#   UVICORN_EXTRA    传给 uvicorn 的额外参数，如 "--log-level debug"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

HOST="${FEISHU_API_HOST:-127.0.0.1}"
PORT="${FEISHU_API_PORT:-8000}"
BASE_URL="http://${HOST}:${PORT}"

cleanup() {
  if [[ -n "${UVICORN_PID:-}" ]] && kill -0 "$UVICORN_PID" 2>/dev/null; then
    kill "$UVICORN_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "[feishu_dev] 停止旧进程（uvicorn / feishu_bot）…"
pkill -f '[u]vicorn app.api_server:app' 2>/dev/null || true
pkill -f '[p]ython3 cli/feishu_bot.py' 2>/dev/null || true
pkill -f '[p]ython cli/feishu_bot.py' 2>/dev/null || true
sleep 1

echo "[feishu_dev] 启动 uvicorn → ${BASE_URL}"
# shellcheck disable=SC2086
uvicorn app.api_server:app --host "$HOST" --port "$PORT" ${UVICORN_EXTRA:-} &
UVICORN_PID=$!

echo "[feishu_dev] 等待 /health …"
ok=0
for _ in $(seq 1 40); do
  if python3 - "$BASE_URL" <<'PY'
import sys, urllib.request
u = sys.argv[1] + "/health"
try:
    urllib.request.urlopen(u, timeout=1).read()
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
  then
    ok=1
    break
  fi
  sleep 0.25
done
if [[ "$ok" != 1 ]]; then
  echo "[feishu_dev] 错误: ${BASE_URL}/health 在约 10s 内未就绪，请查看 uvicorn 日志。" >&2
  exit 1
fi

echo "[feishu_dev] 启动飞书机器人（Ctrl+C 结束机器人并停止 uvicorn）…"
python3 cli/feishu_bot.py --api-base-url "$BASE_URL"
