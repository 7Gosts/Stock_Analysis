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
#   FEISHU_AUTO_START_POSTGRES 默认 1；当 PostgreSQL 未启动时尝试通过 sudo service postgresql start 启动

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

HOST="${FEISHU_API_HOST:-127.0.0.1}"
PORT="${FEISHU_API_PORT:-8000}"
BASE_URL="http://${HOST}:${PORT}"
AUTO_START_POSTGRES="${FEISHU_AUTO_START_POSTGRES:-1}"

resolve_bin() {
  local venv_bin="$1"
  local fallback_bin="$2"
  if [[ -x "$REPO_ROOT/.venv/bin/$venv_bin" ]]; then
    printf '%s\n' "$REPO_ROOT/.venv/bin/$venv_bin"
    return 0
  fi
  command -v "$fallback_bin" 2>/dev/null || true
}

PYTHON_BIN="$(resolve_bin python python3)"
UVICORN_BIN="$(resolve_bin uvicorn uvicorn)"

require_bin() {
  local value="$1"
  local label="$2"
  if [[ -z "$value" ]]; then
    echo "[feishu_dev] 错误: 未找到 ${label}。请先安装依赖或激活 .venv。" >&2
    exit 1
  fi
}

require_bin "$PYTHON_BIN" "python"
require_bin "$UVICORN_BIN" "uvicorn"

ensure_postgres_ready() {
  if ! command -v pg_isready >/dev/null 2>&1; then
    echo "[feishu_dev] 未找到 pg_isready，跳过 PostgreSQL 就绪检查。"
    return 0
  fi

  if pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    return 0
  fi

  if [[ "$AUTO_START_POSTGRES" != "1" ]]; then
    echo "[feishu_dev] PostgreSQL 未就绪，且 FEISHU_AUTO_START_POSTGRES=$AUTO_START_POSTGRES，不自动启动。" >&2
    return 1
  fi

  echo "[feishu_dev] PostgreSQL 未启动，尝试执行 sudo service postgresql start ..."
  if ! sudo service postgresql start; then
    echo "[feishu_dev] 错误: PostgreSQL 启动失败。你可以手动执行 sudo service postgresql start。" >&2
    return 1
  fi

  if ! pg_isready -h 127.0.0.1 -p 5432 >/dev/null 2>&1; then
    echo "[feishu_dev] 错误: PostgreSQL 启动后仍未就绪。" >&2
    return 1
  fi
}

wait_for_health() {
  local base_url="$1"
  local ok=0
  for _ in $(seq 1 40); do
    if "$PYTHON_BIN" - "$base_url" <<'PY'
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
  [[ "$ok" == 1 ]]
}

cleanup() {
  if [[ -n "${UVICORN_PID:-}" ]] && kill -0 "$UVICORN_PID" 2>/dev/null; then
    kill "$UVICORN_PID" 2>/dev/null || true
    wait "$UVICORN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

ensure_postgres_ready

echo "[feishu_dev] 停止旧进程（uvicorn / feishu_bot）…"
pkill -f '[u]vicorn app.api_server:app' 2>/dev/null || true
pkill -f '[p]ython3 cli/feishu_bot.py' 2>/dev/null || true
pkill -f '[p]ython cli/feishu_bot.py' 2>/dev/null || true
sleep 1

echo "[feishu_dev] 启动 uvicorn → ${BASE_URL}"
# shellcheck disable=SC2086
"$UVICORN_BIN" app.api_server:app --host "$HOST" --port "$PORT" ${UVICORN_EXTRA:-} &
UVICORN_PID=$!

echo "[feishu_dev] 等待 /health …"
if ! wait_for_health "$BASE_URL"; then
  echo "[feishu_dev] 错误: ${BASE_URL}/health 在约 10s 内未就绪，请查看 uvicorn 日志。" >&2
  exit 1
fi

echo "[feishu_dev] 启动飞书机器人（Ctrl+C 结束机器人并停止 uvicorn）…"
"$PYTHON_BIN" cli/feishu_bot.py
