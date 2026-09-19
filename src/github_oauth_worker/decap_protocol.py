"""Browser pages for Decap's popup handshake and exact-origin callback messages."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from github_oauth_worker.bindings import canonicalize_origin
from github_oauth_worker.html_templates import render_template


class DecapTokenPayload(BaseModel):
    """The token fields Decap receives only after the bound-origin callback completes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: SecretStr
    token_type: str = Field(min_length=1)
    provider: str = "github"
    refresh_token: SecretStr | None = None
    expires_in: int = Field(gt=0)

    def callback_data(self) -> dict[str, Any]:
        """Return the minimal Decap payload immediately before a browser callback sends it."""
        data: dict[str, Any] = {
            "access_token": self.access_token.get_secret_value(),
            "token_type": self.token_type,
            "provider": self.provider,
            "expires_in": self.expires_in,
        }
        if self.refresh_token is not None:
            data["refresh_token"] = self.refresh_token.get_secret_value()
        return data


def render_handshake_page(handshake_endpoint: str = "/auth/handshake") -> str:
    """Render a non-secret page that obtains the opener origin from Decap's reply event."""
    return render_template("decap_handshake.tpl", endpoint=handshake_endpoint)


def render_setup_handshake_page(installation_id: int) -> str:
    """Render a non-secret App-setup handoff that captures the CMS opener origin."""
    return render_template("setup_handshake.tpl", installation_id=installation_id)


def render_origin_migration_handshake_page(target_origin: str) -> str:
    """Capture an active source origin before asking GitHub to authorize one target migration."""
    return _render_management_handshake_page(
        endpoint="/origins/migrate/handshake",
        title="Start origin migration",
        target_origin=canonicalize_origin(target_origin),
    )


def render_origin_completion_handshake_page() -> str:
    """Capture the destination opener origin before attempting exact pending-origin activation."""
    return _render_management_handshake_page(
        endpoint="/origins/complete/handshake",
        title="Complete origin migration",
    )


def render_management_result_page(
    message: str,
    *,
    heading: str = "comic_git CMS setup",
    diagnostic_code: str | None = None,
) -> str:
    """Render a safe setup result with optional operator-facing troubleshooting classification."""
    return render_template(
        "management_result.tpl",
        message=message,
        heading=heading,
        diagnostic_code=diagnostic_code,
    )


def render_success_callback_page(origin: str, payload: DecapTokenPayload) -> str:
    """Render a callback that delivers Decap token data only to one canonical CMS origin."""
    return _render_callback_page(
        origin,
        "success",
        payload.callback_data(),
    )


def render_error_callback_page(origin: str, message: str) -> str:
    """Render an exact-origin Decap error callback without upstream implementation details."""
    return _render_callback_page(origin, "error", {"message": message})


def _render_callback_page(origin: str, outcome: str, payload: dict[str, Any]) -> str:
    """Embed JSON safely and preserve the exact target origin in the generated callback page."""
    canonical_origin = canonicalize_origin(origin)
    message = f"authorization:github:{outcome}:" + json.dumps(payload, separators=(",", ":"))
    return render_template(
        "decap_callback.tpl",
        target_origin=canonical_origin,
        message=message,
    )


def _render_management_handshake_page(
    *,
    endpoint: str,
    title: str,
    target_origin: str | None = None,
) -> str:
    """Generate the shared non-secret opener handshake used only by origin-management routes."""
    return render_template(
        "management_handshake.tpl",
        endpoint=endpoint,
        title=title,
        target_origin=target_origin,
    )
