"""Offline checks for creator-reviewed default-branch policy pull requests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from github_oauth_worker.cms_access import plan_cms_access_policy
from github_oauth_worker.cms_access_delivery import (
    CmsAccessDeliveryRetryError,
    access_policy_branch_name,
    current_policy_pull_request,
    deliver_cms_access_policy,
)
from github_oauth_worker.github_client import (
    GitHubGitCommit,
    GitHubGitObject,
    GitHubGitReference,
    GitHubGitSha,
    GitHubGitTree,
    GitHubPullRequest,
    GitHubPullRequestDetails,
    GitHubPullRequestRef,
    GitHubRepository,
    GitHubRepositoryOwner,
)

BASE_SHA = "a" * 40
BASE_TREE_SHA = "b" * 40
POLICY_TREE_SHA = "c" * 40
POLICY_COMMIT_SHA = "d" * 40


def _repository() -> GitHubRepository:
    return GitHubRepository(
        id=123,
        name="comic",
        full_name="owner/comic",
        owner=GitHubRepositoryOwner(login="owner"),
        default_branch="main",
    )


def _plan():
    return plan_cms_access_policy(
        existing_content=None,
        repository_id=123,
        default_branch="main",
        target_branch="cms",
        worker_origin="https://worker.example.com",
        approved_origins=("https://owner.github.io",),
    )


def _client():
    base_ref = GitHubGitReference(
        ref="refs/heads/main", object=GitHubGitObject(type="commit", sha=BASE_SHA)
    )
    return SimpleNamespace(
        get_repository_ref=AsyncMock(return_value=base_ref),
        get_repository_commit=AsyncMock(
            side_effect=lambda _token, _repo, sha: GitHubGitCommit(
                sha=sha,
                tree=GitHubGitSha(sha=BASE_TREE_SHA if sha == BASE_SHA else POLICY_TREE_SHA),
            )
        ),
        list_open_repository_pull_requests=AsyncMock(return_value=()),
        find_repository_ref=AsyncMock(return_value=None),
        list_repository_pull_requests_for_head=AsyncMock(return_value=()),
        create_repository_blobs=AsyncMock(return_value=()),
        create_repository_tree=AsyncMock(return_value=GitHubGitTree(sha=POLICY_TREE_SHA, tree=())),
        create_repository_commit=AsyncMock(
            return_value=GitHubGitCommit(
                sha=POLICY_COMMIT_SHA, tree=GitHubGitSha(sha=POLICY_TREE_SHA)
            )
        ),
        create_repository_branch=AsyncMock(),
        update_repository_branch=AsyncMock(),
        create_repository_pull_request=AsyncMock(
            return_value=GitHubPullRequest(
                number=9,
                html_url="https://github.com/owner/comic/pull/9",
                state="open",
            )
        ),
        close_repository_pull_request=AsyncMock(),
        delete_repository_branch=AsyncMock(),
    )


def _pull_request(number: int, branch: str) -> GitHubPullRequestDetails:
    repository = _repository()
    return GitHubPullRequestDetails(
        number=number,
        html_url=f"https://github.com/owner/comic/pull/{number}",
        state="open",
        head=GitHubPullRequestRef(ref=branch, sha=POLICY_COMMIT_SHA, repo=repository),
        base=GitHubPullRequestRef(ref="main", sha=BASE_SHA, repo=repository),
    )


def test_creates_policy_pr_against_reviewed_default_branch() -> None:
    client = _client()
    result = asyncio.run(
        deliver_cms_access_policy(client, SecretStr("token"), _repository(), _plan(), BASE_SHA)
    )

    assert result.pull_request_url.endswith("/pull/9")
    branch = access_policy_branch_name("main", BASE_SHA)
    client.create_repository_branch.assert_awaited_once()
    assert client.create_repository_branch.await_args.args[2:] == (branch, POLICY_COMMIT_SHA)
    assert client.create_repository_pull_request.await_args.args[2:] == (
        "Approve comic_git CMS website access",
        "Review the CMS access policy and its approved website origins before merging. "
        "The policy is read from the repository's default branch during CMS login.",
        branch,
        "main",
    )
    assert client.create_repository_blobs.await_args.args[2][0].path == (
        ".comic_git/cms_access.toml"
    )


def test_rejects_changed_default_head_before_any_write() -> None:
    client = _client()
    client.get_repository_ref.return_value = GitHubGitReference(
        ref="refs/heads/main", object=GitHubGitObject(type="commit", sha="f" * 40)
    )

    with pytest.raises(CmsAccessDeliveryRetryError):
        asyncio.run(
            deliver_cms_access_policy(client, SecretStr("token"), _repository(), _plan(), BASE_SHA)
        )

    client.create_repository_blobs.assert_not_awaited()
    client.create_repository_branch.assert_not_awaited()


def test_refreshes_existing_policy_pr_without_creating_another() -> None:
    client = _client()
    branch = access_policy_branch_name("main", BASE_SHA)
    client.list_open_repository_pull_requests.return_value = (_pull_request(7, branch),)
    client.get_repository_ref.side_effect = [
        GitHubGitReference(
            ref="refs/heads/main", object=GitHubGitObject(type="commit", sha=BASE_SHA)
        ),
        GitHubGitReference(
            ref=f"refs/heads/{branch}",
            object=GitHubGitObject(type="commit", sha="e" * 40),
        ),
    ]
    client.get_repository_commit.side_effect = lambda _token, _repo, sha: GitHubGitCommit(
        sha=sha, tree=GitHubGitSha(sha=BASE_TREE_SHA)
    )

    result = asyncio.run(
        deliver_cms_access_policy(client, SecretStr("token"), _repository(), _plan(), BASE_SHA)
    )

    assert result.pull_request_url.endswith("/pull/7")
    client.update_repository_branch.assert_awaited_once()
    client.create_repository_pull_request.assert_not_awaited()


def test_reuses_up_to_date_policy_pr_without_new_commit() -> None:
    client = _client()
    branch = access_policy_branch_name("main", BASE_SHA)
    client.list_open_repository_pull_requests.return_value = (_pull_request(7, branch),)
    client.get_repository_ref.side_effect = [
        GitHubGitReference(
            ref="refs/heads/main", object=GitHubGitObject(type="commit", sha=BASE_SHA)
        ),
        GitHubGitReference(
            ref=f"refs/heads/{branch}",
            object=GitHubGitObject(type="commit", sha=POLICY_COMMIT_SHA),
        ),
    ]

    result = asyncio.run(
        deliver_cms_access_policy(client, SecretStr("token"), _repository(), _plan(), BASE_SHA)
    )

    assert result.pull_request_url.endswith("/pull/7")
    client.create_repository_commit.assert_not_awaited()
    client.update_repository_branch.assert_not_awaited()


def test_older_policy_pr_is_closed_after_replacement_exists() -> None:
    client = _client()
    old_branch = access_policy_branch_name("main", "0" * 40)
    client.list_open_repository_pull_requests.return_value = (_pull_request(3, old_branch),)

    asyncio.run(
        deliver_cms_access_policy(client, SecretStr("token"), _repository(), _plan(), BASE_SHA)
    )

    client.create_repository_pull_request.assert_awaited_once()
    client.close_repository_pull_request.assert_awaited_once()
    client.delete_repository_branch.assert_awaited_once()
    assert client.delete_repository_branch.await_args.args[2] == old_branch


def test_policy_pr_lookup_ignores_other_revisions_and_repositories() -> None:
    branch = access_policy_branch_name("main", BASE_SHA)
    current = _pull_request(7, branch)
    old = _pull_request(3, access_policy_branch_name("main", "0" * 40))
    foreign = _pull_request(8, branch).model_copy(
        update={
            "head": GitHubPullRequestRef(
                ref=branch,
                sha=POLICY_COMMIT_SHA,
                repo=_repository().model_copy(update={"id": 999}),
            )
        }
    )

    assert (
        current_policy_pull_request((old, foreign, current), _repository(), "main", BASE_SHA)
        == current
    )
