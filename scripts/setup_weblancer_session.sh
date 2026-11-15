#!/usr/bin/env bash
# Сессия Weblancer для скрапера: браузер (по умолчанию) или импорт из буфера.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "${ROOT}/.venv/bin/python" ]]; then
  PYTHON="${ROOT}/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

MODE="${1:---browser}"
if [[ "$MODE" == "--clipboard" || "$MODE" == "-c" ]]; then
  echo "=== Weblancer: импорт из буфера (Cookie-Editor → Copy) ==="
  read -r -p "Enter когда JSON скопирован… " _
  "$PYTHON" scripts/import_weblancer_cookies.py --auto
else
  echo "=== Weblancer: обычный Chrome (не Playwright) ==="
  echo "Без жёлтой плашки про автоматизацию — Cloudflare должен пустить."
  echo "Пройдите проверку, войдите, Enter в терминале."
  echo "Если снова зависло на CF: $0 --clipboard"
  echo ""
  if ! "$PYTHON" scripts/export_weblancer_session.py; then
    echo ""
    echo "Не вышло через браузер (часто из‑за Cloudflare / --no-sandbox)."
    echo "Повторите: $0 --clipboard"
    exit 1
  fi
fi

SESSION="${ROOT}/data/weblancer_session.json"
if [[ -f "$SESSION" ]]; then
  chmod 600 "$SESSION"
fi

if command -v docker >/dev/null 2>&1 && [[ -f "${ROOT}/docker-compose.yml" || -f "${ROOT}/compose.yml" ]]; then
  docker compose restart celery-worker
else
  echo "docker compose restart celery-worker  # если бот в Docker"
fi
