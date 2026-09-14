"""Signed OAuth state and browser correlation-cookie handling."""

import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from pydantic import BaseModel, ConfigDict, Field, model_validator

from github_oauth_worker.config import ConfigurationError, WorkerSettings
from github_oauth_worker.errors import WorkerError


class OAuthFlow(StrEnum):
    """Browser flows that must not be allowed to consume one another's state."""

    DECAP = "decap"
    ENROLLMENT = "enrollment"
    SETUP = "setup"
    ORIGIN_MIGRATION = "origin_migration"


class OAuthState(BaseModel):
    """The minimal signed state payload shared by all browser OAuth flows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    flow: OAuthFlow
    nonce: str
    origin: str | None = None
    repository_id: int | None = Field(default=None, gt=0)
    installation_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_flow_context(self) -> OAuthState:
        """Require each state flow to carry only the signed context its callback needs."""
        context = (self.origin, self.repository_id, self.installation_id)
        if self.flow is OAuthFlow.DECAP and any(
            value is None for value in context
        ):
            raise ValueError("This OAuth state requires a bound origin and repository context.")
        if self.flow is OAuthFlow.SETUP and (
            self.origin is None or self.installation_id is None
        ):
            raise ValueError("Setup OAuth state requires an origin and installation context.")
        if self.flow is OAuthFlow.ENROLLMENT and (
            self.origin is None
            or self.repository_id is not None
            or self.installation_id is not None
        ):
            raise ValueError("Enrollment OAuth state requires only an origin.")
        if self.flow is OAuthFlow.ORIGIN_MIGRATION and any(value is not None for value in context):
            raise ValueError("Origin-migration OAuth state may not contain enrollment context yet.")
        return self


@dataclass(frozen=True)
class IssuedOAuthState:
    """A signed state token and the separate cookie required to consume it."""

    token: str
    cookie_name: str
    correlation_nonce: str


class InvalidOAuthStateError(WorkerError):
    """Reject invalid browser state without revealing signature or cookie details."""

    public_message = "The authorization request could not be verified."


class OAuthStateManager:
    """Issue and consume short-lived state only when the browser has the matching cookie."""

    _SALT_PREFIX = "github-oauth-worker-state"

    def __init__(self, state_signing_secret: str, ttl_seconds: int) -> None:
        self._state_signing_secret = state_signing_secret
        self._ttl_seconds = ttl_seconds

    @classmethod
    def from_settings(cls, settings: WorkerSettings) -> OAuthStateManager:
        """Create a state manager only from validated ready-mode settings."""
        if settings.state_signing_secret is None:
            raise ConfigurationError
        return cls(
            state_signing_secret=settings.state_signing_secret.get_secret_value(),
            ttl_seconds=settings.oauth_state_ttl_seconds,
        )

    def issue(self, flow: OAuthFlow) -> IssuedOAuthState:
        """Create a flow-specific signed state token and opaque browser correlation nonce."""
        correlation_nonce = secrets.token_urlsafe(32)
        state = OAuthState(flow=flow, nonce=correlation_nonce)
        token = self._serializer(flow).dumps(state.model_dump(mode="json"))
        return IssuedOAuthState(
            token=token,
            cookie_name=self.correlation_cookie_name(flow),
            correlation_nonce=correlation_nonce,
        )

    def issue_decap(
        self,
        origin: str,
        repository_id: int,
        installation_id: int,
    ) -> IssuedOAuthState:
        """Create Decap state that binds the callback to an already-resolved site and repository."""
        return self._issue_with_context(OAuthFlow.DECAP, origin, repository_id, installation_id)

    def issue_enrollment(self, origin: str) -> IssuedOAuthState:
        """Create initial enrollment state bound only to the popup's captured CMS origin."""
        return self._issue_with_context(OAuthFlow.ENROLLMENT, origin)

    def issue_setup(
        self,
        origin: str,
        repository_id: int,
        installation_id: int,
    ) -> IssuedOAuthState:
        """Create fresh confirmation state for the selected App installation and repository."""
        return self._issue_with_context(OAuthFlow.SETUP, origin, repository_id, installation_id)

    def issue_setup_installation(self, origin: str, installation_id: int) -> IssuedOAuthState:
        """Create setup state for an untrusted App installation pending fresh user verification."""
        return self._issue_with_context(OAuthFlow.SETUP, origin, installation_id=installation_id)

    def attach_correlation_cookie(self, response: Response, issued_state: IssuedOAuthState) -> None:
        """Bind a browser OAuth flow to its secure, host-only correlation cookie."""
        response.set_cookie(
            key=issued_state.cookie_name,
            value=issued_state.correlation_nonce,
            max_age=self._ttl_seconds,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
        )

    def consume(
        self,
        state_token: str | None,
        correlation_nonce: str | None,
        expected_flow: OAuthFlow,
    ) -> OAuthState:
        """Validate signature, expiry, flow, and cookie correlation before continuing OAuth."""
        if not state_token or not correlation_nonce:
            raise InvalidOAuthStateError

        try:
            state = OAuthState.model_validate(
                self._serializer(expected_flow).loads(state_token, max_age=self._ttl_seconds)
            )
        except (BadSignature, SignatureExpired, ValueError):
            raise InvalidOAuthStateError from None

        is_expected_flow = state.flow == expected_flow
        is_expected_nonce = hmac.compare_digest(state.nonce, correlation_nonce)
        if not is_expected_flow or not is_expected_nonce:
            raise InvalidOAuthStateError
        return state

    def consume_from_cookies(
        self,
        state_token: str | None,
        cookies: Mapping[str, str],
    ) -> OAuthState:
        """Find the one flow whose independently signed state and correlation cookie both match."""
        for flow in OAuthFlow:
            correlation_nonce = cookies.get(self.correlation_cookie_name(flow))
            if correlation_nonce is None:
                continue
            try:
                return self.consume(state_token, correlation_nonce, flow)
            except InvalidOAuthStateError:
                continue
        raise InvalidOAuthStateError

    @staticmethod
    def correlation_cookie_name(flow: OAuthFlow) -> str:
        """Keep correlation cookies isolated so one flow cannot consume another's nonce."""
        return f"oauth_correlation_{flow.value}"

    def _serializer(self, flow: OAuthFlow) -> URLSafeTimedSerializer:
        """Use a flow-specific salt as defense in depth beyond the payload flow check."""
        return URLSafeTimedSerializer(
            secret_key=self._state_signing_secret,
            salt=f"{self._SALT_PREFIX}:{flow.value}",
        )

    def _issue_with_context(
        self,
        flow: OAuthFlow,
        origin: str,
        repository_id: int | None = None,
        installation_id: int | None = None,
    ) -> IssuedOAuthState:
        """Sign state and attach a flow-specific nonce cookie for a validated callback context."""
        correlation_nonce = secrets.token_urlsafe(32)
        state = OAuthState(
            flow=flow,
            nonce=correlation_nonce,
            origin=origin,
            repository_id=repository_id,
            installation_id=installation_id,
        )
        token = self._serializer(flow).dumps(state.model_dump(mode="json"))
        return IssuedOAuthState(
            token=token,
            cookie_name=self.correlation_cookie_name(flow),
            correlation_nonce=correlation_nonce,
        )
