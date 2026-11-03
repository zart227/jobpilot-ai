"""Central logging configuration with secret scrubbing."""

from __future__ import annotations

import logging

import structlog

from app.utils.log_scrubber import scrub_sensitive_text

_CONFIGURED = False


class _SensitiveLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub_sensitive_text(record.msg)
        if record.args:
            record.args = tuple(
                scrub_sensitive_text(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True


def _scrub_structlog_event(
    _logger: logging.Logger,
    _method_name: str,
    event_dict: dict[str, object],
) -> dict[str, object]:
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            event_dict[key] = scrub_sensitive_text(value)
    return event_dict


def configure_application_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    filt = _SensitiveLogFilter()
    logging.getLogger().addFilter(filt)
    for name in (
        "sqlalchemy.engine",
        "uvicorn",
        "uvicorn.error",
        "uvicorn.access",
        "celery",
    ):
        logging.getLogger(name).addFilter(filt)

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            _scrub_structlog_event,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.NOTSET),
        cache_logger_on_first_use=True,
    )

    _CONFIGURED = True
