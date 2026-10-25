"""The browser origin and live default-branch policy gate every Decap token."""

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from pydantic import SecretStr

from github_oauth_worker.app import create_app
from github_oauth_worker.config import AccessPolicyMode, ServiceMode, WorkerSettings
from github_oauth_worker.github_client import (
    GitHubAccessVerificationError,
    GitHubRepository,
    GitHubRepositoryOwner,
    GitHubUser,
    GitHubUserAccessToken,
)


def _settings(**overrides: object) -> WorkerSettings:
    values = {
        "service_mode": ServiceMode.READY,
        "gcp_project_id": "example-project",
        "public_base_url": "https://worker.example.com",
        "github_app_client_id": "client-id",
        "github_app_client_secret": "client-secret",
        "state_signing_secret": "state-secret",
        "access_policy": AccessPolicyMode.PUBLIC,
    }
    values.update(overrides)
    return WorkerSettings(**values)


def _policy(repository_id: int, *origins: str) -> bytes:
    origins_text = ", ".join(f'"{origin}"' for origin in origins)
    return (
        f'schema_version = 1\nrepository_id = {repository_id}\ncms_branch = "cms"\n'
        'worker_origin = "https://worker.example.com"\n'
        f"authorized_origins = [{origins_text}]\n"
    ).encode()


class FakeGitHub:
    def __init__(self) -> None:
        self.policies = {123: _policy(123, "https://shared.example.com")}
        self.allowed_ids = {123}
        self.login = "editor"
        self.policy_reads = 0

    async def exchange_authorization_code(self, code: str, repository_id: int):
        assert code == "code"
        assert repository_id > 0
        return self._token()

    async def refresh_user_access_token(self, refresh_token: SecretStr):
        assert refresh_token.get_secret_value() == "refresh-token"
        return self._token()

    async def get_authenticated_user(self, _token: SecretStr):
        return GitHubUser(login=self.login)

    async def verify_repository_access(self, _token: SecretStr, repository_id: int):
        if repository_id not in self.allowed_ids:
            raise GitHubAccessVerificationError
        return GitHubRepository(
            id=repository_id,
            name=f"comic-{repository_id}",
            owner=GitHubRepositoryOwner(login="owner"),
            default_branch="main",
        )

    async def get_default_branch_cms_access_policy(self, _token: SecretStr, repository):
        self.policy_reads += 1
        return self.policies.get(repository.id)

    @staticmethod
    def _token():
        return GitHubUserAccessToken(
            access_token=SecretStr("access-token"),
            token_type="bearer",
            expires_in=3600,
            refresh_token=SecretStr("refresh-token"),
        )


def _login(client: TestClient, repository_id: int, origin: str):
    start = client.post(
        f"/auth/{repository_id}/handshake",
        data={"origin": origin},
        follow_redirects=False,
    )
    assert start.status_code == 302
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    return client.get(f"/callback?code=code&state={state}")


def test_login_uses_repository_id_and_actual_opener_origin() -> None:
    github = FakeGitHub()
    client = TestClient(
        create_app(_settings(), github_client=github), base_url="https://worker.example.com"
    )

    result = _login(client, 123, "https://shared.example.com")

    assert result.status_code == 200
    assert 'const targetOrigin = "https://shared.example.com"' in result.text
    assert "authorization:github:success:" in result.text
    assert github.policy_reads == 1


def test_login_denies_missing_policy_wrong_origin_or_wrong_repository() -> None:
    github = FakeGitHub()
    github.policies[124] = _policy(124, "https://shared.example.com")
    github.allowed_ids.add(124)
    client = TestClient(
        create_app(_settings(), github_client=github), base_url="https://worker.example.com"
    )

    assert "authorization:github:error:" in _login(client, 123, "https://wrong.example.com").text
    assert _login(client, 124, "https://shared.example.com").status_code == 200
    github.policies[124] = _policy(123, "https://shared.example.com")
    assert "authorization:github:error:" in _login(client, 124, "https://shared.example.com").text
    github.policies.pop(123)
    assert "authorization:github:error:" in _login(client, 123, "https://shared.example.com").text


def test_refresh_rechecks_policy_user_and_github_access() -> None:
    github = FakeGitHub()
    client = TestClient(
        create_app(_settings(), github_client=github), base_url="https://worker.example.com"
    )
    endpoint = "/auth/123/refresh?provider=github"
    headers = {"Origin": "https://shared.example.com"}

    preflight = client.options(endpoint, headers=headers)
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == headers["Origin"]
    success = client.post(endpoint, data={"refresh_token": "refresh-token"}, headers=headers)
    assert success.status_code == 200
    assert success.json()["access_token"] == "access-token"
    github.policies.clear()
    denied = client.post(endpoint, data={"refresh_token": "refresh-token"}, headers=headers)
    assert denied.status_code == 403
    assert denied.headers["access-control-allow-origin"] == headers["Origin"]
    assert "access_token" not in denied.text


def test_shared_origin_can_select_two_independently_approved_repositories() -> None:
    github = FakeGitHub()
    github.allowed_ids.add(124)
    github.policies[124] = _policy(124, "https://shared.example.com")
    client = TestClient(
        create_app(_settings(), github_client=github), base_url="https://worker.example.com"
    )

    assert _login(client, 123, "https://shared.example.com").status_code == 200
    assert _login(client, 124, "https://shared.example.com").status_code == 200


def test_refresh_rejects_revoked_github_access_and_whitelist() -> None:
    github = FakeGitHub()
    github.allowed_ids.clear()
    client = TestClient(
        create_app(_settings(), github_client=github), base_url="https://worker.example.com"
    )
    response = client.post(
        "/auth/123/refresh?provider=github",
        data={"refresh_token": "refresh-token"},
        headers={"Origin": "https://shared.example.com"},
    )
    assert response.status_code == 400
    github.allowed_ids.add(123)
    restricted = TestClient(
        create_app(
            _settings(
                access_policy=AccessPolicyMode.GITHUB_LOGIN_WHITELIST,
                github_login_whitelist=frozenset({"someone-else"}),
            ),
            github_client=github,
        ),
        base_url="https://worker.example.com",
    )
    assert (
        "authorization:github:error:" in _login(restricted, 123, "https://shared.example.com").text
    )
