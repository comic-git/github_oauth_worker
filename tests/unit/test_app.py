"""Tests for bootstrap and ready-mode application surfaces."""

import asyncio
import logging
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from pydantic import SecretStr

from github_oauth_worker.app import create_app
from github_oauth_worker.bindings import BindingStatus, InMemoryBindingStore, RepositoryBinding
from github_oauth_worker.config import AccessPolicyMode, ServiceMode, WorkerSettings
from github_oauth_worker.errors import WorkerUnavailableError
from github_oauth_worker.github_client import (
    GitHubAccessVerificationError,
    GitHubRepository,
    GitHubRepositoryOwner,
    GitHubUser,
    GitHubUserAccessToken,
)


def test_health_reports_bootstrap_without_configuration() -> None:
    response = TestClient(create_app()).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "bootstrap"}


def test_oauth_routes_are_unavailable_in_bootstrap_mode() -> None:
    response = TestClient(create_app()).get("/auth")

    assert response.status_code == 404


def test_health_reports_ready_without_configuration_values() -> None:
    response = TestClient(
        create_app(
            WorkerSettings(
                service_mode=ServiceMode.READY,
                gcp_project_id="example-project",
                public_base_url="https://worker.example.com",
                github_app_client_id="client-id",
                github_app_client_secret="client-secret",
                state_signing_secret="state-secret",
            )
        )
    ).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_application_configures_the_dedicated_event_logger() -> None:
    create_app(_ready_settings())

    logger = logging.getLogger("github_oauth_worker.events")
    assert logger.getEffectiveLevel() == logging.INFO
    assert logger.propagate is False


def test_expected_worker_errors_have_generic_browser_safe_responses() -> None:
    app = create_app()

    @app.get("/unavailable")
    def unavailable() -> None:
        raise WorkerUnavailableError("sensitive deployment detail")

    response = TestClient(app).get("/unavailable")

    assert response.status_code == 503
    assert response.json() == {"error": "The OAuth worker is not configured."}
    assert "sensitive deployment detail" not in response.text


def test_ready_callback_requires_cookie_and_verifies_a_bound_repository() -> None:
    store = InMemoryBindingStore()
    asyncio.run(
        store.create_binding(
            RepositoryBinding.create(
                repository_id=123,
                repository_owner="owner",
                repository_name="comic",
                installation_id=456,
                origin="https://cms.example.com",
            )
        )
    )
    app = create_app(_ready_settings(), binding_store=store, github_client=_FakeGitHubClient())
    client = TestClient(app)

    handshake_response = client.post(
        "/auth/handshake",
        json={"origin": "https://cms.example.com"},
        follow_redirects=False,
    )
    state = parse_qs(urlparse(handshake_response.headers["location"]).query)["state"][0]
    cookie_value = handshake_response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    missing_cookie_response = client.get(f"/callback?code=one-time-code&state={state}")
    assert missing_cookie_response.status_code == 400

    callback_response = client.get(
        f"/callback?code=one-time-code&state={state}",
        headers={"Cookie": f"oauth_correlation_decap={cookie_value}"},
    )
    assert callback_response.status_code == 200
    assert 'const targetOrigin = "https://cms.example.com"' in callback_response.text
    assert "authorization:github:success:" in callback_response.text


def test_ready_refresh_requires_a_bound_browser_origin_and_rechecks_github_access() -> None:
    store = InMemoryBindingStore()
    asyncio.run(
        store.create_binding(
            RepositoryBinding.create(
                repository_id=123,
                repository_owner="owner",
                repository_name="comic",
                installation_id=456,
                origin="https://cms.example.com",
            )
        )
    )
    app = create_app(
        _ready_settings(),
        binding_store=store,
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app)

    response = client.post(
        "/auth/refresh?provider=github",
        content="refresh_token=refresh-token",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://cms.example.com",
        },
    )

    assert response.status_code == 200
    assert response.json()["access_token"] == "access-token"
    assert response.headers["access-control-allow-origin"] == "https://cms.example.com"

    denied_response = client.post(
        "/auth/refresh?provider=github",
        content="refresh_token=refresh-token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert denied_response.status_code == 400


def test_unknown_origin_enrollment_requires_fresh_confirmation_before_binding() -> None:
    store = InMemoryBindingStore()
    client = TestClient(
        create_app(
            _ready_settings(),
            binding_store=store,
            github_client=_FakeGitHubClient(),
        )
    )

    enrollment_start = client.post(
        "/auth/handshake",
        json={"origin": "https://new-cms.example.com"},
        follow_redirects=False,
    )
    enrollment_state = parse_qs(urlparse(enrollment_start.headers["location"]).query)["state"][0]
    enrollment_cookie = enrollment_start.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    confirmation_page = client.get(
        f"/callback?code=one-time-code&state={enrollment_state}",
        headers={"Cookie": f"oauth_correlation_enrollment={enrollment_cookie}"},
    )
    assert confirmation_page.status_code == 200
    assert "owner/comic" in confirmation_page.text

    setup_start = client.post(
        "/enroll/select",
        content=f"state={enrollment_state}&selection=456%3A123",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": f"oauth_correlation_enrollment={enrollment_cookie}",
        },
        follow_redirects=False,
    )
    setup_state = parse_qs(urlparse(setup_start.headers["location"]).query)["state"][0]
    setup_cookie = setup_start.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    setup_callback = client.get(
        f"/callback?code=one-time-code&state={setup_state}",
        headers={"Cookie": f"oauth_correlation_setup={setup_cookie}"},
    )
    assert setup_callback.status_code == 200
    assert "authorization:github:success:" in setup_callback.text
    assert asyncio.run(store.get_binding_for_origin("https://new-cms.example.com")) is not None


def test_app_setup_redirect_requires_fresh_authorization_for_its_same_installation() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app)

    setup_page = client.get("/setup?installation_id=456")
    assert setup_page.status_code == 200
    assert 'name="installation_id" value="456"' in setup_page.text

    setup_start = client.post(
        "/setup/continue",
        content="installation_id=456",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    setup_state = parse_qs(urlparse(setup_start.headers["location"]).query)["state"][0]
    setup_cookie = setup_start.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    confirmation_page = client.get(
        f"/callback?code=one-time-code&state={setup_state}",
        headers={"Cookie": f"oauth_correlation_cms_enablement_installation={setup_cookie}"},
    )
    assert confirmation_page.status_code == 200
    assert "owner/comic" in confirmation_page.text

    selection = client.post(
        "/setup/select",
        content=f"state={setup_state}&repository_id=123",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": f"oauth_correlation_cms_enablement_installation={setup_cookie}",
        },
        follow_redirects=False,
    )
    assert selection.status_code == 302
    assert "repository_id=123" in selection.headers["location"]


def test_cms_setup_confirmation_requires_the_reviewed_state_cookie() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app)
    issued = app.state.state_manager.issue_cms_enablement_confirmation(
        123, 456, "a" * 40, "cms", "b" * 40
    )

    response = client.post(
        "/setup/confirm",
        content=f"state={issued.token}",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": f"{issued.cookie_name}={issued.correlation_nonce}",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert "repository_id=123" in response.headers["location"]


def test_cms_setup_callback_renders_a_safe_error_without_a_decap_origin() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app)
    issued = app.state.state_manager.issue_cms_enablement_confirmation(
        123, 456, "a" * 40, "cms", "b" * 40
    )

    response = client.get(
        f"/callback?state={issued.token}",
        headers={"Cookie": f"{issued.cookie_name}={issued.correlation_nonce}"},
    )

    assert response.status_code == 400
    assert "The request could not be completed." in response.text


def test_origin_migration_uses_the_exact_destination_handshake_before_activation() -> None:
    store = InMemoryBindingStore()
    asyncio.run(
        store.create_binding(
            RepositoryBinding.create(
                repository_id=123,
                repository_owner="owner",
                repository_name="comic",
                installation_id=456,
                origin="https://old-cms.example.com",
            )
        )
    )
    client = TestClient(
        create_app(_ready_settings(), binding_store=store, github_client=_FakeGitHubClient())
    )

    start = client.post(
        "/origins/migrate/handshake",
        json={
            "origin": "https://old-cms.example.com",
            "target_origin": "https://new-cms.example.com",
        },
        follow_redirects=False,
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    cookie = start.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    prepared = client.get(
        f"/callback?code=one-time-code&state={state}",
        headers={"Cookie": f"oauth_correlation_origin_migration={cookie}"},
    )
    assert prepared.status_code == 200
    assert awaitable_result(store.get_binding_for_origin("https://new-cms.example.com")) is None

    complete = client.post(
        "/origins/complete/handshake",
        json={"origin": "https://new-cms.example.com"},
    )
    assert complete.status_code == 200
    assert awaitable_result(store.get_binding_for_origin("https://new-cms.example.com")) is not None


def test_failed_github_verification_marks_the_binding_for_recovery() -> None:
    store = InMemoryBindingStore()
    asyncio.run(
        store.create_binding(
            RepositoryBinding.create(
                repository_id=123,
                repository_owner="owner",
                repository_name="comic",
                installation_id=456,
                origin="https://cms.example.com",
            )
        )
    )
    client = TestClient(
        create_app(
            _ready_settings(),
            binding_store=store,
            github_client=_FakeGitHubClient(fail_repository_verification=True),
        )
    )

    start = client.post(
        "/auth/handshake",
        json={"origin": "https://cms.example.com"},
        follow_redirects=False,
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    cookie = start.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    response = client.get(
        f"/callback?code=one-time-code&state={state}",
        headers={"Cookie": f"oauth_correlation_decap={cookie}"},
    )

    assert response.status_code == 400
    assert awaitable_result(store.get_binding(123)).status is BindingStatus.RECOVERY_REQUIRED


def _ready_settings() -> WorkerSettings:
    return WorkerSettings(
        service_mode=ServiceMode.READY,
        gcp_project_id="example-project",
        public_base_url="https://worker.example.com",
        github_app_client_id="client-id",
        github_app_client_secret="client-secret",
        state_signing_secret="state-secret",
        access_policy=AccessPolicyMode.PUBLIC,
    )


def awaitable_result(awaitable):
    """Run the small async store assertions without introducing an async HTTP-test framework."""
    return asyncio.run(awaitable)


class _FakeGitHubClient:
    """Return one verified repository without making external network calls."""

    async def exchange_authorization_code(
        self,
        authorization_code: str,
        repository_id: int | None = None,
    ) -> GitHubUserAccessToken:
        assert authorization_code == "one-time-code"
        assert repository_id in (None, 123)
        return GitHubUserAccessToken(
            access_token=SecretStr("access-token"),
            token_type="bearer",
            expires_in=28_800,
            refresh_token=SecretStr("refresh-token"),
        )

    async def get_authenticated_user(self, user_access_token: SecretStr) -> GitHubUser:
        assert user_access_token.get_secret_value() == "access-token"
        return GitHubUser(login="marco")

    async def refresh_user_access_token(self, refresh_token: SecretStr) -> GitHubUserAccessToken:
        assert refresh_token.get_secret_value() == "refresh-token"
        return GitHubUserAccessToken(
            access_token=SecretStr("access-token"),
            token_type="bearer",
            expires_in=28_800,
            refresh_token=SecretStr("next-refresh-token"),
        )

    async def list_accessible_installations(
        self,
        user_access_token: SecretStr,
    ) -> tuple[object, ...]:
        assert user_access_token.get_secret_value() == "access-token"
        return (_FakeInstallation(id=456),)

    async def list_repositories_for_installation(
        self,
        user_access_token: SecretStr,
        installation_id: int,
    ) -> tuple[GitHubRepository, ...]:
        assert user_access_token.get_secret_value() == "access-token"
        assert installation_id == 456
        return (
            GitHubRepository(
                id=123,
                name="comic",
                owner=GitHubRepositoryOwner(login="owner"),
            ),
        )

    def __init__(self, fail_repository_verification: bool = False) -> None:
        self._fail_repository_verification = fail_repository_verification

    async def verify_bound_repository_access(
        self,
        user_access_token: SecretStr,
        installation_id: int,
        repository_id: int,
    ) -> GitHubRepository:
        if self._fail_repository_verification:
            raise GitHubAccessVerificationError
        assert user_access_token.get_secret_value() == "access-token"
        assert installation_id == 456
        assert repository_id == 123
        return GitHubRepository(
            id=123,
            name="comic",
            owner=GitHubRepositoryOwner(login="owner"),
        )


class _FakeInstallation:
    """Minimal installation double for the enrollment repository-selection response."""

    def __init__(self, id: int) -> None:
        self.id = id
