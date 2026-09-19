"""Bounded GitHub App user-token and repository-verification client."""

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

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
    default_branch: str | None = Field(default=None, min_length=1)


class GitHubUserAccessToken(BaseModel):
    """Short-lived GitHub App user credentials kept only in the active request flow."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    access_token: SecretStr
    token_type: str = Field(min_length=1)
    expires_in: int = Field(gt=0)
    refresh_token: SecretStr | None = None
    refresh_token_expires_in: int | None = Field(default=None, gt=0)


class GitHubRepositoryPermission(BaseModel):
    """One verified user's effective repository permission."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    permission: str = Field(min_length=1)


class GitHubGitObject(BaseModel):
    """A Git object reference returned from the GitHub Git Data API."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    type: str = Field(min_length=1)
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class GitHubGitSha(BaseModel):
    """A Git Data API object whose response needs only its immutable SHA."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class GitHubGitReference(BaseModel):
    """A named Git ref pointing at an object in a GitHub repository."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    ref: str = Field(min_length=1)
    object: GitHubGitObject


class GitHubGitTag(BaseModel):
    """An annotated tag object that needs one explicit dereference before execution."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    object: GitHubGitObject


class GitHubGitTreeEntry(BaseModel):
    """One recursively listed Git-tree item used for bounded snapshot planning."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    path: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    type: str = Field(min_length=1)
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    size: int | None = Field(default=None, ge=0)


class GitHubGitTree(BaseModel):
    """A Git tree response with GitHub's explicit truncation signal."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    tree: tuple[GitHubGitTreeEntry, ...]
    truncated: bool = False


class GitHubGitBlob(BaseModel):
    """A base64-encoded Git blob returned by the Git Data API."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    content: str
    encoding: str = Field(min_length=1)


class GitHubGitCommit(BaseModel):
    """A commit and the tree it makes available for snapshotting or extension."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    tree: GitHubGitSha


class GitHubPullRequest(BaseModel):
    """The minimal pull-request state used by idempotent migration delivery."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    number: int = Field(gt=0)
    html_url: str = Field(min_length=1)
    state: str = Field(min_length=1)


@dataclass(frozen=True)
class GitHubTreeChange:
    """One text blob selected for an atomic Git tree update."""

    path: str
    content: str


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
    """Perform only the GitHub calls required by worker authorization and CMS enablement."""

    _API_BASE_URL = "https://api.github.com"
    _OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
    _ENGINE_OWNER = "comic-git"
    _ENGINE_REPOSITORY = "comic_git_engine"
    _ENGINE_ARCHIVE_HOST = "codeload.github.com"
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

    async def require_repository_administrator(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
        login: str,
    ) -> None:
        """Require GitHub's effective administrator permission before a migration write."""
        response_data = await self._repository_api_json(
            "GET",
            repository,
            f"/collaborators/{quote(login, safe='')}/permission",
            user_access_token,
        )
        permission = self._validate_response(GitHubRepositoryPermission, response_data)
        if permission.permission != "admin":
            raise GitHubAccessVerificationError

    async def get_repository_ref(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
        ref: str,
    ) -> GitHubGitReference:
        """Read one verified target-repository ref using its user-scoped App token."""
        response_data = await self._repository_api_json(
            "GET", repository, f"/git/ref/{quote(ref, safe='/')}", user_access_token
        )
        return self._validate_response(GitHubGitReference, response_data)

    async def get_repository_commit(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
        commit_sha: str,
    ) -> GitHubGitCommit:
        """Read the target commit and its root tree before snapshotting or writing."""
        response_data = await self._repository_api_json(
            "GET", repository, f"/git/commits/{quote(commit_sha, safe='')}", user_access_token
        )
        return self._validate_response(GitHubGitCommit, response_data)

    async def get_repository_tree(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
        tree_sha: str,
    ) -> GitHubGitTree:
        """Read a recursive target tree and retain GitHub's truncation signal for rejection."""
        response_data = await self._repository_api_json(
            "GET",
            repository,
            f"/git/trees/{quote(tree_sha, safe='')}",
            user_access_token,
            params={"recursive": "1"},
        )
        return self._validate_response(GitHubGitTree, response_data)

    async def get_repository_blob(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
        blob_sha: str,
    ) -> GitHubGitBlob:
        """Read one bounded snapshot blob after its containing tree has been validated."""
        response_data = await self._repository_api_json(
            "GET", repository, f"/git/blobs/{quote(blob_sha, safe='')}", user_access_token
        )
        return self._validate_response(GitHubGitBlob, response_data)

    async def resolve_official_engine_ref(self, ref: str) -> GitHubGitReference:
        """Resolve an official engine ref without trusting a target-repository source URL."""
        response_data = await self._public_repository_api_json(
            self._ENGINE_OWNER,
            self._ENGINE_REPOSITORY,
            f"/git/ref/{quote(ref, safe='/')}",
        )
        return self._validate_response(GitHubGitReference, response_data)

    async def get_official_engine_commit(self, commit_sha: str) -> GitHubGitCommit:
        """Read the exact official engine commit after ref resolution and tag dereferencing."""
        response_data = await self._public_repository_api_json(
            self._ENGINE_OWNER,
            self._ENGINE_REPOSITORY,
            f"/git/commits/{quote(commit_sha, safe='')}",
        )
        return self._validate_response(GitHubGitCommit, response_data)

    async def get_official_engine_tag(self, tag_sha: str) -> GitHubGitTag:
        """Read an annotated official tag so source resolution can reach its commit object."""
        response_data = await self._public_repository_api_json(
            self._ENGINE_OWNER,
            self._ENGINE_REPOSITORY,
            f"/git/tags/{quote(tag_sha, safe='')}",
        )
        return self._validate_response(GitHubGitTag, response_data)

    async def download_official_engine_archive(self, commit_sha: str) -> bytes:
        """Download an exact official engine archive through GitHub's fixed codeload redirect."""
        source_url = (
            f"{self._API_BASE_URL}/repos/{self._ENGINE_OWNER}/{self._ENGINE_REPOSITORY}"
            f"/tarball/{quote(commit_sha, safe='')}"
        )
        redirect = await self._request("GET", source_url, headers=self._api_headers())
        location = redirect.headers.get("location")
        if redirect.status_code not in {301, 302, 307, 308} or location is None:
            raise GitHubClientError
        archive_url = urlparse(location)
        expected_path = (
            f"/{self._ENGINE_OWNER}/{self._ENGINE_REPOSITORY}/legacy.tar.gz/{commit_sha}"
        )
        if archive_url.scheme != "https" or archive_url.netloc != self._ENGINE_ARCHIVE_HOST:
            raise GitHubClientError
        if archive_url.path != expected_path or archive_url.query or archive_url.fragment:
            raise GitHubClientError
        archive = await self._request(
            "GET",
            location,
            headers={"Accept": "application/octet-stream"},
        )
        return archive.content

    async def create_repository_blobs(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
        changes: tuple[GitHubTreeChange, ...],
    ) -> tuple[tuple[GitHubTreeChange, str], ...]:
        """Create UTF-8 blobs for a prevalidated atomic migration plan."""
        created = []
        for change in changes:
            response_data = await self._repository_api_json(
                "POST",
                repository,
                "/git/blobs",
                user_access_token,
                json_data={"content": change.content, "encoding": "utf-8"},
            )
            created.append((change, self._validate_response(GitHubGitSha, response_data).sha))
        return tuple(created)

    async def create_repository_tree(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
        base_tree_sha: str,
        blobs: tuple[tuple[GitHubTreeChange, str], ...],
    ) -> GitHubGitTree:
        """Create one tree that overlays only validated migration-plan paths on the base tree."""
        response_data = await self._repository_api_json(
            "POST",
            repository,
            "/git/trees",
            user_access_token,
            json_data={
                "base_tree": base_tree_sha,
                "tree": [
                    {"path": change.path, "mode": "100644", "type": "blob", "sha": blob_sha}
                    for change, blob_sha in blobs
                ],
            },
        )
        return self._validate_response(GitHubGitTree, response_data)

    async def create_repository_commit(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
        message: str,
        tree_sha: str,
        parent_sha: str,
    ) -> GitHubGitCommit:
        """Create one migration commit with the reviewed default-branch commit as its parent."""
        response_data = await self._repository_api_json(
            "POST",
            repository,
            "/git/commits",
            user_access_token,
            json_data={"message": message, "tree": tree_sha, "parents": [parent_sha]},
        )
        return self._validate_response(GitHubGitCommit, response_data)

    async def create_repository_branch(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
        branch: str,
        commit_sha: str,
    ) -> GitHubGitReference:
        """Create a worker-owned branch once and never update or force-push it."""
        response_data = await self._repository_api_json(
            "POST",
            repository,
            "/git/refs",
            user_access_token,
            json_data={"ref": f"refs/heads/{branch}", "sha": commit_sha},
        )
        return self._validate_response(GitHubGitReference, response_data)

    async def create_repository_pull_request(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> GitHubPullRequest:
        """Create one creator-reviewable migration pull request against the default branch."""
        response_data = await self._repository_api_json(
            "POST",
            repository,
            "/pulls",
            user_access_token,
            json_data={"title": title, "body": body, "head": head, "base": base},
        )
        return self._validate_response(GitHubPullRequest, response_data)

    async def find_open_repository_pull_request(
        self,
        user_access_token: SecretStr,
        repository: GitHubRepository,
        branch: str,
    ) -> GitHubPullRequest | None:
        """Find one existing worker-owned open pull request before attempting another write."""
        response_data = await self._repository_api_json_list(
            repository,
            "/pulls",
            user_access_token,
            params={"state": "open", "head": f"{repository.owner.login}:{branch}"},
        )
        pull_requests = tuple(
            self._validate_response(GitHubPullRequest, item) for item in response_data
        )
        if len(pull_requests) > 1:
            raise GitHubClientError
        return pull_requests[0] if pull_requests else None

    async def _repository_api_json(
        self,
        method: str,
        repository: GitHubRepository,
        path: str,
        user_access_token: SecretStr,
        *,
        params: dict[str, str | int] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._request_json(
            method,
            self._repository_url(repository.owner.login, repository.name, path),
            headers=self._api_headers(user_access_token),
            params=params,
            json_data=json_data,
        )

    async def _public_repository_api_json(
        self,
        owner: str,
        repository: str,
        path: str,
    ) -> dict[str, Any]:
        return await self._request_json(
            "GET",
            self._repository_url(owner, repository, path),
            headers=self._api_headers(),
        )

    async def _repository_api_json_list(
        self,
        repository: GitHubRepository,
        path: str,
        user_access_token: SecretStr,
        *,
        params: dict[str, str | int],
    ) -> list[dict[str, Any]]:
        return await self._request_json_list(
            "GET",
            self._repository_url(repository.owner.login, repository.name, path),
            headers=self._api_headers(user_access_token),
            params=params,
        )

    async def _get_api_json(
        self,
        path: str,
        user_access_token: SecretStr,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any]:
        """Make one authenticated GitHub API request with fixed version and media headers."""
        return await self._request_json(
            "GET",
            f"{self._API_BASE_URL}{path}",
            headers=self._api_headers(user_access_token),
            params=params,
        )

    def _repository_url(self, owner: str, repository: str, path: str) -> str:
        return (
            f"{self._API_BASE_URL}/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
            f"{path}"
        )

    def _api_headers(self, user_access_token: SecretStr | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self._API_VERSION,
        }
        if user_access_token is not None:
            headers["Authorization"] = f"Bearer {user_access_token.get_secret_value()}"
        return headers

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str],
        params: dict[str, str | int] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            method,
            url,
            data=data,
            headers=headers,
            params=params,
            json_data=json_data,
        )
        try:
            response_data = response.json()
        except ValueError:
            raise GitHubClientError from None
        if not isinstance(response_data, dict):
            raise GitHubClientError
        return response_data

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        headers: dict[str, str],
        params: dict[str, str | int] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Apply one bounded request deadline and hide upstream response bodies on failure."""
        try:
            async with asyncio.timeout(self._REQUEST_TIMEOUT_SECONDS):
                response = await self._http_client.request(
                    method,
                    url,
                    data=data,
                    headers=headers,
                    params=params,
                    json=json_data,
                    follow_redirects=False,
                )
            if response.is_error:
                response.raise_for_status()
            return response
        except (httpx.HTTPError, TimeoutError):
            raise GitHubClientError from None

    async def _request_json_list(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str | int] | None = None,
    ) -> list[dict[str, Any]]:
        response = await self._request(
            method,
            url,
            headers=headers,
            params=params,
        )
        try:
            response_data = response.json()
        except ValueError:
            raise GitHubClientError from None
        if not isinstance(response_data, list) or not all(
            isinstance(item, dict) for item in response_data
        ):
            raise GitHubClientError
        return response_data

    @staticmethod
    def _validate_response(model: type[BaseModel], response_data: dict[str, Any]) -> Any:
        """Convert an untrusted GitHub object into one narrow Pydantic boundary model."""
        try:
            return model.model_validate(response_data)
        except ValidationError:
            raise GitHubClientError from None
