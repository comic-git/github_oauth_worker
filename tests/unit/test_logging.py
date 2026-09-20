"""Tests for structured log redaction."""

import io
import json
import logging

import pytest
from pydantic import SecretStr

from github_oauth_worker.logging import configure_event_logger, log_event, sanitize_log_fields


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

    assert caplog.messages[-1] == "authorization_failed"
    assert caplog.records[-1].json_fields == {
        "access_token": "[REDACTED]",
        "event": "authorization_failed",
        "repository_id": 42,
    }


def test_log_event_accepts_an_explicit_error_severity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("github_oauth_worker.test")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        log_event(logger, "oauth_callback_unexpected_failure", severity=logging.ERROR)

    assert caplog.records[-1].levelno == logging.ERROR
    assert caplog.messages[-1] == "oauth_callback_unexpected_failure"


def test_event_logger_writes_raw_json_to_its_stream() -> None:
    stream = io.StringIO()
    logger = configure_event_logger(
        logger_name="github_oauth_worker.test_events",
        stream=stream,
        project_id="example-project",
        labels={"environment": "test"},
    )

    log_event(logger, "cms_setup_target_state_read", repository_id=42)

    assert logger.propagate is False
    payload = json.loads(stream.getvalue().splitlines()[-1])
    assert payload["message"] == "cms_setup_target_state_read"
    assert payload["event"] == "cms_setup_target_state_read"
    assert payload["repository_id"] == 42
    assert payload["logging.googleapis.com/labels"] == {
        "environment": "test",
        "python_logger": "github_oauth_worker.test_events",
    }
