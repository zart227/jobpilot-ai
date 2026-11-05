"""Distinguish live confirmed sends from dry-run / simulation."""

from __future__ import annotations

from typing import Any

from app.db.models import SendAttempt
from app.db.send_attempt_states import ATTEMPT_CONFIRMED_SENT


def platform_response_is_simulation(response: dict[str, Any] | None) -> bool:
    if not response:
        return False
    if response.get("simulated") is True:
        return True
    return response.get("dry_run") is True


def is_live_confirmed_send_attempt(attempt: SendAttempt | None) -> bool:
    if attempt is None or attempt.status != ATTEMPT_CONFIRMED_SENT:
        return False
    return not platform_response_is_simulation(attempt.platform_response)


def is_simulated_confirmed_send_attempt(attempt: SendAttempt | None) -> bool:
    if attempt is None or attempt.status != ATTEMPT_CONFIRMED_SENT:
        return False
    return platform_response_is_simulation(attempt.platform_response)
