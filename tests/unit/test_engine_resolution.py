"""Tests for deployment-controlled engine selector policy."""

import pytest

from github_oauth_worker.config import WorkerSettings
from github_oauth_worker.engine_resolution import (
    EngineSelectorError,
    EngineSelectorPolicy,
    EngineVersion,
)


def test_engine_version_normalizes_a_missing_patch_component() -> None:
    assert EngineVersion.parse("1.2") == EngineVersion(1, 2, 0)
    assert EngineVersion.parse("1.2.3") == EngineVersion(1, 2, 3)


def test_policy_allows_configured_branches_and_sufficiently_recent_versions() -> None:
    policy = EngineSelectorPolicy.from_settings(
        WorkerSettings(
            cms_minimum_engine_version="1.2",
            cms_allowed_engine_branches="latest,master,cms",
        )
    )

    assert policy.select("cms").ref == "heads/cms"
    assert policy.select("1.2").ref == "heads/1.2"
    assert policy.select("1.2.3").version == EngineVersion(1, 2, 3)


@pytest.mark.parametrize("selector", ["1.1", "v1.2", "feature/untrusted", "1.02"])
def test_policy_rejects_unapproved_or_too_old_selectors(selector: str) -> None:
    policy = EngineSelectorPolicy.from_settings(
        WorkerSettings(
            cms_minimum_engine_version="1.2",
            cms_allowed_engine_branches="latest,master",
        )
    )

    with pytest.raises(EngineSelectorError):
        policy.select(selector)


def test_policy_explains_when_a_declared_release_is_too_old() -> None:
    policy = EngineSelectorPolicy.from_settings(
        WorkerSettings(
            cms_minimum_engine_version="1.2",
            cms_allowed_engine_branches="latest,master",
        )
    )

    with pytest.raises(EngineSelectorError) as error:
        policy.select("1.1")

    assert error.value.diagnostic_code == "engine_version_too_old"
    assert "1.2 or newer" in error.value.public_message
    assert "1.1" in error.value.public_message
