"""Structured logging helpers that redact OAuth-sensitive values by default."""

import logging
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

from google.cloud.logging_v2.handlers.structured_log import StructuredLogHandler
from pydantic import SecretStr

_SENSITIVE_FIELD_NAMES = frozenset(
    {
        "authorization",
        "authorization_code",
        "client_secret",
        "cookie",
        "password",
        "refresh_token",
        "state",
    }
)
_SENSITIVE_FIELD_SUFFIXES = ("_secret", "_token", "_cookie")
_REDACTED = "[REDACTED]"
_UNSUPPORTED = "[UNSUPPORTED]"
_EVENT_LOGGER_NAME = "github_oauth_worker.events"
_EVENT_HANDLER_NAME = "github_oauth_worker_structured_log"


def _is_sensitive_field_name(name: str) -> bool:
    normalized_name = name.casefold()
    return normalized_name in _SENSITIVE_FIELD_NAMES or normalized_name.endswith(
        _SENSITIVE_FIELD_SUFFIXES
    )


def _sanitize_value(value: object) -> Any:
    if isinstance(value, SecretStr):
        return _REDACTED
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return sanitize_log_fields(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_sanitize_value(item) for item in value]
    return _UNSUPPORTED


def sanitize_log_fields(fields: Mapping[str, object]) -> dict[str, Any]:
    """Return JSON-safe fields while redacting names and values that can hold credentials."""
    return {
        name: _REDACTED if _is_sensitive_field_name(name) else _sanitize_value(value)
        for name, value in fields.items()
    }


def configure_event_logger(
    *,
    logger_name: str = _EVENT_LOGGER_NAME,
    stream: TextIO | None = None,
    project_id: str | None = None,
    labels: Mapping[str, str] | None = None,
) -> logging.Logger:
    """Configure one official Cloud Logging handler for safe worker-owned events only."""
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = StructuredLogHandler(
        labels=dict(labels or {}),
        project_id=project_id,
        stream=stream,
    )
    handler.name = _EVENT_HANDLER_NAME
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger


def log_event(logger: logging.Logger, event: str, **fields: object) -> None:
    """Write a Cloud Logging JSON payload without allowing callers to serialize sensitive values."""
    logger.info(event, extra={"json_fields": {"event": event, **sanitize_log_fields(fields)}})
