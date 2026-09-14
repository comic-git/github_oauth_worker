"""Tests for GitHub-login access-policy behavior."""

import pytest

from github_oauth_worker.config import AccessPolicyMode, WorkerSettings
from github_oauth_worker.policy import (
    AccessDeniedError,
    AccessOperation,
    GitHubLoginWhitelistPolicy,
    PublicAccessPolicy,
    build_access_policy,
)


@pytest.mark.parametrize("operation", AccessOperation)
def test_whitelist_applies_to_enrollment_authorization_and_refresh(
    operation: AccessOperation,
) -> None:
    policy = GitHubLoginWhitelistPolicy(frozenset({"marco"}))

    assert policy.is_permitted("MARCO", operation)
    assert not policy.is_permitted("someone-else", operation)


def test_an_empty_whitelist_denies_all_logins() -> None:
    policy = GitHubLoginWhitelistPolicy(frozenset())

    with pytest.raises(AccessDeniedError) as error:
        policy.require_permitted("marco", AccessOperation.ENROLLMENT)

    assert error.value.public_message == "This GitHub account is not permitted to use this service."


@pytest.mark.parametrize("operation", AccessOperation)
def test_public_policy_requires_a_non_blank_verified_login(operation: AccessOperation) -> None:
    policy = PublicAccessPolicy()

    assert policy.is_permitted("marco", operation)
    assert not policy.is_permitted("   ", operation)


def test_settings_select_the_expected_access_policy() -> None:
    whitelist_policy = build_access_policy(WorkerSettings(github_login_whitelist="marco"))
    public_policy = build_access_policy(WorkerSettings(access_policy=AccessPolicyMode.PUBLIC))

    assert whitelist_policy.is_permitted("marco", AccessOperation.REFRESH)
    assert public_policy.is_permitted("any-github-login", AccessOperation.REFRESH)
