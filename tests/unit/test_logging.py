"""Tests for structured log redaction."""

import json
import logging

import pytest
from pydantic import SecretStr

from github_oauth_worker.logging import log_event, sanitize_log_fields


def test_sanitize_log_fields_redacts_sensitive_names_and_secret_values() -> None:
    fields = sanitize_log_fields(
        {
            "authorization_code": "code-value",
            "nested": {"refresh_token": "refresh-value"},
            "state": SecretStr("state-value"),
            "status_code": 503,
        }
    )

    assert fields == {
        "authorization_code": "[REDACTED]",
        "nested": {"refresh_token": "[REDACTED]"},
        "state": "[REDACTED]",
        "status_code": 503,
    }


def test_log_event_emits_json_without_sensitive_values(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("github_oauth_worker.test")

    with caplog.at_level(logging.INFO, logger=logger.name):
        log_event(logger, "authorization_failed", access_token="token-value", repository_id=42)

    payload = json.loads(caplog.messages[-1])
    assert payload == {
        "access_token": "[REDACTED]",
        "event": "authorization_failed",
        "repository_id": 42,
    }
