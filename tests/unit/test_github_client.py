"""Offline tests for bounded GitHub App client requests and response validation."""

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from github_oauth_worker.github_client import (
    GitHubAccessVerificationError,
    GitHubAppClient,
    GitHubClientError,
    GitHubRepository,
    GitHubRepositoryOwner,
    GitHubTreeChange,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def test_exchange_requests_a_repository_restricted_token_without_logging_secrets() -> None:
    async def scenario() -> None:
        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json={
                    "access_token": "user-access-token",
                    "token_type": "bearer",
                    "expires_in": 28_800,
                    "refresh_token": "refresh-token",
                    "refresh_token_expires_in": 15_552_000,
                },
            )

        client = _client(handler)
        token = await client.exchange_authorization_code("one-time-code", repository_id=123)

        assert token.access_token.get_secret_value() == "user-access-token"
        assert captured_request is not None
        assert captured_request.url == httpx.URL(
            "https://github.com/login/oauth/access_token"
        )
        assert "repository_id=123" in captured_request.content.decode()
        assert "one-time-code" in captured_request.content.decode()

    asyncio.run(scenario())


def test_refresh_and_github_response_failures_are_generic() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/login/oauth/access_token"
            return httpx.Response(401, text="secret upstream detail")

        client = _client(handler)
        with pytest.raises(GitHubClientError) as error:
            await client.refresh_user_access_token(SecretStr("refresh-token"))

        assert "secret upstream detail" not in error.value.public_message

    asyncio.run(scenario())


def test_verify_bound_access_requires_both_installation_and_repository() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/user/installations":
                return httpx.Response(200, json={"installations": [{"id": 44}]})
            if request.url.path == "/repositories/123":
                return httpx.Response(
                    200,
                    json={"id": 123, "name": "comic", "owner": {"login": "owner"}},
                )
            raise AssertionError(f"Unexpected URL: {request.url}")

        client = _client(handler)
        repository = await client.verify_bound_repository_access(
            SecretStr("user-access-token"),
            installation_id=44,
            repository_id=123,
        )
        assert repository.name == "comic"

        with pytest.raises(GitHubAccessVerificationError):
            await client.verify_bound_repository_access(
                SecretStr("user-access-token"),
                installation_id=45,
                repository_id=123,
            )

    asyncio.run(scenario())


def test_list_repositories_uses_bounded_pagination() -> None:
    async def scenario() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.params["page"] == "1":
                return httpx.Response(
                    200,
                    json={
                        "total_count": 2,
                        "repositories": [{"id": 1, "name": "one", "owner": {"login": "owner"}}],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "repositories": [{"id": 2, "name": "two", "owner": {"login": "owner"}}],
                },
            )

        client = _client(handler)
        repositories = await client.list_repositories_for_installation(
            SecretStr("user-access-token"),
            installation_id=44,
        )

        assert [repository.id for repository in repositories] == [1, 2]
        assert len(requests) == 2

    asyncio.run(scenario())


def test_cms_enablement_reads_require_administrator_permission() -> None:
    async def scenario() -> None:
        repository = _repository()

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["Authorization"] == "Bearer user-access-token"
            if request.url.path == "/repos/owner/comic/collaborators/editor/permission":
                return httpx.Response(200, json={"permission": "admin"})
            if request.url.path == "/repos/owner/comic/git/ref/heads/master":
                return httpx.Response(
                    200,
                    json={"ref": "refs/heads/master", "object": {"type": "commit", "sha": SHA_A}},
                )
            if request.url.path == f"/repos/owner/comic/git/commits/{SHA_A}":
                return httpx.Response(200, json={"sha": SHA_A, "tree": {"sha": SHA_B}})
            if request.url.path == f"/repos/owner/comic/git/trees/{SHA_B}":
                assert request.url.params == httpx.QueryParams("recursive=1")
                return httpx.Response(
                    200,
                    json={
                        "sha": SHA_B,
                        "tree": [
                            {
                                "path": "your_content/comic_info.ini",
                                "mode": "100644",
                                "type": "blob",
                                "sha": SHA_C,
                                "size": 12,
                            }
                        ],
                    },
                )
            if request.url.path == f"/repos/owner/comic/git/blobs/{SHA_C}":
                return httpx.Response(
                    200,
                    json={"sha": SHA_C, "encoding": "base64", "content": "W0NvbWljIEluZm9d"},
                )
            raise AssertionError(f"Unexpected URL: {request.url}")

        client = _client(handler)
        token = SecretStr("user-access-token")
        await client.require_repository_administrator(token, repository, "editor")
        ref = await client.get_repository_ref(token, repository, "heads/master")
        commit = await client.get_repository_commit(token, repository, ref.object.sha)
        tree = await client.get_repository_tree(token, repository, commit.tree.sha)
        blob = await client.get_repository_blob(token, repository, tree.tree[0].sha)

        assert blob.content == "W0NvbWljIEluZm9d"

    asyncio.run(scenario())


def test_cms_enablement_writes_one_atomic_commit_and_finds_existing_pr() -> None:
    async def scenario() -> None:
        repository = _repository()
        request_bodies: dict[str, dict[str, Any]] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                request_bodies[request.url.path] = json.loads(request.content)
            if request.url.path == "/repos/owner/comic/git/blobs":
                return httpx.Response(201, json={"sha": SHA_A})
            if request.url.path == "/repos/owner/comic/git/trees":
                return httpx.Response(201, json={"sha": SHA_B, "tree": []})
            if request.url.path == "/repos/owner/comic/git/commits":
                return httpx.Response(201, json={"sha": SHA_C, "tree": {"sha": SHA_B}})
            if request.url.path == "/repos/owner/comic/git/refs":
                return httpx.Response(
                    201,
                    json={
                        "ref": "refs/heads/cms-enable-1",
                        "object": {"type": "commit", "sha": SHA_C},
                    },
                )
            if request.url.path == "/repos/owner/comic/pulls" and request.method == "POST":
                return httpx.Response(
                    201,
                    json={
                        "number": 4,
                        "html_url": "https://github.com/owner/comic/pull/4",
                        "state": "open",
                    },
                )
            if request.url.path == "/repos/owner/comic/pulls" and request.method == "GET":
                assert request.url.params == httpx.QueryParams(
                    "state=open&head=owner%3Acms-enable-1"
                )
                return httpx.Response(200, json=[])
            raise AssertionError(f"Unexpected URL: {request.url}")

        client = _client(handler)
        token = SecretStr("user-access-token")
        changes = (GitHubTreeChange("your_content/comic_info.toml", "[cms]\nenabled = true\n"),)
        blobs = await client.create_repository_blobs(token, repository, changes)
        tree = await client.create_repository_tree(token, repository, SHA_A, blobs)
        commit = await client.create_repository_commit(
            token,
            repository,
            "Enable CMS",
            tree.sha,
            SHA_A,
        )
        await client.create_repository_branch(token, repository, "cms-enable-1", commit.sha)
        pull_request = await client.create_repository_pull_request(
            token,
            repository,
            "Enable CMS",
            "Resolved engine SHA: abc",
            "cms-enable-1",
            "master",
        )
        existing = await client.find_open_repository_pull_request(token, repository, "cms-enable-1")

        assert pull_request.number == 4
        assert existing is None
        assert request_bodies["/repos/owner/comic/git/trees"] == {
            "base_tree": SHA_A,
            "tree": [
                {
                    "path": "your_content/comic_info.toml",
                    "mode": "100644",
                    "type": "blob",
                    "sha": SHA_A,
                }
            ],
        }

    asyncio.run(scenario())


def test_engine_archive_download_accepts_only_the_fixed_codeload_redirect() -> None:
    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == f"/repos/comic-git/comic_git_engine/tarball/{SHA_A}":
                return httpx.Response(
                    302,
                    headers={
                        "Location": (
                            "https://codeload.github.com/comic-git/comic_git_engine/"
                            f"legacy.tar.gz/{SHA_A}"
                        )
                    },
                )
            if request.url.host == "codeload.github.com":
                assert "Authorization" not in request.headers
                return httpx.Response(200, content=b"engine archive")
            raise AssertionError(f"Unexpected URL: {request.url}")

        archive = await _client(handler).download_official_engine_archive(SHA_A)

        assert archive == b"engine archive"

    asyncio.run(scenario())


def _client(handler: Any) -> GitHubAppClient:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubAppClient("client-id", SecretStr("client-secret"), http_client=http_client)


def _repository() -> GitHubRepository:
    return GitHubRepository(
        id=123,
        name="comic",
        owner=GitHubRepositoryOwner(login="owner"),
        default_branch="master",
    )
