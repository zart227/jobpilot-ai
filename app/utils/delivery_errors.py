"""Shared heuristics for ambiguous network/platform delivery failures."""


def is_ambiguous_send_error(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.lower()
    markers = (
        "timeout",
        "timed out",
        "неизвест",
        "unknown",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
    )
    return any(marker in lowered for marker in markers)
