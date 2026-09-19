"""Validated comic_git_engine selector policy shared by setup planning and confirmation."""

import re
from dataclasses import dataclass

from github_oauth_worker.config import WorkerSettings
from github_oauth_worker.errors import WorkerError

_VERSION_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)(?:\.(?P<patch>0|[1-9]\d*))?$"
)


class EngineSelectorError(WorkerError):
    """Reject an engine selector outside the deployment's CMS compatibility policy."""

    public_message = "This repository's comic_git engine version is not supported by this worker."


@dataclass(frozen=True, order=True)
class EngineVersion:
    """A canonical engine branch version with an optional patch component normalized to zero."""

    major: int
    minor: int
    patch: int = 0

    @classmethod
    def parse(cls, value: str) -> EngineVersion:
        """Parse the branch-version syntax shared by host repositories and the worker."""
        match = _VERSION_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError(f"Expected engine version X.Y or X.Y.Z, got {value!r}.")
        return cls(
            major=int(match["major"]),
            minor=int(match["minor"]),
            patch=int(match["patch"] or 0),
        )


@dataclass(frozen=True)
class EngineSelector:
    """A validated target-config selector and its explicit official branch ref."""

    value: str
    ref: str
    version: EngineVersion | None


@dataclass(frozen=True)
class EngineSelectorPolicy:
    """Environment-specific selector policy that rejects arbitrary source refs by construction."""

    minimum_version: EngineVersion
    allowed_branches: frozenset[str]

    @classmethod
    def from_settings(cls, settings: WorkerSettings) -> EngineSelectorPolicy:
        """Build the policy only from startup-validated deployment configuration."""
        return cls(
            minimum_version=EngineVersion.parse(settings.cms_minimum_engine_version),
            allowed_branches=settings.cms_allowed_engine_branches,
        )

    def select(self, value: str) -> EngineSelector:
        """Accept one official branch selector or one sufficiently recent release branch."""
        selector = value.strip()
        if selector in self.allowed_branches:
            return EngineSelector(value=selector, ref=f"heads/{selector}", version=None)
        try:
            version = EngineVersion.parse(selector)
        except ValueError:
            raise EngineSelectorError from None
        if version < self.minimum_version:
            raise EngineSelectorError
        return EngineSelector(value=selector, ref=f"heads/{selector}", version=version)
