"""Tests for bootstrap and ready-mode application surfaces."""

import asyncio
import base64
import logging
import re
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from pydantic import SecretStr

from github_oauth_worker.app import _migration_plan_digest, create_app
from github_oauth_worker.bindings import BindingStatus, InMemoryBindingStore, RepositoryBinding
from github_oauth_worker.cms_access_delivery import CmsAccessDeliveryResult
from github_oauth_worker.cms_enablement import (
    CmsEnablementError,
    CmsMigrationPreview,
    MigrationFileChange,
    MigrationFileChangeKind,
    MigrationPlanFile,
    TargetRepositoryState,
    migration_branch_name,
)
from github_oauth_worker.cms_setup import render_cms_migration_summary_page
from github_oauth_worker.config import AccessPolicyMode, ServiceMode, WorkerSettings
from github_oauth_worker.errors import WorkerUnavailableError
from github_oauth_worker.github_client import (
    GitHubAccessVerificationError,
    GitHubClientError,
    GitHubGitBlob,
    GitHubGitCommit,
    GitHubGitObject,
    GitHubGitReference,
    GitHubGitSha,
    GitHubGitTree,
    GitHubGitTreeEntry,
    GitHubPullRequest,
    GitHubPullRequestDetails,
    GitHubPullRequestRef,
    GitHubRepository,
    GitHubRepositoryBranch,
    GitHubRepositoryOwner,
    GitHubUser,
    GitHubUserAccessToken,
)
from github_oauth_worker.state import OAuthFlow


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
        data={"origin": "https://cms.example.com"},
        follow_redirects=False,
    )
    assert handshake_response.status_code == 302
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


def test_setup_handshake_form_navigates_to_github_with_cookie_bound_state() -> None:
    client = TestClient(
        create_app(
            _ready_settings(),
            binding_store=InMemoryBindingStore(),
            github_client=_FakeGitHubClient(),
        )
    )

    response = client.post(
        "/setup/handshake",
        data={"origin": "https://cms.example.com", "installation_id": "456"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://github.com/login/oauth/authorize?")
    assert "oauth_correlation_setup=" in response.headers["set-cookie"]


def test_guided_setup_discovers_installed_repositories_before_selection() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app)

    landing = client.get("/setup")
    assert landing.status_code == 200
    assert 'action="/setup/start"' in landing.text
    assert 'action="/setup/switch-account"' not in landing.text

    start = client.post("/setup/start", follow_redirects=False)
    assert start.status_code == 302
    assert start.headers["location"].startswith("https://github.com/login/oauth/authorize?")
    assert "repository_id=" not in start.headers["location"]
    state_token = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    nonce = start.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    cookie = f"oauth_correlation_cms_enablement_discovery={nonce}"
    state = app.state.state_manager.consume(
        state_token, nonce, OAuthFlow.CMS_ENABLEMENT_DISCOVERY
    )
    assert state.repository_id is None
    assert state.installation_id is None

    discovery = client.get(
        f"/callback?code=one-time-code&state={state_token}",
        headers={"Cookie": cookie},
    )
    assert discovery.status_code == 200
    assert "owner/comic" in discovery.text
    assert 'value="456:123"' in discovery.text
    assert "https://github.com/apps/comic-git-test/installations/new" in discovery.text

    selected = client.post(
        "/setup/select-discovered",
        data={"state": state_token, "selection": "456:123"},
        headers={"Cookie": cookie},
        follow_redirects=False,
    )
    assert selected.status_code == 302
    assert "repository_id=123" in selected.headers["location"]
    selected_token = parse_qs(urlparse(selected.headers["location"]).query)["state"][0]
    selected_nonce = selected.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    selected_state = app.state.state_manager.consume(
        selected_token, selected_nonce, OAuthFlow.CMS_ENABLEMENT_REPOSITORY
    )
    assert (selected_state.installation_id, selected_state.repository_id) == (456, 123)


def test_guided_setup_reuses_discovery_authorization_through_review(monkeypatch) -> None:
    async def read_target(*_args: object) -> TargetRepositoryState:
        return TargetRepositoryState(
            base_commit_sha="a" * 40,
            target_branch="cms",
            tree=GitHubGitTree(sha="b" * 40, tree=()),
            config_path="your_content/comic_info.toml",
            engine_selector="cms",
        )

    async def resolve_engine(*_args: object) -> str:
        return "c" * 40

    async def build_preview(*_args: object) -> CmsMigrationPreview:
        return CmsMigrationPreview(
            base_commit_sha="a" * 40,
            target_branch="cms",
            engine_selector="cms",
            engine_commit_sha="c" * 40,
            changed_paths=("your_content/comic_info.toml",),
            files=(),
        )

    monkeypatch.setattr("github_oauth_worker.app.read_target_repository_state", read_target)
    monkeypatch.setattr("github_oauth_worker.app.resolve_official_engine_commit", resolve_engine)
    monkeypatch.setattr("github_oauth_worker.app.build_migration_preview", build_preview)
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app, base_url="https://worker.example.com")

    start = client.post("/setup/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    discovery = client.get(f"/callback?code=one-time-code&state={state}")
    assert discovery.status_code == 200
    assert "cms_setup_session" in client.cookies
    assert (
        app.state.setup_sessions.read(
            client.cookies["cms_setup_session"],
            client.cookies["oauth_correlation_cms_enablement_discovery"],
            expected_scope="discovery",
        )
        is not None
    )
    assert "access-token" not in discovery.text

    selected = client.post(
        "/setup/select-discovered",
        data={"state": state, "selection": "456:123"},
        follow_redirects=False,
    )
    assert selected.status_code == 200
    branch_page = selected
    assert branch_page.status_code == 200
    assert "Select comic branch" in branch_page.text
    assert "cms_setup_session" in client.cookies
    assert (
        app.state.setup_sessions.read(
            client.cookies["cms_setup_session"],
            client.cookies["oauth_correlation_cms_enablement_repository"],
            expected_installation_id=456,
            expected_repository_id=123,
        )
        is not None
    )
    branch_state = re.search('name="state" value="([^"]+)"', branch_page.text)
    assert branch_state is not None

    review = client.post(
        "/setup/select-branch",
        data={"state": branch_state.group(1), "target_branch": "cms"},
        follow_redirects=False,
    )
    assert review.status_code == 200
    assert "Target branch: cms" in review.text
    assert "Create migration pull request" in review.text


def test_guided_setup_rejects_a_tampered_session_on_branch_selection() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app, base_url="https://worker.example.com")
    start = client.post("/setup/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    assert client.get(f"/callback?code=one-time-code&state={state}").status_code == 200
    selected = client.post(
        "/setup/select-discovered",
        data={"state": state, "selection": "456:123"},
        follow_redirects=False,
    )
    branch_page = selected
    assert branch_page.status_code == 200
    branch_state = re.search('name="state" value="([^"]+)"', branch_page.text)
    assert branch_state is not None

    client.cookies.set("cms_setup_session", "tampered", path="/setup")
    response = client.post(
        "/setup/select-branch",
        data={"state": branch_state.group(1), "target_branch": "cms"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "CMS setup session expired" in response.text
    assert 'href="/setup"' in response.text


def test_guided_setup_rechecks_github_access_before_showing_branches() -> None:
    github_client = _FakeGitHubClient()
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=github_client,
    )
    client = TestClient(app, base_url="https://worker.example.com")
    start = client.post("/setup/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    assert client.get(f"/callback?code=one-time-code&state={state}").status_code == 200
    selected = client.post(
        "/setup/select-discovered",
        data={"state": state, "selection": "456:123"},
        follow_redirects=False,
    )
    branch_page = selected
    assert branch_page.status_code == 200
    branch_state = re.search('name="state" value="([^"]+)"', branch_page.text)
    assert branch_state is not None
    github_client._fail_repository_verification = True

    response = client.post(
        "/setup/select-branch",
        data={"state": branch_state.group(1), "target_branch": "cms"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "GitHub could not verify access" in response.text


def test_guided_setup_falls_back_to_oauth_when_discovery_session_is_invalid() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app, base_url="https://worker.example.com")
    start = client.post("/setup/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    assert client.get(f"/callback?code=one-time-code&state={state}").status_code == 200
    client.cookies.set("cms_setup_session", "tampered", path="/setup")

    selected = client.post(
        "/setup/select-discovered",
        data={"state": state, "selection": "456:123"},
        follow_redirects=False,
    )

    assert selected.status_code == 302
    assert "repository_id=123" in selected.headers["location"]


def test_guided_setup_rejects_replaying_selected_session_for_another_repository() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app, base_url="https://worker.example.com")
    start = client.post("/setup/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    assert client.get(f"/callback?code=one-time-code&state={state}").status_code == 200
    selected = client.post(
        "/setup/select-discovered",
        data={"state": state, "selection": "456:123"},
    )
    assert selected.status_code == 200
    nonce = client.cookies["oauth_correlation_cms_enablement_repository"]
    other_repository = app.state.state_manager.issue_cms_enablement_repository(
        789, 456, correlation_nonce=nonce
    )

    response = client.post(
        "/setup/select-branch",
        data={"state": other_repository.token, "target_branch": "cms"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "CMS setup session expired" in response.text


def test_guided_setup_with_no_installations_offers_github_install() -> None:
    class NoInstallationsClient(_FakeGitHubClient):
        async def list_accessible_installations(self, user_access_token: SecretStr) -> tuple:
            return ()

    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=NoInstallationsClient(),
    )
    client = TestClient(app)
    start = client.post("/setup/start", follow_redirects=False)
    state_token = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    nonce = start.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    discovery = client.get(
        f"/callback?code=one-time-code&state={state_token}",
        headers={"Cookie": f"oauth_correlation_cms_enablement_discovery={nonce}"},
    )
    assert discovery.status_code == 200
    assert "No repositories are connected" in discovery.text
    assert "https://github.com/apps/comic-git-test/installations/new" in discovery.text
    assert 'action="/setup/select-discovered"' not in discovery.text


def test_guided_setup_shows_repositories_from_multiple_installations() -> None:
    class MultipleInstallationsClient(_FakeGitHubClient):
        async def list_accessible_installations(self, user_access_token: SecretStr) -> tuple:
            return (_FakeInstallation(id=456), _FakeInstallation(id=789))

        async def list_repositories_for_installation(
            self, user_access_token: SecretStr, installation_id: int
        ) -> tuple[GitHubRepository, ...]:
            return (
                GitHubRepository(
                    id=installation_id,
                    name=f"comic-{installation_id}",
                    owner=GitHubRepositoryOwner(login="owner"),
                ),
            )

    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=MultipleInstallationsClient(),
    )
    client = TestClient(app)
    start = client.post("/setup/start", follow_redirects=False)
    state_token = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    nonce = start.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    cookie = f"oauth_correlation_cms_enablement_discovery={nonce}"

    discovery = client.get(
        f"/callback?code=one-time-code&state={state_token}",
        headers={"Cookie": cookie},
    )
    assert discovery.status_code == 200
    assert 'value="456:456"' in discovery.text
    assert 'value="789:789"' in discovery.text

    selected = client.post(
        "/setup/select-discovered",
        data={"state": state_token, "selection": "789:789"},
        headers={"Cookie": cookie},
        follow_redirects=False,
    )
    assert selected.status_code == 302
    selected_token = parse_qs(urlparse(selected.headers["location"]).query)["state"][0]
    selected_nonce = selected.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    selected_state = app.state.state_manager.consume(
        selected_token, selected_nonce, OAuthFlow.CMS_ENABLEMENT_REPOSITORY
    )
    assert (selected_state.installation_id, selected_state.repository_id) == (789, 789)


def test_guided_setup_cancellation_offers_a_safe_restart() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app)
    start = client.post("/setup/start", follow_redirects=False)
    state_token = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    nonce = start.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    cancelled = client.get(
        f"/callback?error=access_denied&state={state_token}",
        headers={"Cookie": f"oauth_correlation_cms_enablement_discovery={nonce}"},
    )

    assert cancelled.status_code == 400
    assert "GitHub authorization was not completed" in cancelled.text
    assert '<a href="/setup">Start CMS setup again</a>' in cancelled.text


def test_guided_setup_rejects_selection_without_its_discovery_cookie() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    issued = app.state.state_manager.issue(OAuthFlow.CMS_ENABLEMENT_DISCOVERY)
    client = TestClient(app)

    missing_cookie = client.post(
        "/setup/select-discovered",
        data={"state": issued.token, "selection": "456:123"},
        follow_redirects=False,
    )
    invalid_selection = client.post(
        "/setup/select-discovered",
        data={"state": issued.token, "selection": "0:123"},
        headers={"Cookie": f"{issued.cookie_name}={issued.correlation_nonce}"},
        follow_redirects=False,
    )

    assert missing_cookie.status_code == 400
    assert invalid_selection.status_code == 400


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
        data={"origin": "https://new-cms.example.com"},
        follow_redirects=False,
    )
    assert enrollment_start.status_code == 302
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

    repository_state = parse_qs(urlparse(selection.headers["location"]).query)["state"][0]
    repository_cookie = selection.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    branch_page = client.get(
        f"/callback?code=one-time-code&state={repository_state}",
        headers={"Cookie": f"oauth_correlation_cms_enablement_repository={repository_cookie}"},
    )
    assert branch_page.status_code == 200
    assert "Select comic branch" in branch_page.text
    assert '<option value="main" selected>' in branch_page.text
    assert '<option value="cms">' in branch_page.text

    branch_state = re.search('name="state" value="([^"]+)"', branch_page.text)
    assert branch_state is not None
    branch_cookie = branch_page.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    branch_selection = client.post(
        "/setup/select-branch",
        content=f"state={branch_state.group(1)}&target_branch=cms",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": f"oauth_correlation_cms_enablement_repository={branch_cookie}",
        },
        follow_redirects=False,
    )
    assert branch_selection.status_code == 302
    assert "repository_id=123" in branch_selection.headers["location"]


def test_app_install_return_reuses_token_after_repository_selection() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app, base_url="https://worker.example.com")
    start = client.post("/setup/continue", data={"installation_id": "456"}, follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    repositories = client.get(f"/callback?code=one-time-code&state={state}")
    assert repositories.status_code == 200
    assert "owner/comic" in repositories.text

    selected = client.post(
        "/setup/select",
        data={"state": state, "repository_id": "123"},
        follow_redirects=False,
    )
    assert selected.status_code == 200
    branches = selected
    assert branches.status_code == 200
    assert "Select comic branch" in branches.text
    assert "cms_setup_session" in client.cookies


def test_app_install_return_reuses_discovery_authorization_when_installation_is_visible() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app, base_url="https://worker.example.com")
    start = client.post("/setup/start", follow_redirects=False)
    discovery_state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    discovery = client.get(f"/callback?code=one-time-code&state={discovery_state}")
    assert discovery.status_code == 200
    assert "cms_setup_session" in client.cookies

    return_page = client.post("/setup/continue", data={"installation_id": "456"})
    assert return_page.status_code == 200
    assert "owner/comic" in return_page.text
    assert "cms_setup_session" in client.cookies
    installation_state = re.search('name="state" value="([^"]+)"', return_page.text)
    assert installation_state is not None

    selected = client.post(
        "/setup/select",
        data={"state": installation_state.group(1), "repository_id": "123"},
        follow_redirects=False,
    )
    assert selected.status_code == 200
    assert "Select comic branch" in selected.text


def test_app_install_return_reauthorizes_if_discovery_token_cannot_see_installation() -> None:
    class ChangedInstallationsClient(_FakeGitHubClient):
        def __init__(self) -> None:
            super().__init__()
            self.visible = True

        async def list_accessible_installations(self, user_access_token: SecretStr) -> tuple:
            return (_FakeInstallation(id=456),) if self.visible else ()

    github_client = ChangedInstallationsClient()
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=github_client,
    )
    client = TestClient(app, base_url="https://worker.example.com")
    start = client.post("/setup/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    assert client.get(f"/callback?code=one-time-code&state={state}").status_code == 200

    github_client.visible = False
    return_page = client.post(
        "/setup/continue", data={"installation_id": "456"}, follow_redirects=False
    )
    assert return_page.status_code == 302
    assert return_page.headers["location"].startswith("https://github.com/login/oauth/authorize?")


def test_guided_setup_can_switch_github_accounts() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app, base_url="https://worker.example.com")
    start = client.post("/setup/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    discovery = client.get(f"/callback?code=one-time-code&state={state}")
    assert 'action="/setup/switch-account"' in discovery.text
    assert "cms_setup_session" in client.cookies

    switch = client.post("/setup/switch-account", follow_redirects=False)
    assert switch.status_code == 302
    assert parse_qs(urlparse(switch.headers["location"]).query)["prompt"] == ["select_account"]
    assert "cms_setup_session" not in client.cookies


def test_cms_setup_confirmation_requires_the_reviewed_state_cookie() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app)
    issued = app.state.state_manager.issue_cms_enablement_confirmation(
        123, 456, "master", "a" * 40, "cms", "b" * 40, "0" * 64
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
    redirected_token = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
    redirected_cookie = response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    redirected = app.state.state_manager.consume(
        redirected_token,
        redirected_cookie,
        expected_flow=OAuthFlow.CMS_ENABLEMENT_CONFIRMATION,
    )
    assert redirected.reviewed_plan_digest == "0" * 64


def test_session_confirmation_rechecks_access_and_clears_its_cookie(monkeypatch) -> None:
    base_sha = "a" * 40
    engine_sha = "c" * 40
    branch_name = migration_branch_name("cms", base_sha)
    preview = CmsMigrationPreview(
        base_commit_sha=base_sha,
        target_branch="cms",
        engine_selector="cms",
        engine_commit_sha=engine_sha,
        changed_paths=(),
        files=(),
    )

    async def read_target(*_args: object) -> TargetRepositoryState:
        return TargetRepositoryState(
            base_commit_sha=base_sha,
            target_branch="cms",
            tree=GitHubGitTree(sha="b" * 40, tree=()),
            config_path="your_content/comic_info.toml",
            engine_selector="cms",
        )

    async def resolve_engine(*_args: object) -> str:
        return engine_sha

    async def build_preview(*_args: object) -> CmsMigrationPreview:
        return preview

    class ExistingPrClient(_FakeGitHubClient):
        def __init__(self) -> None:
            super().__init__()
            self.admin_checks = 0

        async def require_repository_administrator(self, token, repository, login):
            await super().require_repository_administrator(token, repository, login)
            self.admin_checks += 1

        async def list_open_repository_pull_requests(self, _token, repository, base):
            assert base == "cms"
            return (
                GitHubPullRequestDetails(
                    number=4,
                    html_url="https://github.com/owner/comic/pull/4",
                    state="open",
                    head=GitHubPullRequestRef(ref=branch_name, sha="d" * 40, repo=repository),
                    base=GitHubPullRequestRef(ref="cms", sha=base_sha, repo=repository),
                    body=f"Resolved engine SHA: `{engine_sha}`",
                ),
            )

        async def get_repository_ref(self, _token, _repository, ref):
            assert ref == f"heads/{branch_name}"
            return GitHubGitReference(
                ref=f"refs/{ref}", object=GitHubGitObject(type="commit", sha="d" * 40)
            )

        async def get_repository_commit(self, _token, _repository, sha):
            assert sha == "d" * 40
            return GitHubGitCommit(sha=sha, tree=GitHubGitSha(sha="e" * 40))

        async def get_repository_tree(self, _token, _repository, sha):
            assert sha == "e" * 40
            return GitHubGitTree(sha=sha, tree=())

    monkeypatch.setattr("github_oauth_worker.app.read_target_repository_state", read_target)
    monkeypatch.setattr("github_oauth_worker.app.resolve_official_engine_commit", resolve_engine)
    monkeypatch.setattr("github_oauth_worker.app.build_migration_preview", build_preview)
    github_client = ExistingPrClient()
    app = create_app(
        _ready_settings(), binding_store=InMemoryBindingStore(), github_client=github_client
    )
    client = TestClient(app, base_url="https://worker.example.com")
    issued = app.state.state_manager.issue_cms_enablement_confirmation(
        123, 456, "cms", base_sha, "cms", engine_sha, _migration_plan_digest(preview)
    )
    client.cookies.set(issued.cookie_name, issued.correlation_nonce, path="/")
    setup_cookie = Response()
    app.state.setup_sessions.attach(
        setup_cookie,
        SecretStr("access-token"),
        issued.correlation_nonce,
        installation_id=456,
        repository_id=123,
    )
    value = setup_cookie.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    client.cookies.set("cms_setup_session", value, path="/setup")

    result = client.post("/setup/confirm", data={"state": issued.token}, follow_redirects=False)

    assert result.status_code == 200
    assert "https://github.com/owner/comic/pull/4" in result.text
    assert github_client.admin_checks == 1
    assert "cms_setup_session=" in result.headers["set-cookie"]
    assert "Max-Age=0" in result.headers["set-cookie"]


def test_cms_setup_review_uses_the_selected_branch_and_sets_its_confirmation_cookie(
    monkeypatch,
) -> None:
    async def read_target_state(
        _github_client: object,
        _user_access_token: SecretStr,
        _repository: GitHubRepository,
        target_branch: str,
    ) -> TargetRepositoryState:
        assert target_branch == "cms"
        return TargetRepositoryState(
            base_commit_sha="a" * 40,
            target_branch=target_branch,
            tree=GitHubGitTree(sha="b" * 40, tree=()),
            config_path="your_content/comic_info.toml",
            engine_selector="cms",
        )

    async def resolve_engine_commit(_github_client: object, _selector: object) -> str:
        return "c" * 40

    async def build_preview(
        _github_client: object,
        _user_access_token: SecretStr,
        _repository: GitHubRepository,
        state: TargetRepositoryState,
        _selector: object,
        engine_commit_sha: str,
        cms_enablement: dict[str, object],
    ) -> CmsMigrationPreview:
        assert cms_enablement["branch"] == "cms"
        assert cms_enablement["backend_base_url"] == "https://worker.example.com"
        assert cms_enablement["backend_auth_endpoint"] == "auth"
        return CmsMigrationPreview(
            base_commit_sha=state.base_commit_sha,
            target_branch=state.target_branch,
            engine_selector=state.engine_selector,
            engine_commit_sha=engine_commit_sha,
            changed_paths=("your_content/comic_info.toml",),
            files=(),
        )

    monkeypatch.setattr("github_oauth_worker.app.read_target_repository_state", read_target_state)
    monkeypatch.setattr(
        "github_oauth_worker.app.resolve_official_engine_commit", resolve_engine_commit
    )
    monkeypatch.setattr("github_oauth_worker.app.build_migration_preview", build_preview)
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app)
    issued_state = app.state.state_manager.issue_cms_enablement_branch(123, 456, "cms")

    response = client.get(
        f"/callback?code=one-time-code&state={issued_state.token}",
        headers={"Cookie": f"{issued_state.cookie_name}={issued_state.correlation_nonce}"},
    )

    assert response.status_code == 200
    assert "Target branch: cms" in response.text
    assert "Create migration pull request" in response.text
    assert "Existing pull request" not in response.text
    confirmation_state = re.search('name="state" value="([^"]+)"', response.text)
    assert confirmation_state is not None
    confirmation_cookie = response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    confirmed = app.state.state_manager.consume(
        confirmation_state.group(1),
        confirmation_cookie,
        expected_flow=OAuthFlow.CMS_ENABLEMENT_CONFIRMATION,
    )
    assert confirmed.target_branch == "cms"
    assert confirmed.reviewed_plan_digest is not None


def test_cms_setup_review_links_existing_pr_and_shows_incremental_changes(monkeypatch) -> None:
    base_sha = "a" * 40
    head_sha = "d" * 40
    branch_name = migration_branch_name("cms", base_sha)

    async def read_target(*_args: object) -> TargetRepositoryState:
        return TargetRepositoryState(
            base_commit_sha=base_sha,
            target_branch="cms",
            tree=GitHubGitTree(sha="b" * 40, tree=()),
            config_path="your_content/comic_info.toml",
            engine_selector="cms",
        )

    async def resolve_engine(*_args: object) -> str:
        return "c" * 40

    async def build_preview(*_args: object) -> CmsMigrationPreview:
        return CmsMigrationPreview(
            base_commit_sha=base_sha,
            target_branch="cms",
            engine_selector="cms",
            engine_commit_sha="c" * 40,
            changed_paths=("your_content/comic_info.toml",),
            files=(MigrationPlanFile("your_content/comic_info.toml", "new\n"),),
        )

    class ReviewClient(_FakeGitHubClient):
        async def list_open_repository_pull_requests(self, token, repository, base):
            assert base == "cms"
            return (await self.get_repository_pull_request(token, repository, 4),)

        async def get_repository_pull_request(self, _token, repository, number):
            assert number == 4
            return GitHubPullRequestDetails(
                number=4,
                html_url="https://github.com/owner/comic/pull/4",
                state="open",
                head=GitHubPullRequestRef(ref=branch_name, sha=head_sha, repo=repository),
                base=GitHubPullRequestRef(ref="cms", sha=base_sha, repo=repository),
                body=(
                    "Engine selector: `cms`\n\n"
                    f"Resolved engine SHA: `{'e' * 40}`\n\nTarget branch: `cms`"
                ),
            )

        async def get_repository_ref(self, _token, _repository, ref):
            assert ref == f"heads/{branch_name}"
            return GitHubGitReference(
                ref=f"refs/{ref}", object=GitHubGitObject(type="commit", sha=head_sha)
            )

        async def get_repository_commit(self, _token, _repository, sha):
            assert sha == head_sha
            return GitHubGitCommit(sha=sha, tree=GitHubGitSha(sha="f" * 40))

        async def get_repository_tree(self, _token, _repository, sha):
            assert sha == "f" * 40
            return GitHubGitTree(
                sha=sha,
                tree=(
                    GitHubGitTreeEntry(
                        path="your_content/comic_info.toml",
                        mode="100644",
                        type="blob",
                        sha="1" * 40,
                        size=4,
                    ),
                ),
            )

        async def get_repository_blob(self, _token, _repository, sha):
            assert sha == "1" * 40
            return GitHubGitBlob(sha=sha, content="b2xkCg==", encoding="base64")

    monkeypatch.setattr("github_oauth_worker.app.read_target_repository_state", read_target)
    monkeypatch.setattr("github_oauth_worker.app.resolve_official_engine_commit", resolve_engine)
    monkeypatch.setattr("github_oauth_worker.app.build_migration_preview", build_preview)
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=ReviewClient(),
    )
    issued = app.state.state_manager.issue_cms_enablement_branch(123, 456, "cms")
    response = TestClient(app).get(
        f"/callback?code=one-time-code&state={issued.token}",
        headers={"Cookie": f"{issued.cookie_name}={issued.correlation_nonce}"},
    )

    assert response.status_code == 200
    assert "https://github.com/owner/comic/pull/4" in response.text
    assert "Update migration pull request" in response.text
    assert '<form action="/setup/confirm" method="post"><p>' in response.text
    assert "</p></form>" in response.text
    assert "Create migration pull request" not in response.text
    assert "your_content/comic_info.toml" in response.text
    assert "Updated" in response.text
    confirmation_state = re.search('name="state" value="([^"]+)"', response.text)
    assert confirmation_state is not None
    cookie = response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    confirmed = app.state.state_manager.consume(
        confirmation_state.group(1),
        cookie,
        expected_flow=OAuthFlow.CMS_ENABLEMENT_CONFIRMATION,
    )
    assert confirmed.reviewed_plan_digest is not None


def test_cms_review_of_up_to_date_pull_request_has_no_update_action() -> None:
    preview = CmsMigrationPreview(
        base_commit_sha="a" * 40,
        target_branch="cms",
        engine_selector="cms",
        engine_commit_sha="b" * 40,
        changed_paths=("your_content/comic_info.toml",),
        files=(MigrationPlanFile("your_content/comic_info.toml", "[cms]\n"),),
    )
    page = render_cms_migration_summary_page(
        "signed-state",
        GitHubRepository(id=123, name="comic", owner=GitHubRepositoryOwner(login="owner")),
        preview,
        (MigrationFileChange("your_content/comic_info.toml", MigrationFileChangeKind.UPDATED),),
        "https://github.com/owner/comic/pull/4",
        up_to_date=True,
    )

    assert "https://github.com/owner/comic/pull/4" in page
    assert "The migration pull request is up to date." in page
    assert "Update migration pull request" not in page
    assert "Create migration pull request" not in page


@pytest.mark.parametrize("branch_state", ["missing", "orphan", "closed_pr"])
@pytest.mark.parametrize("cleanup_state", ["deleted", "shared", "failed"])
def test_cms_setup_replaces_old_target_revision_pr_after_new_pr_exists(
    monkeypatch, branch_state: str, cleanup_state: str
) -> None:
    old_base = "a" * 40
    new_base = "b" * 40
    old_branch = migration_branch_name("cms", old_base)
    new_branch = migration_branch_name("cms", new_base)
    selected_branch = f"{new_branch}-1" if branch_state == "closed_pr" else new_branch

    async def read_target(*_args: object) -> TargetRepositoryState:
        return TargetRepositoryState(
            base_commit_sha=new_base,
            target_branch="cms",
            tree=GitHubGitTree(sha="c" * 40, tree=()),
            config_path="your_content/comic_info.toml",
            engine_selector="cms",
        )

    async def resolve_engine(*_args: object) -> str:
        return "d" * 40

    async def build_preview(*_args: object) -> CmsMigrationPreview:
        return CmsMigrationPreview(
            base_commit_sha=new_base,
            target_branch="cms",
            engine_selector="cms",
            engine_commit_sha="d" * 40,
            changed_paths=("your_content/comic_info.toml",),
            files=(MigrationPlanFile("your_content/comic_info.toml", "[cms]\n"),),
        )

    class ReplacementClient(_FakeGitHubClient):
        def __init__(self) -> None:
            super().__init__()
            self.events: list[str] = []
            self.old_closed = False

        async def list_open_repository_pull_requests(self, _token, repository, base):
            assert base == "cms"
            return (
                ()
                if self.old_closed
                else (
                    GitHubPullRequestDetails(
                        number=4,
                        html_url="https://github.com/owner/comic/pull/4",
                        state="open",
                        head=GitHubPullRequestRef(ref=old_branch, sha="e" * 40, repo=repository),
                        base=GitHubPullRequestRef(ref="cms", sha=old_base, repo=repository),
                    ),
                )
            )

        async def find_repository_ref(self, _token, _repository, ref):
            assert ref in {f"heads/{new_branch}", f"heads/{selected_branch}"}
            return (
                GitHubGitReference(
                    ref=f"refs/{ref}", object=GitHubGitObject(type="commit", sha="8" * 40)
                )
                if ref == f"heads/{new_branch}" and branch_state != "missing"
                else None
            )

        async def list_repository_pull_requests_for_head(self, _token, _repository, branch):
            if branch == old_branch:
                assert self.old_closed
                return (
                    GitHubPullRequest(
                        number=7,
                        html_url="https://github.com/owner/comic/pull/7",
                        state="open",
                    ),
                ) if cleanup_state == "shared" else ()
            assert branch == new_branch
            return (
                GitHubPullRequest(
                    number=6,
                    html_url="https://github.com/owner/comic/pull/6",
                    state="closed",
                ),
            ) if branch_state == "closed_pr" else ()

        async def get_repository_ref(self, _token, _repository, ref):
            assert ref == f"heads/{new_branch}"
            return GitHubGitReference(
                ref=f"refs/{ref}", object=GitHubGitObject(type="commit", sha="8" * 40)
            )

        async def get_repository_commit(self, _token, _repository, sha):
            assert sha == "8" * 40
            return GitHubGitCommit(sha=sha, tree=GitHubGitSha(sha="1" * 40))

        async def get_repository_tree(self, _token, _repository, sha):
            assert sha == "1" * 40
            return GitHubGitTree(sha=sha, tree=())

        async def create_repository_blobs(self, _token, _repository, changes):
            return ((changes[0], "f" * 40),)

        async def create_repository_tree(self, _token, _repository, base_tree_sha, _blobs):
            assert base_tree_sha == "c" * 40
            return GitHubGitTree(sha="1" * 40, tree=())

        async def create_repository_commit(self, _token, _repository, _message, tree, parent):
            assert tree == "1" * 40
            assert parent == new_base
            return GitHubGitCommit(sha="2" * 40, tree=GitHubGitSha(sha=tree))

        async def create_repository_branch(self, _token, _repository, name, _sha):
            assert name == selected_branch
            self.events.append("branch_created")

        async def create_repository_pull_request(self, _token, _repository, *args):
            assert args[2] == selected_branch
            self.events.append("pr_created")
            return GitHubPullRequest(
                number=5, html_url="https://github.com/owner/comic/pull/5", state="open"
            )

        async def close_repository_pull_request(self, _token, _repository, number):
            assert number == 4
            self.events.append("old_pr_closed")
            self.old_closed = True

        async def delete_repository_branch(self, _token, _repository, branch):
            assert branch == old_branch
            if cleanup_state == "failed":
                self.events.append("old_branch_delete_failed")
                raise GitHubClientError
            self.events.append("old_branch_deleted")

    fake = ReplacementClient()
    monkeypatch.setattr("github_oauth_worker.app.read_target_repository_state", read_target)
    monkeypatch.setattr("github_oauth_worker.app.resolve_official_engine_commit", resolve_engine)
    monkeypatch.setattr("github_oauth_worker.app.build_migration_preview", build_preview)
    app = create_app(_ready_settings(), binding_store=InMemoryBindingStore(), github_client=fake)
    reviewed = app.state.state_manager.issue_cms_enablement_branch(123, 456, "cms")
    review = TestClient(app).get(
        f"/callback?code=one-time-code&state={reviewed.token}",
        headers={"Cookie": f"{reviewed.cookie_name}={reviewed.correlation_nonce}"},
    )
    assert review.status_code == 200
    assert "https://github.com/owner/comic/pull/4" in review.text
    assert "will be closed" in review.text
    assert fake.events == []

    confirmation = app.state.state_manager.issue_cms_enablement_confirmation(
        123,
        456,
        "cms",
        new_base,
        "cms",
        "d" * 40,
        _migration_plan_digest(asyncio.run(build_preview())),
    )
    result = TestClient(app).get(
        f"/callback?code=one-time-code&state={confirmation.token}",
        headers={"Cookie": f"{confirmation.cookie_name}={confirmation.correlation_nonce}"},
    )
    assert result.status_code == 200
    assert "https://github.com/owner/comic/pull/5" in result.text
    expected_events = [] if branch_state == "orphan" else ["branch_created"]
    expected_events.extend(["pr_created", "old_pr_closed"])
    if cleanup_state != "shared":
        expected_events.append(
            "old_branch_deleted" if cleanup_state == "deleted" else "old_branch_delete_failed"
        )
    assert fake.events == expected_events
    assert ("could not remove" in result.text) is (cleanup_state == "failed")
    if cleanup_state == "failed":
        assert old_branch in result.text


def test_cms_branch_callback_returns_a_safe_page_for_a_migration_error(monkeypatch) -> None:
    async def fail_target_state(*_args: object) -> TargetRepositoryState:
        raise CmsEnablementError("target_content_invalid")

    monkeypatch.setattr("github_oauth_worker.app.read_target_repository_state", fail_target_state)
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app)
    issued_state = app.state.state_manager.issue_cms_enablement_branch(123, 456, "cms")

    response = client.get(
        f"/callback?code=one-time-code&state={issued_state.token}",
        headers={"Cookie": f"{issued_state.cookie_name}={issued_state.correlation_nonce}"},
    )

    assert response.status_code == 400
    assert "comic_git CMS setup could not continue" in response.text
    assert "target_content_invalid" in response.text
    assert "Internal Server Error" not in response.text


def test_cms_branch_callback_rejects_an_old_engine_before_loading_source(monkeypatch) -> None:
    async def read_old_engine(*_args: object) -> TargetRepositoryState:
        return TargetRepositoryState(
            base_commit_sha="a" * 40,
            target_branch="cms",
            tree=GitHubGitTree(sha="b" * 40, tree=()),
            config_path="your_content/comic_info.ini",
            engine_selector="1.1",
        )

    async def unexpected_source_resolution(*_args: object) -> str:
        pytest.fail("Unsupported engine source must not be loaded")

    monkeypatch.setattr("github_oauth_worker.app.read_target_repository_state", read_old_engine)
    monkeypatch.setattr(
        "github_oauth_worker.app.resolve_official_engine_commit", unexpected_source_resolution
    )
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    issued_state = app.state.state_manager.issue_cms_enablement_branch(123, 456, "cms")

    response = TestClient(app).get(
        f"/callback?code=one-time-code&state={issued_state.token}",
        headers={"Cookie": f"{issued_state.cookie_name}={issued_state.correlation_nonce}"},
    )

    assert response.status_code == 400
    assert "engine_version_too_old" in response.text
    assert "1.2 or newer" in response.text
    assert "1.1" in response.text


@pytest.mark.parametrize("binding_state", ("absent", "stale", "recovery_required"))
def test_cms_branch_callback_reports_an_already_enabled_site_without_a_pr(
    monkeypatch, binding_state: str
) -> None:
    async def read_target(*_args: object) -> TargetRepositoryState:
        return TargetRepositoryState(
            base_commit_sha="a" * 40,
            target_branch="cms",
            tree=GitHubGitTree(sha="b" * 40, tree=()),
            config_path="your_content/comic_info.toml",
            engine_selector="cms",
        )

    async def resolve_engine(*_args: object) -> str:
        return "c" * 40

    async def build_preview(*_args: object) -> CmsMigrationPreview:
        return CmsMigrationPreview(
            base_commit_sha="a" * 40,
            target_branch="cms",
            engine_selector="cms",
            engine_commit_sha="c" * 40,
            changed_paths=(),
            files=(),
            already_enabled=True,
        )

    class NoPullRequestClient(_FakeGitHubClient):
        async def list_open_repository_pull_requests(self, *_args):
            pytest.fail("Already-enabled setup must not inspect or create migration PRs")

    monkeypatch.setattr("github_oauth_worker.app.read_target_repository_state", read_target)
    monkeypatch.setattr("github_oauth_worker.app.resolve_official_engine_commit", resolve_engine)
    monkeypatch.setattr("github_oauth_worker.app.build_migration_preview", build_preview)
    store = InMemoryBindingStore()
    if binding_state != "absent":
        asyncio.run(
            store.create_binding(
                RepositoryBinding.create(
                    repository_id=123,
                    repository_owner="old-owner",
                    repository_name="old-comic",
                    installation_id=789,
                    origin="https://cms.example.com",
                )
            )
        )
        if binding_state == "recovery_required":
            asyncio.run(store.mark_recovery_required(123))
    app = create_app(
        _ready_settings(),
        binding_store=store,
        github_client=NoPullRequestClient(),
    )
    issued_state = app.state.state_manager.issue_cms_enablement_branch(123, 456, "cms")

    response = TestClient(app).get(
        f"/callback?code=one-time-code&state={issued_state.token}",
        headers={"Cookie": f"{issued_state.cookie_name}={issued_state.correlation_nonce}"},
    )

    assert response.status_code == 200
    assert "comic_git CMS is already enabled" in response.text
    assert "owner/comic" in response.text
    assert "No migration pull request is needed" in response.text
    assert "Create migration pull request" not in response.text
    binding = awaitable_result(store.get_binding(123))
    if binding_state == "absent":
        assert binding is None
        assert "CMS connection was refreshed" not in response.text
    else:
        assert binding.status is BindingStatus.ACTIVE
        assert binding.installation_id == 456
        assert binding.repository_owner == "owner"
        assert binding.origins[0].origin == "https://cms.example.com"
        assert "CMS connection was refreshed" in response.text


def test_cms_branch_callback_logs_safe_migration_diagnostics(monkeypatch) -> None:
    async def fail_target_state(*_args: object) -> TargetRepositoryState:
        raise CmsEnablementError(
            "migration_runner_failed",
            safe_log_fields={"runner_failure_kind": "nonzero_exit", "runner_returncode": 1},
        )

    logged_fields: dict[str, object] = {}

    def capture_log_event(*args: object, **fields: object) -> None:
        if len(args) > 1 and args[1] == "oauth_callback_failed":
            logged_fields["event"] = args[1]
            logged_fields.update(fields)

    monkeypatch.setattr("github_oauth_worker.app.read_target_repository_state", fail_target_state)
    monkeypatch.setattr("github_oauth_worker.app.log_event", capture_log_event)
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app)
    issued_state = app.state.state_manager.issue_cms_enablement_branch(123, 456, "cms")

    response = client.get(
        f"/callback?code=one-time-code&state={issued_state.token}",
        headers={"Cookie": f"{issued_state.cookie_name}={issued_state.correlation_nonce}"},
    )

    assert response.status_code == 400
    assert logged_fields["event"] == "oauth_callback_failed"
    assert logged_fields["runner_failure_kind"] == "nonzero_exit"
    assert logged_fields["runner_returncode"] == 1


def test_cms_branch_callback_hides_an_unexpected_error_without_a_traceback(monkeypatch) -> None:
    async def fail_target_state(*_args: object) -> TargetRepositoryState:
        raise RuntimeError("sensitive unexpected detail")

    monkeypatch.setattr("github_oauth_worker.app.read_target_repository_state", fail_target_state)
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app)
    issued_state = app.state.state_manager.issue_cms_enablement_branch(123, 456, "cms")

    response = client.get(
        f"/callback?code=one-time-code&state={issued_state.token}",
        headers={"Cookie": f"{issued_state.cookie_name}={issued_state.correlation_nonce}"},
    )

    assert response.status_code == 500
    assert "comic_git CMS setup could not continue" in response.text
    assert "unexpected_callback_failure" in response.text
    assert "sensitive unexpected detail" not in response.text


def test_cms_setup_callback_renders_a_safe_error_without_a_decap_origin() -> None:
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=_FakeGitHubClient(),
    )
    client = TestClient(app)
    issued = app.state.state_manager.issue_cms_enablement_confirmation(
        123, 456, "master", "a" * 40, "cms", "b" * 40, "0" * 64
    )

    response = client.get(
        f"/callback?state={issued.token}",
        headers={"Cookie": f"{issued.cookie_name}={issued.correlation_nonce}"},
    )

    assert response.status_code == 400
    assert "The request could not be completed." in response.text


@pytest.mark.parametrize(
    "branch_changed,interrupt_after_branch,edited_body,plan_changed,race",
    [
        (False, False, False, False, False),
        (True, False, False, False, False),
        (False, True, False, False, False),
        (False, False, True, False, False),
        (False, False, False, True, False),
        (False, False, False, False, True),
    ],
)
def test_cms_setup_revises_an_untouched_existing_pull_request(
    monkeypatch,
    branch_changed: bool,
    interrupt_after_branch: bool,
    edited_body: bool,
    plan_changed: bool,
    race: bool,
) -> None:
    base_sha = "a" * 40
    old_head = "c" * 40
    new_head = "3" * 40
    new_engine = "e" * 40
    branch_name = migration_branch_name("cms", base_sha)

    async def read_target(*_args: object) -> TargetRepositoryState:
        return TargetRepositoryState(
            base_commit_sha=base_sha,
            target_branch="cms",
            tree=GitHubGitTree(sha="b" * 40, tree=()),
            config_path="your_content/comic_info.toml",
            engine_selector="cms",
        )

    async def resolve_engine(*_args: object) -> str:
        return new_engine

    async def build_preview(*_args: object) -> CmsMigrationPreview:
        from github_oauth_worker.cms_enablement import MigrationPlanFile

        return CmsMigrationPreview(
            base_commit_sha=base_sha,
            target_branch="cms",
            engine_selector="cms",
            engine_commit_sha=new_engine,
            changed_paths=("your_content/comic_info.toml",),
            files=(MigrationPlanFile("your_content/comic_info.toml", "title = 'New'\n"),),
        )

    class RevisionClient(_FakeGitHubClient):
        def __init__(self) -> None:
            super().__init__()
            self.head_sha = "4" * 40 if branch_changed else old_head
            self.body = (
                "Engine selector: `cms`\n\n"
                f"Resolved engine SHA: `{'d' * 40}`\n\n"
                "Target branch: `cms`"
            )
            if edited_body:
                self.body += "\n\nManual note"
            self.updated_body = ""
            self.fail_body_once = interrupt_after_branch
            self.race_once = race
            self.created_pull_requests = 0

        async def list_open_repository_pull_requests(self, token, repository, base):
            assert base == "cms"
            return (await self.get_repository_pull_request(token, repository, 4),)

        async def get_repository_pull_request(self, _token, repository, number):
            assert number == 4
            head = GitHubPullRequestRef(ref=branch_name, sha=self.head_sha, repo=repository)
            base = GitHubPullRequestRef(ref="cms", sha=base_sha, repo=repository)
            return GitHubPullRequestDetails(
                number=4,
                html_url="https://github.com/owner/comic/pull/4",
                state="open",
                head=head,
                base=base,
                body=self.body,
            )

        async def get_repository_ref(self, _token, _repository, ref):
            assert ref == f"heads/{branch_name}"
            return GitHubGitReference(
                ref=f"refs/{ref}", object=GitHubGitObject(type="commit", sha=self.head_sha)
            )

        async def get_repository_commit(self, _token, _repository, sha):
            if sha == new_head:
                return GitHubGitCommit(
                    sha=sha,
                    tree=GitHubGitSha(sha="2" * 40),
                    message="Refresh comic_git CMS migration",
                    parents=(GitHubGitSha(sha=old_head),),
                )
            assert sha in {old_head, "4" * 40, "6" * 40}
            return GitHubGitCommit(
                sha=sha,
                tree=GitHubGitSha(sha="f" * 40),
                message="Enable comic_git CMS",
                parents=(GitHubGitSha(sha=base_sha),),
            )

        async def get_repository_tree(self, _token, _repository, sha):
            assert sha in {"f" * 40, "2" * 40}
            current = sha == "2" * 40
            return GitHubGitTree(
                sha=sha,
                tree=(
                    GitHubGitTreeEntry(
                        path="your_content/comic_info.toml",
                        mode="100644",
                        type="blob",
                        sha=("5" if current else "1") * 40,
                        size=len("title = 'New'\n") if current else 4,
                    ),
                ),
            )

        async def get_repository_blob(self, _token, _repository, sha):
            contents = "title = 'New'\n" if sha == "5" * 40 else "old\n"
            return GitHubGitBlob(
                sha=sha,
                content=base64.b64encode(contents.encode()).decode(),
                encoding="base64",
            )

        async def create_repository_blobs(self, _token, _repository, changes):
            assert changes[0].content == "title = 'New'\n"
            return ((changes[0], "1" * 40),)

        async def create_repository_tree(self, _token, _repository, base_tree_sha, _blobs):
            assert base_tree_sha == "b" * 40
            return GitHubGitTree(sha="2" * 40, tree=())

        async def create_repository_commit(
            self, _token, _repository, _message, tree_sha, parent_sha
        ):
            assert tree_sha == "2" * 40
            assert parent_sha == self.head_sha
            return GitHubGitCommit(sha=new_head, tree=GitHubGitSha(sha=tree_sha))

        async def update_repository_branch(self, _token, _repository, branch, commit_sha):
            assert branch == branch_name
            assert commit_sha == new_head
            if self.race_once:
                self.race_once = False
                self.head_sha = "6" * 40
                raise GitHubClientError
            self.head_sha = commit_sha

        async def update_repository_pull_request_body(self, _token, repository, number, body):
            assert number == 4
            if self.fail_body_once:
                self.fail_body_once = False
                raise GitHubClientError
            self.updated_body = body
            self.body = body
            return await self.get_repository_pull_request(_token, repository, number)

        async def create_repository_pull_request(self, *_args):
            self.created_pull_requests += 1
            raise AssertionError("Must revise the existing pull request")

    fake = RevisionClient()
    monkeypatch.setattr("github_oauth_worker.app.read_target_repository_state", read_target)
    monkeypatch.setattr("github_oauth_worker.app.resolve_official_engine_commit", resolve_engine)
    monkeypatch.setattr("github_oauth_worker.app.build_migration_preview", build_preview)
    app = create_app(
        _ready_settings(),
        binding_store=InMemoryBindingStore(),
        github_client=fake,
    )
    reviewed_digest = _migration_plan_digest(
        CmsMigrationPreview(
            base_commit_sha=base_sha,
            target_branch="cms",
            engine_selector="cms",
            engine_commit_sha=new_engine,
            changed_paths=("your_content/comic_info.toml",),
            files=(MigrationPlanFile("your_content/comic_info.toml", "title = 'New'\n"),),
        )
    )
    issued = app.state.state_manager.issue_cms_enablement_confirmation(
        123,
        456,
        "cms",
        base_sha,
        "cms",
        new_engine,
        "0" * 64 if plan_changed else reviewed_digest,
    )
    response = TestClient(app).get(
        f"/callback?code=one-time-code&state={issued.token}",
        headers={"Cookie": f"{issued.cookie_name}={issued.correlation_nonce}"},
    )

    if plan_changed:
        assert response.status_code == 409
        assert "cms_migration_review_changed" in response.text
        assert fake.updated_body == ""
        return
    if race:
        assert response.status_code == 409
        assert "cms_migration_retry" in response.text
        assert "/setup?installation_id=456" in response.text
        issued = app.state.state_manager.issue_cms_enablement_confirmation(
            123, 456, "cms", base_sha, "cms", new_engine, reviewed_digest
        )
        response = TestClient(app).get(
            f"/callback?code=one-time-code&state={issued.token}",
            headers={"Cookie": f"{issued.cookie_name}={issued.correlation_nonce}"},
        )
    if interrupt_after_branch:
        assert response.status_code == 409
        assert "cms_migration_retry" in response.text
        issued = app.state.state_manager.issue_cms_enablement_confirmation(
            123, 456, "cms", base_sha, "cms", new_engine, reviewed_digest
        )
        response = TestClient(app).get(
            f"/callback?code=one-time-code&state={issued.token}",
            headers={"Cookie": f"{issued.cookie_name}={issued.correlation_nonce}"},
        )
    assert response.status_code == 200
    assert fake.head_sha == new_head
    assert new_engine in fake.updated_body
    assert fake.created_pull_requests == 0
    if edited_body:
        assert "Manual note" in fake.updated_body


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
        data={
            "origin": "https://old-cms.example.com",
            "target_origin": "https://new-cms.example.com",
        },
        follow_redirects=False,
    )
    assert start.status_code == 302
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
        data={"origin": "https://new-cms.example.com"},
    )
    assert complete.status_code == 200
    assert awaitable_result(store.get_binding_for_origin("https://new-cms.example.com")) is not None


def test_failed_editor_verification_does_not_disable_the_shared_binding() -> None:
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
        data={"origin": "https://cms.example.com"},
        follow_redirects=False,
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    cookie = start.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    response = client.get(
        f"/callback?code=one-time-code&state={state}",
        headers={"Cookie": f"oauth_correlation_decap={cookie}"},
    )

    assert response.status_code == 400
    assert awaitable_result(store.get_binding(123)).status is BindingStatus.ACTIVE
    assert client.post(
        "/auth/handshake",
        data={"origin": "https://cms.example.com"},
        follow_redirects=False,
    ).status_code == 302


def test_failed_editor_refresh_does_not_disable_the_shared_binding() -> None:
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

    response = client.post(
        "/auth/refresh?provider=github",
        content="refresh_token=refresh-token",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://cms.example.com",
        },
    )

    assert response.status_code == 400
    assert awaitable_result(store.get_binding(123)).status is BindingStatus.ACTIVE


def test_failed_origin_migration_check_does_not_disable_the_shared_binding() -> None:
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
        "/origins/migrate/handshake",
        data={
            "origin": "https://cms.example.com",
            "target_origin": "https://new-cms.example.com",
        },
        follow_redirects=False,
    )
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    cookie = start.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

    response = client.get(
        f"/callback?code=one-time-code&state={state}",
        headers={"Cookie": f"oauth_correlation_origin_migration={cookie}"},
    )

    assert response.status_code == 400
    assert awaitable_result(store.get_binding(123)).status is BindingStatus.ACTIVE


def _ready_settings() -> WorkerSettings:
    return WorkerSettings(
        service_mode=ServiceMode.READY,
        gcp_project_id="example-project",
        public_base_url="https://worker.example.com",
        github_app_client_id="client-id",
        github_app_slug="comic-git-test",
        github_app_client_secret="client-secret",
        state_signing_secret="state-secret",
        access_policy=AccessPolicyMode.PUBLIC,
        cms_allowed_engine_branches=frozenset({"cms", "latest", "master"}),
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

    async def list_open_repository_pull_requests(self, _token, _repository, _base):
        return ()

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
            default_branch="main",
        )

    async def require_repository_administrator(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
        login: str,
    ) -> None:
        assert user_access_token.get_secret_value() == "access-token"
        assert repository.id == 123
        assert login == "marco"

    async def list_repository_branches(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
    ) -> tuple[GitHubRepositoryBranch, ...]:
        assert user_access_token.get_secret_value() == "access-token"
        assert repository.id == 123
        return (GitHubRepositoryBranch(name="cms"), GitHubRepositoryBranch(name="main"))


class _FakeInstallation:
    """Minimal installation double for the enrollment repository-selection response."""

    def __init__(self, id: int) -> None:
        self.id = id


def _access_setup_case(
    monkeypatch,
    *,
    target_branch: str = "cms",
    already_enabled: bool = False,
    policy_file_collision: bool = False,
):
    base_sha = "a" * 40

    async def read_target(_client, _token, _repository, branch):
        assert branch == target_branch
        return TargetRepositoryState(
            base_commit_sha=base_sha,
            target_branch=branch,
            tree=GitHubGitTree(sha="b" * 40, tree=()),
            config_path="your_content/comic_info.toml",
            engine_selector="cms",
        )

    async def resolve_engine(*_args):
        return "c" * 40

    async def build_preview(*_args):
        return CmsMigrationPreview(
            base_commit_sha=base_sha,
            target_branch=target_branch,
            engine_selector="cms",
            engine_commit_sha="c" * 40,
            changed_paths=(
                ()
                if already_enabled
                else ("your_content/comic_info.toml",)
                + ((".comic_git/cms_access.toml",) if policy_file_collision else ())
            ),
            files=(
                ()
                if already_enabled
                else (MigrationPlanFile("your_content/comic_info.toml", "cms = true\n"),)
                + (
                    (MigrationPlanFile(".comic_git/cms_access.toml", "unexpected = true\n"),)
                    if policy_file_collision
                    else ()
                )
            ),
            already_enabled=already_enabled,
        )

    class AccessPolicyClient(_FakeGitHubClient):
        def __init__(self):
            super().__init__()
            self.default_sha = base_sha

        async def get_default_branch_cms_access_policy(self, _token, _repository):
            return None

        async def get_repository_ref(self, _token, _repository, ref):
            assert ref == "heads/main"
            return GitHubGitReference(
                ref="refs/heads/main",
                object=GitHubGitObject(type="commit", sha=self.default_sha),
            )

    monkeypatch.setattr("github_oauth_worker.app.read_target_repository_state", read_target)
    monkeypatch.setattr("github_oauth_worker.app.resolve_official_engine_commit", resolve_engine)
    monkeypatch.setattr("github_oauth_worker.app.build_migration_preview", build_preview)
    github_client = AccessPolicyClient()
    settings = _ready_settings().model_copy(update={"cms_access_policy_setup_enabled": True})
    app = create_app(
        settings,
        binding_store=InMemoryBindingStore(),
        github_client=github_client,
    )
    client = TestClient(app, base_url="https://worker.example.com")
    state = app.state.state_manager.issue_cms_enablement_branch(123, 456, target_branch)
    origins_page = client.get(
        f"/callback?code=one-time-code&state={state.token}",
        headers={"Cookie": f"{state.cookie_name}={state.correlation_nonce}"},
    )
    assert origins_page.status_code == 200
    assert "Approve CMS websites" in origins_page.text
    origin_state = re.search('name="state" value="([^"]+)"', origins_page.text)
    assert origin_state is not None
    review = client.post(
        "/setup/review-access",
        data={
            "state": origin_state.group(1),
            "approved_origins": "https://owner.github.io\nhttps://comic.example.com",
        },
        follow_redirects=False,
    )
    return app, client, github_client, review


def test_access_policy_review_is_signed_and_requires_a_separate_pr(monkeypatch) -> None:
    app, client, _, review = _access_setup_case(monkeypatch)

    assert review.status_code == 200
    assert "A separate access-policy pull request" in review.text
    assert "https://comic.example.com" in review.text
    assert "Prepare CMS pull requests" in review.text
    confirmation_state = re.search('name="state" value="([^"]+)"', review.text)
    assert confirmation_state is not None
    verified = app.state.state_manager.consume_from_cookies(
        confirmation_state.group(1), client.cookies
    )
    assert verified.approved_origins == (
        "https://owner.github.io",
        "https://comic.example.com",
    )
    assert verified.default_branch_commit_sha == "a" * 40
    assert verified.reviewed_policy_digest is not None


def test_default_branch_setup_combines_policy_and_migration_files(monkeypatch) -> None:
    _, _, _, review = _access_setup_case(monkeypatch, target_branch="main")

    assert review.status_code == 200
    assert ".comic_git/cms_access.toml" in review.text
    assert "The access policy will be included in this pull request" in review.text
    assert "A separate access-policy pull request" not in review.text


def test_default_branch_setup_rejects_engine_policy_file_collision(monkeypatch) -> None:
    _, _, _, review = _access_setup_case(
        monkeypatch, target_branch="main", policy_file_collision=True
    )

    assert review.status_code == 400
    assert "cms_access_plan_invalid" in review.text


def test_access_policy_confirmation_rejects_a_changed_default_branch(monkeypatch) -> None:
    _, client, github_client, review = _access_setup_case(monkeypatch)
    confirmation_state = re.search('name="state" value="([^"]+)"', review.text)
    assert confirmation_state is not None
    github_client.default_sha = "f" * 40

    result = client.post(
        "/setup/confirm", data={"state": confirmation_state.group(1)}, follow_redirects=False
    )

    assert result.status_code == 409
    assert "changed" in result.text


def test_access_review_reports_invalid_origin_in_a_browser_page(monkeypatch) -> None:
    app, client, _, _ = _access_setup_case(monkeypatch)
    nonce = client.cookies["oauth_correlation_cms_enablement_branch"]
    state = app.state.state_manager.issue_cms_enablement_branch(
        123, 456, "cms", correlation_nonce=nonce
    )

    result = client.post(
        "/setup/review-access",
        data={"state": state.token, "approved_origins": "http://comic.example.com"},
        follow_redirects=False,
    )

    assert result.status_code == 400
    assert "cms_access_plan_invalid" in result.text
    assert 'href="/setup"' in result.text


def test_already_enabled_site_gets_policy_only_review_and_result(monkeypatch) -> None:
    _, client, _, review = _access_setup_case(monkeypatch, already_enabled=True)
    assert review.status_code == 200
    assert "Only website access approval needs a pull request" in review.text
    assert "Create access-policy pull request" in review.text
    delivery = AsyncMock(
        return_value=CmsAccessDeliveryResult("https://github.com/owner/comic/pull/12")
    )
    monkeypatch.setattr("github_oauth_worker.app.deliver_cms_access_policy", delivery)
    confirmation_state = re.search('name="state" value="([^"]+)"', review.text)
    assert confirmation_state is not None

    result = client.post(
        "/setup/confirm", data={"state": confirmation_state.group(1)}, follow_redirects=False
    )

    assert result.status_code == 200
    assert "https://github.com/owner/comic/pull/12" in result.text
    assert "Review migration pull request" not in result.text
    delivery.assert_awaited_once()


@pytest.mark.parametrize("target_branch", ["cms", "main"])
def test_access_policy_confirmation_creates_the_expected_prs(monkeypatch, target_branch) -> None:
    _, client, github_client, review = _access_setup_case(
        monkeypatch, target_branch=target_branch
    )
    delivery = AsyncMock(
        return_value=CmsAccessDeliveryResult("https://github.com/owner/comic/pull/12")
    )
    monkeypatch.setattr("github_oauth_worker.app.deliver_cms_access_policy", delivery)
    github_client.find_repository_ref = AsyncMock(return_value=None)
    github_client.create_repository_blobs = AsyncMock(return_value=())
    github_client.create_repository_tree = AsyncMock(
        return_value=GitHubGitTree(sha="e" * 40, tree=())
    )
    github_client.create_repository_commit = AsyncMock(
        return_value=GitHubGitCommit(
            sha="f" * 40, tree=GitHubGitSha(sha="e" * 40)
        )
    )
    github_client.create_repository_branch = AsyncMock()
    github_client.create_repository_pull_request = AsyncMock(
        return_value=GitHubPullRequest(
            number=13,
            html_url="https://github.com/owner/comic/pull/13",
            state="open",
        )
    )
    confirmation_state = re.search('name="state" value="([^"]+)"', review.text)
    assert confirmation_state is not None

    result = client.post(
        "/setup/confirm", data={"state": confirmation_state.group(1)}, follow_redirects=False
    )

    assert result.status_code == 200
    assert "https://github.com/owner/comic/pull/13" in result.text
    paths = {
        change.path for change in github_client.create_repository_blobs.await_args.args[2]
    }
    if target_branch == "cms":
        delivery.assert_awaited_once()
        assert "https://github.com/owner/comic/pull/12" in result.text
        assert paths == {"your_content/comic_info.toml"}
    else:
        delivery.assert_not_awaited()
        assert "https://github.com/owner/comic/pull/12" not in result.text
        assert paths == {
            "your_content/comic_info.toml",
            ".comic_git/cms_access.toml",
        }
