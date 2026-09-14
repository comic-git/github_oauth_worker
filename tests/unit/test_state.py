"""Tests for signed OAuth state and correlation-cookie behavior."""

import pytest
from fastapi import Response

from github_oauth_worker.errors import WorkerError
from github_oauth_worker.state import InvalidOAuthStateError, OAuthFlow, OAuthStateManager


@pytest.fixture
def state_manager() -> OAuthStateManager:
    return OAuthStateManager(state_signing_secret="test-signing-secret", ttl_seconds=600)


@pytest.mark.parametrize("flow", OAuthFlow)
def test_issued_state_requires_the_matching_cookie_nonce(
    state_manager: OAuthStateManager,
    flow: OAuthFlow,
) -> None:
    issued_state = _issue_state(state_manager, flow)

    state = state_manager.consume(
        issued_state.token,
        issued_state.correlation_nonce,
        flow,
    )

    assert state.flow is flow
    assert state.nonce == issued_state.correlation_nonce


def test_state_cannot_be_reused_for_a_different_flow(state_manager: OAuthStateManager) -> None:
    issued_state = state_manager.issue_enrollment("https://cms.example.com")

    with pytest.raises(InvalidOAuthStateError):
        state_manager.consume(
            issued_state.token,
            issued_state.correlation_nonce,
            OAuthFlow.SETUP,
        )


def test_state_rejects_a_tampered_token_or_mismatched_cookie(
    state_manager: OAuthStateManager,
) -> None:
    issued_state = _issue_state(state_manager, OAuthFlow.DECAP)

    with pytest.raises(InvalidOAuthStateError):
        state_manager.consume(
            f"{issued_state.token}tampered",
            issued_state.correlation_nonce,
            OAuthFlow.DECAP,
        )
    with pytest.raises(InvalidOAuthStateError):
        state_manager.consume(issued_state.token, "wrong-cookie", OAuthFlow.DECAP)


def test_state_expires_after_its_configured_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_manager = OAuthStateManager(state_signing_secret="test-signing-secret", ttl_seconds=60)
    monkeypatch.setattr("itsdangerous.timed.time.time", lambda: 1_000)
    issued_state = _issue_state(state_manager, OAuthFlow.DECAP)
    monkeypatch.setattr("itsdangerous.timed.time.time", lambda: 1_061)

    with pytest.raises(InvalidOAuthStateError):
        state_manager.consume(
            issued_state.token,
            issued_state.correlation_nonce,
            OAuthFlow.DECAP,
        )


@pytest.mark.parametrize("flow", OAuthFlow)
def test_correlation_cookie_has_browser_safe_attributes(
    state_manager: OAuthStateManager,
    flow: OAuthFlow,
) -> None:
    response = Response()
    issued_state = _issue_state(state_manager, flow)

    state_manager.attach_correlation_cookie(response, issued_state)

    cookie_header = response.headers["set-cookie"]
    assert f"{issued_state.cookie_name}={issued_state.correlation_nonce}" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "Max-Age=600" in cookie_header
    assert "Path=/" in cookie_header
    assert "SameSite=lax" in cookie_header
    assert "Secure" in cookie_header


def test_invalid_state_error_is_a_generic_worker_error(state_manager: OAuthStateManager) -> None:
    with pytest.raises(InvalidOAuthStateError) as error:
        state_manager.consume(None, None, OAuthFlow.DECAP)

    assert isinstance(error.value, WorkerError)
    assert error.value.public_message == "The authorization request could not be verified."


def _issue_state(state_manager: OAuthStateManager, flow: OAuthFlow):
    if flow is OAuthFlow.DECAP:
        return state_manager.issue_decap("https://cms.example.com", 123, 456)
    if flow is OAuthFlow.ENROLLMENT:
        return state_manager.issue_enrollment("https://cms.example.com")
    if flow is OAuthFlow.SETUP:
        return state_manager.issue_setup("https://cms.example.com", 123, 456)
    return state_manager.issue(flow)
