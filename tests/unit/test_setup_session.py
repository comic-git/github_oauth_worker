"""Tests for confidential, bounded CMS setup browser sessions."""

import pytest
from fastapi import Response
from pydantic import SecretStr

from github_oauth_worker.setup_session import InvalidSetupSessionError, SetupSessionManager


def test_setup_session_is_encrypted_cookie_bound_to_its_form_nonce() -> None:
    manager = SetupSessionManager("secret-for-state-signatures", 600)
    response = Response()
    manager.attach(
        response,
        SecretStr("private-github-token"),
        "browser-nonce",
        installation_id=456,
        repository_id=123,
    )

    cookie = response.headers["set-cookie"]
    value = cookie.split("=", 1)[1].split(";", 1)[0]
    assert "private-github-token" not in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/setup" in cookie
    assert manager.read(
        value, "browser-nonce", expected_installation_id=456, expected_repository_id=123
    ).access_token.get_secret_value() == ("private-github-token")
    with pytest.raises(InvalidSetupSessionError):
        manager.read(
            value, "different-nonce", expected_installation_id=456, expected_repository_id=123
        )
    with pytest.raises(InvalidSetupSessionError):
        manager.read(
            value + "tampered",
            "browser-nonce",
            expected_installation_id=456,
            expected_repository_id=123,
        )


def test_setup_session_expires_within_its_configured_lifetime(monkeypatch) -> None:
    manager = SetupSessionManager("secret-for-state-signatures", 600)
    response = Response()
    manager.attach(
        response,
        SecretStr("private-github-token"),
        "browser-nonce",
        installation_id=456,
        repository_id=123,
    )
    value = response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    import cryptography.fernet

    now = cryptography.fernet.time.time()
    monkeypatch.setattr(cryptography.fernet.time, "time", lambda: now + 601)
    with pytest.raises(InvalidSetupSessionError):
        manager.read(
            value, "browser-nonce", expected_installation_id=456, expected_repository_id=123
        )


def test_setup_session_scope_separates_discovery_from_repository_writes() -> None:
    manager = SetupSessionManager("secret-for-state-signatures", 600)
    response = Response()
    manager.attach(response, SecretStr("discovery-token"), "browser-nonce", scope="discovery")
    value = response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    assert manager.read(value, "browser-nonce", expected_scope="discovery") is not None
    with pytest.raises(InvalidSetupSessionError):
        manager.read(
            value, "browser-nonce", expected_installation_id=456, expected_repository_id=123
        )


def test_repository_session_cannot_be_replayed_for_another_repository() -> None:
    manager = SetupSessionManager("secret-for-state-signatures", 600)
    response = Response()
    manager.attach(
        response,
        SecretStr("broad-discovery-token"),
        "browser-nonce",
        installation_id=456,
        repository_id=123,
    )
    value = response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    with pytest.raises(InvalidSetupSessionError):
        manager.read(
            value, "browser-nonce", expected_installation_id=456, expected_repository_id=789
        )
    with pytest.raises(InvalidSetupSessionError):
        manager.read(
            value, "browser-nonce", expected_installation_id=789, expected_repository_id=123
        )
    with pytest.raises(InvalidSetupSessionError):
        manager.read(value, "browser-nonce", expected_scope="discovery")


def test_setup_session_is_removed_after_successful_confirmation() -> None:
    manager = SetupSessionManager("secret-for-state-signatures", 600)
    response = Response()

    manager.clear(response)

    cookie = response.headers["set-cookie"]
    assert "cms_setup_session=" in cookie
    assert "Max-Age=0" in cookie
    assert "Path=/setup" in cookie
