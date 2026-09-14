"""Offline tests for bounded GitHub App client requests and response validation."""

import asyncio
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from github_oauth_worker.github_client import (
    GitHubAccessVerificationError,
    GitHubAppClient,
    GitHubClientError,
)


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


def _client(handler: Any) -> GitHubAppClient:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubAppClient("client-id", SecretStr("client-secret"), http_client=http_client)
