"""Browser pages for Decap's popup handshake and exact-origin callback messages."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from github_oauth_worker.html_templates import render_template
from github_oauth_worker.origin import canonicalize_origin


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


def render_management_result_page(
    message: str,
    *,
    heading: str = "comic_git CMS setup",
    diagnostic_code: str | None = None,
    retry_url: str | None = None,
    retry_label: str = "Review latest result",
) -> str:
    """Render a safe setup result with optional operator-facing troubleshooting classification."""
    return render_template(
        "management_result.tpl",
        message=message,
        heading=heading,
        diagnostic_code=diagnostic_code,
        retry_url=retry_url,
        retry_label=retry_label,
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
