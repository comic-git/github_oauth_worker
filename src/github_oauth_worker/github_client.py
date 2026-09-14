"""Bounded GitHub App user-token and repository-verification client."""

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from github_oauth_worker.config import ConfigurationError, WorkerSettings
from github_oauth_worker.errors import WorkerError, WorkerUnavailableError


class GitHubClientError(WorkerUnavailableError):
    """Hide upstream transport and malformed-response details from browser clients."""

    public_message = "GitHub could not complete the requested operation."


class GitHubAccessVerificationError(WorkerError):
    """Reject a token that GitHub does not prove can access its bound repository."""

    public_message = "GitHub could not verify access to the configured repository."


class GitHubUser(BaseModel):
    """The minimal verified GitHub user identity needed for access-policy evaluation."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    login: str = Field(min_length=1)


class GitHubInstallation(BaseModel):
    """An App installation visible to a GitHub App user access token."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: int = Field(gt=0)


class GitHubRepositoryOwner(BaseModel):
    """The display owner returned with a repository metadata response."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    login: str = Field(min_length=1)


class GitHubRepository(BaseModel):
    """Repository metadata used to verify a selected App repository."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    id: int = Field(gt=0)
    name: str = Field(min_length=1)
    owner: GitHubRepositoryOwner


class GitHubUserAccessToken(BaseModel):
    """Short-lived GitHub App user credentials kept only in the active request flow."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    access_token: SecretStr
    token_type: str = Field(min_length=1)
    expires_in: int = Field(gt=0)
    refresh_token: SecretStr | None = None
    refresh_token_expires_in: int | None = Field(default=None, gt=0)


class _InstallationsPage(BaseModel):
    """The GitHub pagination envelope for user-visible App installations."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    installations: tuple[GitHubInstallation, ...]


class _RepositoriesPage(BaseModel):
    """The GitHub pagination envelope for repositories in one App installation."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    total_count: int = Field(ge=0)
    repositories: tuple[GitHubRepository, ...]


class GitHubAppClient:
    """Perform only the GitHub calls needed for authorization, enrollment, and refresh."""

    _API_BASE_URL = "https://api.github.com"
    _OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
    _API_VERSION = "2026-03-10"
    _REQUEST_TIMEOUT_SECONDS = 10.0
    _MAXIMUM_REPOSITORY_PAGES = 100

    def __init__(
        self,
        client_id: str,
        client_secret: SecretStr,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http_client = http_client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(self._REQUEST_TIMEOUT_SECONDS, connect=5.0),
        )
        self._owns_http_client = http_client is None

    @classmethod
    def from_settings(cls, settings: WorkerSettings) -> GitHubAppClient:
        """Build a client only from ready-mode App credentials."""
        if settings.github_app_client_id is None or settings.github_app_client_secret is None:
            raise ConfigurationError
        return cls(settings.github_app_client_id, settings.github_app_client_secret)

    async def aclose(self) -> None:
        """Close the internally owned HTTP pool during application shutdown."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def exchange_authorization_code(
        self,
        authorization_code: str,
        repository_id: int | None = None,
    ) -> GitHubUserAccessToken:
        """Exchange a one-time code while asking GitHub to restrict the token to one repository."""
        request_data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret.get_secret_value(),
            "code": authorization_code,
        }
        if repository_id is not None:
            request_data["repository_id"] = str(repository_id)
        response_data = await self._request_json(
            "POST",
            self._OAUTH_TOKEN_URL,
            data=request_data,
            headers={"Accept": "application/json"},
        )
        return self._validate_response(GitHubUserAccessToken, response_data)

    async def refresh_user_access_token(
        self,
        refresh_token: SecretStr,
    ) -> GitHubUserAccessToken:
        """Refresh an expiring token without storing the caller's refresh token."""
        response_data = await self._request_json(
            "POST",
            self._OAUTH_TOKEN_URL,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret.get_secret_value(),
                "grant_type": "refresh_token",
                "refresh_token": refresh_token.get_secret_value(),
            },
            headers={"Accept": "application/json"},
        )
        return self._validate_response(GitHubUserAccessToken, response_data)

    async def get_authenticated_user(self, user_access_token: SecretStr) -> GitHubUser:
        """Return the verified GitHub login associated with a user access token."""
        response_data = await self._get_api_json("/user", user_access_token)
        return self._validate_response(GitHubUser, response_data)

    async def list_accessible_installations(
        self,
        user_access_token: SecretStr,
    ) -> tuple[GitHubInstallation, ...]:
        """List App installations that the authenticated user can administer or access."""
        response_data = await self._get_api_json("/user/installations", user_access_token)
        return self._validate_response(_InstallationsPage, response_data).installations

    async def list_repositories_for_installation(
        self,
        user_access_token: SecretStr,
        installation_id: int,
    ) -> tuple[GitHubRepository, ...]:
        """List a bounded set of repositories available to a user in one App installation."""
        repositories: list[GitHubRepository] = []
        for page in range(1, self._MAXIMUM_REPOSITORY_PAGES + 1):
            response_data = await self._get_api_json(
                f"/user/installations/{installation_id}/repositories",
                user_access_token,
                params={"page": page, "per_page": 100},
            )
            response = self._validate_response(_RepositoriesPage, response_data)
            repositories.extend(response.repositories)
            if len(repositories) >= response.total_count:
                return tuple(repositories)
        raise GitHubClientError

    async def get_repository(
        self,
        user_access_token: SecretStr,
        repository_id: int,
    ) -> GitHubRepository:
        """Fetch repository metadata only when the token can access that repository."""
        response_data = await self._get_api_json(
            f"/repositories/{repository_id}",
            user_access_token,
        )
        return self._validate_response(GitHubRepository, response_data)

    async def verify_bound_repository_access(
        self,
        user_access_token: SecretStr,
        installation_id: int,
        repository_id: int,
    ) -> GitHubRepository:
        """Prove the binding's installation and repository remain visible to this user token."""
        installations = await self.list_accessible_installations(user_access_token)
        if installation_id not in {installation.id for installation in installations}:
            raise GitHubAccessVerificationError

        repository = await self.get_repository(user_access_token, repository_id)
        if repository.id != repository_id:
            raise GitHubAccessVerificationError
        return repository

    async def _get_api_json(
        self,
        path: str,
        user_access_token: SecretStr,
        params: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Make one authenticated GitHub API request with fixed version and media headers."""
        return await self._request_json(
            "GET",
            f"{self._API_BASE_URL}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {user_access_token.get_secret_value()}",
                "X-GitHub-Api-Version": self._API_VERSION,
            },
            params=params,
        )

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str],
        params: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Apply one bounded request deadline and never expose upstream error bodies."""
        try:
            async with asyncio.timeout(self._REQUEST_TIMEOUT_SECONDS):
                response = await self._http_client.request(
                    method,
                    url,
                    data=data,
                    headers=headers,
                    params=params,
                )
            response.raise_for_status()
            response_data = response.json()
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise GitHubClientError from None
        if not isinstance(response_data, dict):
            raise GitHubClientError
        return response_data

    @staticmethod
    def _validate_response(model: type[BaseModel], response_data: dict[str, Any]) -> Any:
        """Convert an untrusted GitHub object into one narrow Pydantic boundary model."""
        try:
            return model.model_validate(response_data)
        except ValidationError:
            raise GitHubClientError from None
