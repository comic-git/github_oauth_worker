"""Tests for worker startup configuration and boundary validation."""

import pytest
from pydantic import ValidationError

from github_oauth_worker.config import (
    AccessPolicyMode,
    ConfigurationError,
    Environment,
    ServiceMode,
    WorkerSettings,
    load_settings,
)


def test_bootstrap_settings_need_no_runtime_credentials() -> None:
    settings = WorkerSettings()

    assert settings.service_mode is ServiceMode.BOOTSTRAP
    assert settings.firestore_database_id == "test"
    assert settings.github_login_whitelist == frozenset()


def test_whitelist_is_casefolded_and_uses_comma_separated_config() -> None:
    settings = WorkerSettings(github_login_whitelist="Marco, editor, MARCO")

    assert settings.github_login_whitelist == frozenset({"marco", "editor"})


def test_cms_engine_policy_preserves_branch_names_and_accepts_canonical_minimum_version() -> None:
    settings = WorkerSettings(
        cms_minimum_engine_version="1.2.3",
        cms_allowed_engine_branches="latest, master, cms",
    )

    assert settings.cms_minimum_engine_version == "1.2.3"
    assert settings.cms_allowed_engine_branches == frozenset({"latest", "master", "cms"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cms_minimum_engine_version", "v1.2"),
        ("cms_minimum_engine_version", "1.02"),
        ("cms_allowed_engine_branches", "latest,../unsafe"),
        ("cms_allowed_engine_branches", ""),
    ],
)
def test_cms_engine_policy_rejects_invalid_config(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        WorkerSettings(**{field: value})


def test_ready_mode_requires_all_sensitive_runtime_values() -> None:
    with pytest.raises(ValidationError, match="Ready mode requires"):
        WorkerSettings(service_mode=ServiceMode.READY)


def test_ready_mode_rejects_database_from_another_environment() -> None:
    with pytest.raises(ValidationError, match="must use Firestore database"):
        WorkerSettings(
            service_mode=ServiceMode.READY,
            environment=Environment.PRODUCTION,
            gcp_project_id="example-project",
            firestore_database_id="test",
            public_base_url="https://worker.example.com",
            github_app_client_id="client-id",
            github_app_client_secret="client-secret",
            state_signing_secret="state-secret",
        )


def test_ready_mode_rejects_a_non_https_callback_url() -> None:
    with pytest.raises(ValidationError, match="HTTPS origin"):
        WorkerSettings(
            service_mode=ServiceMode.READY,
            gcp_project_id="example-project",
            public_base_url="http://worker.example.com",
            github_app_client_id="client-id",
            github_app_client_secret="client-secret",
            state_signing_secret="state-secret",
        )


def test_ready_mode_exposes_only_validated_firestore_identifiers() -> None:
    settings = WorkerSettings(
        service_mode=ServiceMode.READY,
        gcp_project_id="example-project",
        public_base_url="https://worker.example.com",
        github_app_client_id="client-id",
        github_app_client_secret="client-secret",
        state_signing_secret="state-secret",
        access_policy=AccessPolicyMode.PUBLIC,
    )

    assert settings.firestore.project_id == "example-project"
    assert settings.firestore.database_id == "test"


@pytest.mark.parametrize("ttl_seconds", [59, 3_601])
def test_oauth_state_ttl_is_limited_to_a_short_lifetime(ttl_seconds: int) -> None:
    with pytest.raises(ValidationError, match="OAUTH_STATE_TTL_SECONDS"):
        WorkerSettings(oauth_state_ttl_seconds=ttl_seconds)


def test_load_settings_hides_secret_input_when_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SERVICE_MODE", "ready")
    monkeypatch.setenv("GITHUB_APP_CLIENT_SECRET", "not-for-error-output")

    with pytest.raises(ConfigurationError) as error:
        load_settings()

    assert "not-for-error-output" not in str(error.value)
    assert str(error.value) == "Worker configuration is invalid."
    assert error.value.__cause__ is None
