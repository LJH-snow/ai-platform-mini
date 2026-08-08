#!/usr/bin/env bash
# One-command demo: build and start the full stack, wait for readiness,
# and print the entry points (frontend / API / Jaeger / Ollama).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -z "${INITIAL_API_KEY:-}" ] || [ -z "${ADMIN_API_KEYS:-}" ]; then
    echo "Set INITIAL_API_KEY and ADMIN_API_KEYS first:" >&2
    echo "  export INITIAL_API_KEY=sk-your-initial-key" >&2
    echo "  export ADMIN_API_KEYS=sk-your-admin-key" >&2
    exit 1
fi

docker compose up -d --build

echo "Waiting for the backend to become healthy..."
until curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1; do
    sleep 2
done

echo
echo "Demo ready:"
echo "  Frontend:  http://localhost:5173"
echo "  API:       http://localhost:8000"
echo "  Jaeger UI: http://localhost:16686"
echo "  Ollama:    http://localhost:11434"
echo
echo "Suggested path: 注册/登录 → Agent 工作流 → 知识库问答 → Trace 回放 → 用量仪表盘"
