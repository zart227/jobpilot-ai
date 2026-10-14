"""External delivery attempt statuses."""

from __future__ import annotations

ATTEMPT_PENDING = "pending"
ATTEMPT_SENDING = "sending"
ATTEMPT_CONFIRMED_SENT = "confirmed_sent"
ATTEMPT_KNOWN_FAILED = "known_failed"
ATTEMPT_UNKNOWN_RESULT = "unknown_result"
ATTEMPT_NEEDS_ATTENTION = "needs_attention"
ATTEMPT_CANCELLED = "cancelled"

ATTEMPT_KIND_PROPOSAL = "proposal"
ATTEMPT_KIND_INBOX_REPLY = "inbox_reply"
ATTEMPT_KIND_PROPOSAL_UPDATE = "proposal_update"

TERMINAL_ATTEMPT_STATUSES: frozenset[str] = frozenset(
    {
        ATTEMPT_CONFIRMED_SENT,
        ATTEMPT_UNKNOWN_RESULT,
        ATTEMPT_NEEDS_ATTENTION,
        ATTEMPT_CANCELLED,
    }
)

BLOCKING_ATTEMPT_STATUSES: frozenset[str] = frozenset(
    {
        ATTEMPT_SENDING,
        ATTEMPT_UNKNOWN_RESULT,
        ATTEMPT_NEEDS_ATTENTION,
    }
)
