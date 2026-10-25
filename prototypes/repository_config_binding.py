"""Prototype a repository-owned CMS origin policy; not wired into OAuth routes.

The caller must fetch the config from the claimed repository with a verified
GitHub App user token. Parsing a browser-supplied copy would prove nothing.
"""

import tomllib
from dataclasses import dataclass

from pydantic import HttpUrl, TypeAdapter, ValidationError

_url_adapter = TypeAdapter(HttpUrl)


def _canonicalize_origin(value: str) -> str:
    """Validate an HTTPS browser origin without importing the Firestore binding module."""
    try:
        url = _url_adapter.validate_python(value)
    except ValidationError as error:
        raise ValueError("Expected an HTTPS browser origin.") from error
    if (
        url.scheme != "https"
        or url.username is not None
        or url.password is not None
        or url.path not in ("", "/")
        or url.query is not None
        or url.fragment is not None
        or url.host is None
    ):
        raise ValueError("Expected an HTTPS browser origin.")
    port = "" if url.port in (None, 443) else f":{url.port}"
    return f"https://{url.host}{port}"


@dataclass(frozen=True)
class VerifiedSitePolicy:
    repository_id: int
    branch: str
    origin: str


def verify_site_policy(
    config_bytes: bytes,
    *,
    browser_origin: str,
    github_repository_id: int,
    requested_branch: str,
    worker_origin: str,
) -> VerifiedSitePolicy:
    """Check one GitHub-fetched TOML file against the actual popup origin.

    A caller must separately verify GitHub App installation membership and
    choose an authoritative ref before treating this result as authorization.
    """
    if len(config_bytes) > 64 * 1024:
        raise ValueError("CMS policy file exceeds the prototype size limit.")
    try:
        document = tomllib.loads(config_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("CMS policy file is not UTF-8 TOML.") from error

    cms = document.get("cms")
    if not isinstance(cms, dict) or cms.get("enabled") is not True:
        raise ValueError("CMS is not enabled in the repository policy.")
    if cms.get("repository_id") != github_repository_id or cms.get("branch") != requested_branch:
        raise ValueError("Repository or branch does not match the GitHub-fetched policy.")
    if _canonicalize_origin(cms.get("backend_base_url", "")) != _canonicalize_origin(worker_origin):
        raise ValueError("Repository policy names another OAuth worker.")

    browser_origin = _canonicalize_origin(browser_origin)
    allowed_origins = cms.get("authorized_origins")
    if not isinstance(allowed_origins, list) or not allowed_origins:
        raise ValueError("Repository policy has no authorized CMS origins.")
    if any(not isinstance(origin, str) for origin in allowed_origins):
        raise ValueError("Repository policy has an invalid CMS origin.")
    if browser_origin not in {_canonicalize_origin(origin) for origin in allowed_origins}:
        raise ValueError("Browser origin is not authorized by the repository policy.")
    return VerifiedSitePolicy(github_repository_id, requested_branch, browser_origin)
