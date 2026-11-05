#!/usr/bin/env sh
# Full automated remediation verification (does not perform live Kwork).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Stale shell exports override .env (compose Postgres is on host 5434).
case "${DATABASE_URL:-}" in
  *:5433/*) unset DATABASE_URL ;;
esac

# Broken shell export overrides .env and breaks Settings (dotenv-stripped JSON).
if [ -n "${GENERIC_SCRAPER_SELECTORS:-}" ]; then
  if ! "$ROOT/.venv/bin/python" -c "import json, os; json.loads(os.environ['GENERIC_SCRAPER_SELECTORS']); assert isinstance(json.loads(os.environ['GENERIC_SCRAPER_SELECTORS']), dict)" 2>/dev/null; then
    unset GENERIC_SCRAPER_SELECTORS
  fi
fi

PY="${ROOT}/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY=python3
fi

echo "== remediation_verify =="
"$PY" scripts/remediation_checklist_report.py
REMEDIATION_MAX_OPEN=115 "$PY" scripts/remediation_checklist_status.py
"$PY" scripts/audit_remediation_status.py
./scripts/live_acceptance_preflight.sh

_SAVED_INTEGRATION="${JOBPILOT_INTEGRATION_TESTS:-}"
unset JOBPILOT_INTEGRATION_TESTS
_GATE_TESTS="tests/test_remediation_checklist_gate.py tests/test_audit_remediation_status.py tests/test_settings_generic_selectors.py tests/test_proposal_conflict_resolution.py tests/test_qdrant_embedding_drill.py tests/test_ci_workflow_policy.py"
if [ -f "$ROOT/tests/test_c090_c093_production_repro_policy.py" ]; then
  _GATE_TESTS="$_GATE_TESTS tests/test_c090_c093_production_repro_policy.py"
fi
# shellcheck disable=SC2086
"$PY" -m pytest -q $_GATE_TESTS
JOBPILOT_INTEGRATION_TESTS="${_SAVED_INTEGRATION}"

if [ "${JOBPILOT_INTEGRATION_TESTS:-}" = "1" ]; then
  if ! "$PY" -c "import os; from sqlalchemy.engine import make_url; db=make_url(os.environ.get('DATABASE_URL','')).database or ''; raise SystemExit(0 if db.endswith('_test') else 1)" 2>/dev/null; then
    echo "WARN: JOBPILOT_INTEGRATION_TESTS=1 but DATABASE_URL is not a *_test database; skipping full pytest" >&2
    echo "Hint: ./scripts/bootstrap_integration_db.sh && set -a && . ./.jobpilot_integration_test.env && set +a" >&2
    JOBPILOT_INTEGRATION_TESTS=0
  fi
fi

if [ "${JOBPILOT_INTEGRATION_TESTS:-}" = "1" ]; then
  echo ""
  echo "== integration pytest (JOBPILOT_INTEGRATION_TESTS=1) =="
  "$PY" -m pytest -q
else
  echo ""
  echo "Optional (full suite + DB): JOBPILOT_INTEGRATION_TESTS=1 $PY -m pytest -q"
fi
echo "Live Kwork: copy data/live_kwork_signoff.json.example → data/live_kwork_signoff.json, then validate_live_signoff.py"
