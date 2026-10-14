.PHONY: verify test checklist-report checklist-gate install close-live audit-status rollout drill preflight fix-proposal-conflicts dependency-drill redis-drill cursor-bridge-drill qdrant-drill scoring-drill cursor-bridge-container verify-integration autonomous

PY ?= .venv/bin/python

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
