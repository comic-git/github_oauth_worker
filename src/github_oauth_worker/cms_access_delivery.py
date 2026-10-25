"""Deliver a reviewed CMS access policy through a worker-owned GitHub pull request."""

import hashlib
import re
from dataclasses import dataclass

from pydantic import SecretStr

from github_oauth_worker.cms_access import (
    CMS_ACCESS_POLICY_PATH,
    CmsAccessPolicyChange,
    CmsAccessPolicyPlan,
)
from github_oauth_worker.errors import WorkerError
from github_oauth_worker.github_client import (
    GitHubAppClient,
    GitHubClientError,
    GitHubPullRequestDetails,
    GitHubRepository,
    GitHubTreeChange,
)


class CmsAccessDeliveryRetryError(WorkerError):
    """Require a fresh review after the default branch or PR changes."""

    status_code = 409
    diagnostic_code = "cms_access_delivery_retry"
    public_message = "The repository changed while CMS access was being saved. Review it again."


@dataclass(frozen=True)
class CmsAccessDeliveryResult:
    """One policy PR and any superseded PRs whose branches could not be removed."""

    pull_request_url: str
    unremoved_branches: tuple[str, ...] = ()


def access_policy_branch_name(default_branch: str, base_commit_sha: str) -> str:
    """Reserve a stable worker namespace for one default-branch revision."""
    identity = f"{default_branch}\0{base_commit_sha}".encode()
    return f"comic-git/cms-access/{hashlib.sha256(identity).hexdigest()[:12]}"


def _worker_policy_pull_requests(
    pull_requests: tuple[GitHubPullRequestDetails, ...],
    repository: GitHubRepository,
    default_branch: str,
) -> tuple[GitHubPullRequestDetails, ...]:
    return tuple(
        pr
        for pr in pull_requests
        if pr.state == "open"
        and pr.base.ref == default_branch
        and pr.base.repo is not None
        and pr.base.repo.id == repository.id
        and pr.head.repo is not None
        and pr.head.repo.id == repository.id
        and re.fullmatch(r"comic-git/cms-access/[0-9a-f]{12}(?:-[1-9][0-9]*)?", pr.head.ref)
    )


def current_policy_pull_request(
    pull_requests: tuple[GitHubPullRequestDetails, ...],
    repository: GitHubRepository,
    default_branch: str,
    base_commit_sha: str,
) -> GitHubPullRequestDetails | None:
    """Find the newest worker policy PR derived from this default-branch revision."""
    base_name = access_policy_branch_name(default_branch, base_commit_sha)
    return max(
        (
            pr
            for pr in _worker_policy_pull_requests(pull_requests, repository, default_branch)
            if pr.head.ref == base_name
            or re.fullmatch(re.escape(base_name) + r"-[1-9][0-9]*", pr.head.ref)
        ),
        key=lambda pr: pr.number,
        default=None,
    )


async def deliver_cms_access_policy(
    client: GitHubAppClient,
    user_access_token: SecretStr,
    repository: GitHubRepository,
    plan: CmsAccessPolicyPlan,
    reviewed_default_commit_sha: str,
) -> CmsAccessDeliveryResult:
    """Create or refresh a policy PR only while the reviewed default revision is current."""
    if (
        plan.change is CmsAccessPolicyChange.UNCHANGED
        or repository.id != plan.desired_policy.repository_id
        or repository.default_branch != plan.default_branch
    ):
        raise CmsAccessDeliveryRetryError

    try:
        base_ref = await client.get_repository_ref(
            user_access_token, repository, f"heads/{plan.default_branch}"
        )
        if base_ref.object.type != "commit" or base_ref.object.sha != reviewed_default_commit_sha:
            raise CmsAccessDeliveryRetryError
        base_commit = await client.get_repository_commit(
            user_access_token, repository, reviewed_default_commit_sha
        )
        base_name = access_policy_branch_name(plan.default_branch, reviewed_default_commit_sha)
        candidates = _worker_policy_pull_requests(
            await client.list_open_repository_pull_requests(
                user_access_token, repository, plan.default_branch
            ),
            repository,
            plan.default_branch,
        )
        current = current_policy_pull_request(
            candidates, repository, plan.default_branch, reviewed_default_commit_sha
        )
        if current is None:
            for suffix in range(20):
                branch = base_name if suffix == 0 else f"{base_name}-{suffix}"
                branch_ref = await client.find_repository_ref(
                    user_access_token, repository, f"heads/{branch}"
                )
                if branch_ref is None:
                    break
                attached = await client.list_repository_pull_requests_for_head(
                    user_access_token, repository, branch
                )
                if not attached:
                    break
            else:
                raise CmsAccessDeliveryRetryError
        else:
            branch = current.head.ref
            branch_ref = await client.get_repository_ref(
                user_access_token, repository, f"heads/{branch}"
            )

        blobs = await client.create_repository_blobs(
            user_access_token,
            repository,
            (GitHubTreeChange(CMS_ACCESS_POLICY_PATH, plan.proposed_content),),
        )
        tree = await client.create_repository_tree(
            user_access_token, repository, base_commit.tree.sha, blobs
        )
        if branch_ref is None:
            commit = await client.create_repository_commit(
                user_access_token,
                repository,
                "Approve comic_git CMS website access",
                tree.sha,
                reviewed_default_commit_sha,
            )
            await client.create_repository_branch(user_access_token, repository, branch, commit.sha)
        else:
            branch_commit = await client.get_repository_commit(
                user_access_token, repository, branch_ref.object.sha
            )
            if branch_commit.tree.sha != tree.sha:
                commit = await client.create_repository_commit(
                    user_access_token,
                    repository,
                    "Refresh comic_git CMS website access",
                    tree.sha,
                    branch_ref.object.sha,
                )
                await client.update_repository_branch(
                    user_access_token, repository, branch, commit.sha
                )

        if current is None:
            pull_request = await client.create_repository_pull_request(
                user_access_token,
                repository,
                "Approve comic_git CMS website access",
                "Review the CMS access policy and its approved website origins before merging. "
                "The policy is read from the repository's default branch during CMS login.",
                branch,
                plan.default_branch,
            )
        else:
            pull_request = current

        unremoved = set()
        for old_pr in candidates:
            if old_pr.number == pull_request.number:
                continue
            await client.close_repository_pull_request(user_access_token, repository, old_pr.number)
            if old_pr.head.ref == branch:
                continue
            try:
                attached = await client.list_repository_pull_requests_for_head(
                    user_access_token, repository, old_pr.head.ref
                )
                if not any(pr.state == "open" for pr in attached):
                    await client.delete_repository_branch(
                        user_access_token, repository, old_pr.head.ref
                    )
            except GitHubClientError:
                unremoved.add(old_pr.head.ref)
        return CmsAccessDeliveryResult(pull_request.html_url, tuple(sorted(unremoved)))
    except GitHubClientError:
        raise CmsAccessDeliveryRetryError from None
