"""Browser pages for Decap's popup handshake and exact-origin callback messages."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from github_oauth_worker.bindings import canonicalize_origin


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
    endpoint = json.dumps(handshake_endpoint).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Authorizing</title></head>
<body><script>
(() => {{
  const message = "authorizing:github";
  const endpoint = {endpoint};
  const receiveHandshake = async (event) => {{
    if (event.source !== window.opener || event.data !== message) return;
    window.removeEventListener("message", receiveHandshake);
    try {{
      const response = await fetch(endpoint, {{
        method: "POST",
        credentials: "same-origin",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{origin: event.origin}}),
      }});
      document.body.textContent = await response.text();
    }} catch {{
      document.body.textContent = "Authorization could not be started.";
    }}
  }};
  window.addEventListener("message", receiveHandshake);
  // This first discovery message carries no token, state, or user data.
  if (window.opener) window.opener.postMessage(message, "*");
}})();
</script></body></html>"""


def render_setup_handshake_page(installation_id: int) -> str:
    """Render a non-secret App-setup handoff that captures the CMS opener origin."""
    installation_value = json.dumps(installation_id)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Continue setup</title></head>
<body><script>
(() => {{
  const message = "authorizing:github";
  const receiveHandshake = async (event) => {{
    if (event.source !== window.opener || event.data !== message) return;
    window.removeEventListener("message", receiveHandshake);
    const response = await fetch("/setup/handshake", {{
      method: "POST", credentials: "same-origin",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{origin: event.origin, installation_id: {installation_value}}}),
    }});
    document.body.textContent = await response.text();
  }};
  window.addEventListener("message", receiveHandshake);
  // This initial discovery message has no token, state, or user data.
  if (window.opener) window.opener.postMessage(message, "*");
}})();
</script></body></html>"""


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
    target_origin = json.dumps(canonical_origin).replace("<", "\\u003c")
    message_data = json.dumps(
        f"authorization:github:{outcome}:" + json.dumps(payload, separators=(",", ":")),
    ).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Authorization complete</title></head>
<body><script>
(() => {{
  const targetOrigin = {target_origin};
  const message = {message_data};
  if (window.opener) window.opener.postMessage(message, targetOrigin);
  window.close();
}})();
</script></body></html>"""
