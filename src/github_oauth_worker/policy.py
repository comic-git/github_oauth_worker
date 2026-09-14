"""GitHub-login access policies applied before every token-related operation."""

from abc import ABC, abstractmethod
from enum import StrEnum

from github_oauth_worker.config import AccessPolicyMode, WorkerSettings
from github_oauth_worker.errors import WorkerError


class AccessOperation(StrEnum):
    """Operations that must evaluate a GitHub user's current access policy."""

    ENROLLMENT = "enrollment"
    AUTHORIZATION = "authorization"
    REFRESH = "refresh"


class AccessDeniedError(WorkerError):
    """Reject a user without revealing operator policy details to the browser."""

    public_message = "This GitHub account is not permitted to use this service."


class AccessPolicy(ABC):
    """The single authorization boundary used by enrollment and Decap token flows."""

    def require_permitted(self, github_login: str, operation: AccessOperation) -> None:
        """Raise a safe error when this login cannot perform the requested operation."""
        if not self.is_permitted(github_login, operation):
            raise AccessDeniedError

    @abstractmethod
    def is_permitted(self, github_login: str, operation: AccessOperation) -> bool:
        """Return whether a verified GitHub login may perform this operation."""


class GitHubLoginWhitelistPolicy(AccessPolicy):
    """Restrict worker use to configured GitHub logins, without case sensitivity."""

    def __init__(self, github_login_whitelist: frozenset[str]) -> None:
        self._github_login_whitelist = github_login_whitelist

    def is_permitted(self, github_login: str, operation: AccessOperation) -> bool:
        """Allow only a configured non-blank GitHub login for every protected operation."""
        del operation
        return self._canonical_login(github_login) in self._github_login_whitelist

    @staticmethod
    def _canonical_login(github_login: str) -> str:
        """Match GitHub login comparisons to normalized environment configuration."""
        return github_login.strip().casefold()


class PublicAccessPolicy(AccessPolicy):
    """Permit verified GitHub logins for the comic_git-operated shared service only."""

    def is_permitted(self, github_login: str, operation: AccessOperation) -> bool:
        """Require a non-blank verified login even when no operator whitelist applies."""
        del operation
        return bool(github_login.strip())


def build_access_policy(settings: WorkerSettings) -> AccessPolicy:
    """Create the configured access-policy boundary from validated settings."""
    if settings.access_policy is AccessPolicyMode.GITHUB_LOGIN_WHITELIST:
        return GitHubLoginWhitelistPolicy(settings.github_login_whitelist)
    if settings.access_policy is AccessPolicyMode.PUBLIC:
        return PublicAccessPolicy()
    raise ValueError(f"Unsupported access policy mode: {settings.access_policy}")
