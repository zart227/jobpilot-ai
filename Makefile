.PHONY: verify test checklist-report checklist-gate install close-live audit-status rollout drill preflight fix-proposal-conflicts dependency-drill redis-drill cursor-bridge-drill qdrant-drill scoring-drill cursor-bridge-container verify-integration autonomous ops-snapshot ops-outbox-triage ops-outbox-triage-md ops-rollout-readiness ops-remediation-preflight secret-scan

PY ?= .venv/bin/python

secret-scan:
	$(PY) scripts/secret_scan_repo.py

install:
	sh scripts/verify_lockfile.sh

test:
	$(PY) -m pytest -q

checklist-report:
	$(PY) scripts/remediation_checklist_report.py

checklist-gate:
	$(PY) scripts/remediation_checklist_status.py

verify:
	./scripts/remediation_verify.sh

close-live:
	./scripts/close_live_kwork_remediation.sh

audit-status:
	$(PY) scripts/audit_remediation_status.py --write docs/audit-remediation-matrix.md

OPS_DB_URL = $(shell DATABASE_URL="$$DATABASE_URL" JOBPILOT_WORKING_DATABASE_URL="$$JOBPILOT_WORKING_DATABASE_URL" PYTHONPATH=. $(PY) scripts/ops_database_url.py 2>/dev/null)

ops-snapshot:
	@test -n "$(OPS_DB_URL)" || (echo "Set JOBPILOT_WORKING_DATABASE_URL or DATABASE_URL to jobpilot_ai" >&2; exit 2)
	DATABASE_URL="$(OPS_DB_URL)" PYTHONPATH=. $(PY) scripts/ops_remediation_snapshot.py

ops-outbox-triage:
	@test -n "$(OPS_DB_URL)" || (echo "Set JOBPILOT_WORKING_DATABASE_URL or DATABASE_URL to jobpilot_ai" >&2; exit 2)
	DATABASE_URL="$(OPS_DB_URL)" PYTHONPATH=. $(PY) scripts/ops_outbox_triage_rollup.py --json

ops-outbox-triage-md:
	@test -n "$(OPS_DB_URL)" || (echo "Set JOBPILOT_WORKING_DATABASE_URL or DATABASE_URL to jobpilot_ai" >&2; exit 2)
	DATABASE_URL="$(OPS_DB_URL)" PYTHONPATH=. $(PY) scripts/ops_outbox_triage_markdown_bundle.py

ops-rollout-readiness:
	@test -n "$(OPS_DB_URL)" || (echo "Set JOBPILOT_WORKING_DATABASE_URL or DATABASE_URL to jobpilot_ai" >&2; exit 2)
	@case "$(OPS_DB_URL)" in *\_test) echo "Refusing *_test for rollout readiness; point URL at jobpilot_ai (see .env.example)" >&2; exit 2;; esac
	DATABASE_URL="$(OPS_DB_URL)" JOBPILOT_WORKING_DATABASE_URL="$(OPS_DB_URL)" PYTHONPATH=. $(PY) scripts/ops_rollout_readiness.py

ops-remediation-preflight:
	@test -n "$(OPS_DB_URL)" || (echo "Set JOBPILOT_WORKING_DATABASE_URL or DATABASE_URL to jobpilot_ai" >&2; exit 2)
	@case "$(OPS_DB_URL)" in *\_test) echo "Refusing *_test for remediation preflight; point URL at jobpilot_ai" >&2; exit 2;; esac
	DATABASE_URL="$(OPS_DB_URL)" JOBPILOT_WORKING_DATABASE_URL="$(OPS_DB_URL)" PYTHONPATH=. $(PY) scripts/ops_working_db_preflight.py
	DATABASE_URL="$(OPS_DB_URL)" JOBPILOT_WORKING_DATABASE_URL="$(OPS_DB_URL)" PYTHONPATH=. $(PY) scripts/ops_rollout_readiness.py
	DATABASE_URL="$(OPS_DB_URL)" JOBPILOT_WORKING_DATABASE_URL="$(OPS_DB_URL)" PYTHONPATH=. $(PY) scripts/ops_outbox_triage_rollup.py --json

rollout:
	./scripts/production_rollout.sh

drill:
	./scripts/staging_drill_automated.sh

preflight:
	$(PY) scripts/migration_preflight.py

fix-proposal-conflicts:
	$(PY) scripts/resolve_active_proposal_conflicts.py

dependency-drill:
	./scripts/dependency_failure_drill.sh

redis-drill:
	./scripts/redis_failure_drill.sh

cursor-bridge-drill:
	./scripts/cursor_bridge_drill.sh

qdrant-drill:
	./scripts/qdrant_reindex_drill.sh

scoring-drill:
	./scripts/scoring_weights_drill.sh

cursor-bridge-container:
	./scripts/cursor_bridge_from_container.sh

verify-integration:
	./scripts/bootstrap_integration_db.sh
	unset DATABASE_URL REDIS_URL CELERY_BROKER_URL CELERY_RESULT_BACKEND; \
	set -a && . ./.jobpilot_integration_test.env && set +a && \
	JOBPILOT_INTEGRATION_TESTS=1 API_RATE_LIMIT_ENABLED=false $(PY) -m pytest -q

autonomous:
	./scripts/autonomous_remediation_run.sh

finish-owner:
	./scripts/finish_owner_checklist_automated.sh
