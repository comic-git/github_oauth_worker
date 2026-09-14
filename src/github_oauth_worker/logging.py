"""Structured logging helpers that redact OAuth-sensitive values by default."""

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

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


def log_event(logger: logging.Logger, event: str, **fields: object) -> None:
    """Write a structured event without allowing callers to serialize raw sensitive values."""
    logger.info(json.dumps({"event": event, **sanitize_log_fields(fields)}, sort_keys=True))
