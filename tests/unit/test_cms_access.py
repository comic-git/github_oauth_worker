"""Policy parsing stays independent of Firestore and normal Decap login."""

import tomllib

import pytest

from github_oauth_worker.cms_access import (
    MAX_CMS_ACCESS_POLICY_BYTES,
    CmsAccessPolicyChange,
    CmsAccessPolicyError,
    CmsAccessPolicyPlanError,
    parse_cms_access_policy,
    plan_cms_access_policy,
)


def _policy() -> bytes:
    return (
        b"schema_version = 1\n"
        b"repository_id = 123\n"
        b'cms_branch = "cms"\n'
        b'worker_origin = "https://worker.example.com"\n'
        b'authorized_origins = ["https://owner.github.io", "https://comic.example.com"]\n'
    )


def _parse(content: bytes):
    return parse_cms_access_policy(
        content,
        repository_id=123,
        worker_origin="https://worker.example.com/",
    )


def test_valid_policy_authorizes_only_its_origins() -> None:
    policy = _parse(_policy())

    assert policy.cms_branch == "cms"
    assert policy.authorizes_origin("https://OWNER.github.io:443/")
    assert policy.authorizes_origin("https://comic.example.com")
    assert not policy.authorizes_origin("https://other.example.com")
    assert not policy.authorizes_origin("http://owner.github.io")


def test_repository_and_worker_must_match_verified_identities() -> None:
    with pytest.raises(CmsAccessPolicyError):
        parse_cms_access_policy(
            _policy(), repository_id=456, worker_origin="https://worker.example.com"
        )
    with pytest.raises(CmsAccessPolicyError):
        parse_cms_access_policy(
            _policy(), repository_id=123, worker_origin="https://another-worker.example.com"
        )


@pytest.mark.parametrize(
    "content",
    [
        b"not toml = [",
        b"\xff",
        b"schema_version = 2\n" + _policy().split(b"\n", 1)[1],
        _policy().replace(b"repository_id = 123", b"repository_id = true"),
        _policy().replace(b"https://owner.github.io", b"http://owner.github.io"),
        _policy().replace(b"https://owner.github.io", b"https://owner.github.io/path"),
        _policy().replace(b"https://comic.example.com", b"https://owner.github.io"),
        _policy().replace(b'cms_branch = "cms"', b'cms_branch = " cms "'),
        _policy() + b"unknown_field = true\n",
    ],
    ids=[
        "invalid-toml",
        "invalid-utf8",
        "schema-version",
        "repository-id-type",
        "insecure-origin",
        "origin-path",
        "duplicate-origin",
        "branch-whitespace",
        "unknown-field",
    ],
)
def test_invalid_policy_fails_closed(content: bytes) -> None:
    with pytest.raises(CmsAccessPolicyError):
        _parse(content)


def test_oversized_policy_fails_closed() -> None:
    with pytest.raises(CmsAccessPolicyError):
        _parse(b"x" * (MAX_CMS_ACCESS_POLICY_BYTES + 1))


def _plan(
    *,
    existing_content: bytes | None = None,
    default_branch: str = "main",
    target_branch: str = "cms",
    approved_origins: tuple[str, ...] = ("https://comic.example.com", "https://owner.github.io"),
):
    return plan_cms_access_policy(
        existing_content=existing_content,
        repository_id=123,
        default_branch=default_branch,
        target_branch=target_branch,
        worker_origin="https://worker.example.com/",
        approved_origins=approved_origins,
    )


def test_missing_policy_is_added_to_default_branch() -> None:
    plan = _plan()

    assert plan.change is CmsAccessPolicyChange.ADDED
    assert plan.needs_separate_pull_request
    assert plan.default_branch == "main"
    assert plan.target_branch == "cms"
    assert tomllib.loads(plan.proposed_content) == {
        "schema_version": 1,
        "repository_id": 123,
        "cms_branch": "cms",
        "worker_origin": "https://worker.example.com",
        "authorized_origins": ["https://comic.example.com", "https://owner.github.io"],
    }
    assert _parse(plan.proposed_content.encode("utf-8")) == plan.desired_policy


def test_default_branch_cms_change_can_share_migration_pull_request() -> None:
    plan = _plan(default_branch="cms", target_branch="cms")

    assert plan.change is CmsAccessPolicyChange.ADDED
    assert not plan.needs_separate_pull_request


def test_semantically_identical_policy_needs_no_write() -> None:
    plan = _plan(existing_content=_policy())

    assert plan.change is CmsAccessPolicyChange.UNCHANGED
    assert not plan.needs_separate_pull_request


def test_existing_policy_can_update_branch_worker_and_explicit_origins() -> None:
    old_policy = _policy().replace(
        b"https://worker.example.com", b"https://previous-worker.example.com"
    )
    plan = _plan(
        existing_content=old_policy,
        target_branch="release",
        approved_origins=("https://new-comic.example.com",),
    )

    assert plan.change is CmsAccessPolicyChange.UPDATED
    assert plan.current_policy is not None
    assert plan.current_policy.authorized_origins == (
        "https://owner.github.io",
        "https://comic.example.com",
    )
    assert plan.desired_policy.cms_branch == "release"
    assert plan.desired_policy.authorized_origins == ("https://new-comic.example.com",)
    assert plan.needs_separate_pull_request


@pytest.mark.parametrize(
    "existing_content",
    [
        b"not toml = [",
        _policy().replace(b"repository_id = 123", b"repository_id = 456"),
    ],
    ids=["invalid-existing-policy", "different-repository"],
)
def test_planner_refuses_to_overwrite_untrusted_policy(existing_content: bytes) -> None:
    with pytest.raises(CmsAccessPolicyPlanError):
        _plan(existing_content=existing_content)


@pytest.mark.parametrize(
    "approved_origins",
    [
        (),
        ("http://comic.example.com",),
        ("https://comic.example.com/path",),
        ("https://comic.example.com", "https://COMIC.example.com:443/"),
    ],
    ids=["empty", "insecure", "path", "duplicate-after-normalization"],
)
def test_planner_requires_explicit_valid_origins(approved_origins: tuple[str, ...]) -> None:
    with pytest.raises(CmsAccessPolicyPlanError):
        _plan(approved_origins=approved_origins)


def test_planner_requires_a_verified_default_branch() -> None:
    with pytest.raises(CmsAccessPolicyPlanError):
        _plan(default_branch="")
