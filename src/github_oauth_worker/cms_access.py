"""Parse a repository's default-branch CMS access policy without granting access yet."""

import tomllib
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from github_oauth_worker.errors import WorkerError
from github_oauth_worker.origin import canonicalize_origin

CMS_ACCESS_POLICY_PATH = ".comic_git/cms_access.toml"
MAX_CMS_ACCESS_POLICY_BYTES = 64 * 1024


class CmsAccessPolicyError(WorkerError):
    """Deny use of a missing, invalid, or mismatched repository access policy."""

    status_code = 403
    diagnostic_code = "cms_access_policy_invalid"
    public_message = "This repository's CMS access settings do not authorize this site."


class CmsAccessPolicyPlanError(WorkerError):
    """Reject an unsafe or incomplete creator-reviewed policy proposal."""

    status_code = 400
    diagnostic_code = "cms_access_plan_invalid"
    public_message = (
        "The CMS access settings could not be prepared. Review the website origins and "
        "the repository's existing access policy before trying again."
    )


class CmsAccessPolicy(BaseModel):
    """One repository's CMS access settings, read only from its default branch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    repository_id: int = Field(strict=True, gt=0)
    cms_branch: str = Field(min_length=1, max_length=255)
    worker_origin: str
    authorized_origins: tuple[str, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_origins(self) -> CmsAccessPolicy:
        """Require unambiguous, exact HTTPS origins in worker-managed policy files."""
        origins = (self.worker_origin, *self.authorized_origins)
        if any(canonicalize_origin(origin) != origin for origin in origins):
            raise ValueError("CMS access policy origins must be canonical HTTPS origins.")
        if len(set(self.authorized_origins)) != len(self.authorized_origins):
            raise ValueError("CMS access policy cannot repeat an authorized origin.")
        if any(character.isspace() or ord(character) < 32 for character in self.cms_branch):
            raise ValueError("CMS branch must not contain whitespace or control characters.")
        return self

    def authorizes_origin(self, browser_origin: str) -> bool:
        """Compare the actual browser origin with the repository owner's approved list."""
        try:
            return canonicalize_origin(browser_origin) in self.authorized_origins
        except ValueError:
            return False


class CmsAccessPolicyChange(StrEnum):
    """How the proposed policy differs from the current default-branch file."""

    ADDED = "added"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class CmsAccessPolicyPlan:
    """A reviewable policy proposal without a GitHub write or login-side effect."""

    default_branch: str
    target_branch: str
    desired_policy: CmsAccessPolicy
    current_policy: CmsAccessPolicy | None
    proposed_content: str
    change: CmsAccessPolicyChange

    @property
    def needs_separate_pull_request(self) -> bool:
        """A policy change targets the default branch, not another CMS branch."""
        return self.change is not CmsAccessPolicyChange.UNCHANGED and (
            self.default_branch != self.target_branch
        )


def load_cms_access_policy(content: bytes) -> CmsAccessPolicy:
    """Parse a bounded policy without yet deciding whether its identity is trusted."""
    if len(content) > MAX_CMS_ACCESS_POLICY_BYTES:
        raise CmsAccessPolicyError
    try:
        document = tomllib.loads(content.decode("utf-8"))
        return CmsAccessPolicy.model_validate(document)
    except UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError, ValueError:
        raise CmsAccessPolicyError from None


def parse_cms_access_policy(
    content: bytes,
    *,
    repository_id: int,
    worker_origin: str,
) -> CmsAccessPolicy:
    """Validate a GitHub-fetched policy against verified repository and worker identities."""
    try:
        policy = load_cms_access_policy(content)
        expected_worker_origin = canonicalize_origin(worker_origin)
    except ValueError:
        raise CmsAccessPolicyError from None
    if policy.repository_id != repository_id or policy.worker_origin != expected_worker_origin:
        raise CmsAccessPolicyError
    return policy


def plan_cms_access_policy(
    *,
    existing_content: bytes | None,
    repository_id: int,
    default_branch: str,
    target_branch: str,
    worker_origin: str,
    approved_origins: tuple[str, ...],
) -> CmsAccessPolicyPlan:
    """Prepare the creator-confirmed policy for the repository's current default branch."""
    try:
        if not default_branch:
            raise ValueError("A verified default branch is required.")
        canonical_origins = tuple(
            sorted(canonicalize_origin(origin) for origin in approved_origins)
        )
        desired = CmsAccessPolicy.model_validate(
            {
                "schema_version": 1,
                "repository_id": repository_id,
                "cms_branch": target_branch,
                "worker_origin": canonicalize_origin(worker_origin),
                "authorized_origins": canonical_origins,
            }
        )
        current = load_cms_access_policy(existing_content) if existing_content is not None else None
    except CmsAccessPolicyError, ValidationError, ValueError:
        raise CmsAccessPolicyPlanError from None
    if current is not None and current.repository_id != repository_id:
        raise CmsAccessPolicyPlanError

    if current is None:
        change = CmsAccessPolicyChange.ADDED
    elif (
        current.schema_version == desired.schema_version
        and current.cms_branch == desired.cms_branch
        and current.worker_origin == desired.worker_origin
        and set(current.authorized_origins) == set(desired.authorized_origins)
    ):
        change = CmsAccessPolicyChange.UNCHANGED
    else:
        change = CmsAccessPolicyChange.UPDATED

    proposed_content = tomli_w.dumps(desired.model_dump(mode="json"))
    if len(proposed_content.encode("utf-8")) > MAX_CMS_ACCESS_POLICY_BYTES:
        raise CmsAccessPolicyPlanError
    return CmsAccessPolicyPlan(
        default_branch=default_branch,
        target_branch=target_branch,
        desired_policy=desired,
        current_policy=current,
        proposed_content=proposed_content,
        change=change,
    )
