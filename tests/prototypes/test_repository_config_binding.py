"""Demonstrate the proposed GitHub-backed trust check without changing live auth."""

import pytest

from prototypes.repository_config_binding import verify_site_policy


def policy(
    repository_id: int = 101,
    branch: str = "cms",
    origins: str = '"https://comic-git.github.io"',
    worker: str = "https://worker.example.com",
) -> bytes:
    return (
        "[cms]\n"
        "enabled = true\n"
        f"repository_id = {repository_id}\n"
        f'branch = "{branch}"\n'
        f'backend_base_url = "{worker}"\n'
        f"authorized_origins = [{origins}]\n"
    ).encode()


def verify(config: bytes, *, origin: str = "https://comic-git.github.io", repo_id: int = 101):
    return verify_site_policy(
        config,
        browser_origin=origin,
        github_repository_id=repo_id,
        requested_branch="cms",
        worker_origin="https://worker.example.com",
    )


def test_two_repositories_can_share_one_pages_origin() -> None:
    first = verify(policy())
    second = verify(policy(repository_id=102), repo_id=102)
    assert first.origin == second.origin
    assert first.repository_id != second.repository_id


def test_copied_config_cannot_authorize_another_origin() -> None:
    with pytest.raises(ValueError, match="Browser origin"):
        verify(policy(), origin="https://attacker.example.com")


def test_browser_cannot_claim_a_different_repository() -> None:
    with pytest.raises(ValueError, match="Repository or branch"):
        verify(policy(), repo_id=102)


def test_other_worker_cannot_use_this_policy() -> None:
    with pytest.raises(ValueError, match="another OAuth worker"):
        verify(policy(worker="https://another-worker.example.com"))


def test_revocation_is_visible_when_github_config_changes() -> None:
    original_ref = policy()
    assert verify(original_ref).repository_id == 101
    with pytest.raises(ValueError, match="Browser origin"):
        verify(policy(origins='"https://new-site.example.com"'))
    # A caller that may choose the old ref can still pass after the new ref revokes it.
    assert verify(original_ref).repository_id == 101


def test_policy_must_be_enabled_and_explicit() -> None:
    with pytest.raises(ValueError, match="no authorized CMS origins"):
        verify(policy(origins=""))
    with pytest.raises(ValueError, match="not enabled"):
        verify(policy().replace(b"enabled = true", b"enabled = false"))
