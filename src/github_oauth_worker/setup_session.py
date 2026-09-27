"""Short-lived, encrypted browser credentials for one CMS setup walkthrough."""

import base64
import json
import secrets
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import Response
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, model_validator

from github_oauth_worker.config import ConfigurationError, WorkerSettings
from github_oauth_worker.errors import WorkerError


class InvalidSetupSessionError(WorkerError):
    """Require a new setup authorization if its browser session cannot be trusted."""

    public_message = "This CMS setup session expired. Start setup again."


class SetupSession(BaseModel):
    """A verified user token bound to one phase and, after selection, one repository."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: SecretStr
    nonce: str
    scope: Literal["discovery", "repository"]
    installation_id: int | None = None
    repository_id: int | None = None

    @model_validator(mode="after")
    def validate_binding(self) -> SetupSession:
        if self.scope == "discovery":
            if self.installation_id is not None or self.repository_id is not None:
                raise ValueError("Discovery sessions cannot bind a repository")
        elif (
            self.installation_id is None
            or self.installation_id <= 0
            or self.repository_id is None
            or self.repository_id <= 0
        ):
            raise ValueError("Repository sessions require a positive installation and repository")
        return self


class SetupSessionManager:
    """Keep setup tokens confidential without server-side session storage."""

    COOKIE_NAME = "cms_setup_session"
    COOKIE_PATH = "/setup"

    def __init__(self, signing_secret: str, ttl_seconds: int) -> None:
        # Domain-separated key material keeps OAuth state signatures apart from cookie encryption.
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"github-oauth-worker:cms-setup-session:v1",
        ).derive(signing_secret.encode("utf-8"))
        self._fernet = Fernet(base64.urlsafe_b64encode(key))
        self._ttl_seconds = ttl_seconds

    @classmethod
    def from_settings(cls, settings: WorkerSettings) -> SetupSessionManager:
        if settings.state_signing_secret is None:
            raise ConfigurationError
        return cls(
            settings.state_signing_secret.get_secret_value(),
            settings.oauth_state_ttl_seconds,
        )

    def attach(
        self,
        response: Response,
        access_token: SecretStr,
        nonce: str,
        *,
        scope: Literal["discovery", "repository"] = "repository",
        installation_id: int | None = None,
        repository_id: int | None = None,
    ) -> None:
        """Set a host-only cookie; GitHub credentials never appear in HTML or signed state."""
        session = SetupSession(
            access_token=access_token,
            nonce=nonce,
            scope=scope,
            installation_id=installation_id,
            repository_id=repository_id,
        )
        value = self._fernet.encrypt(
            json.dumps(
                {
                    "nonce": session.nonce,
                    "access_token": session.access_token.get_secret_value(),
                    "scope": session.scope,
                    "installation_id": session.installation_id,
                    "repository_id": session.repository_id,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        ).decode("ascii")
        response.set_cookie(
            key=self.COOKIE_NAME,
            value=value,
            max_age=self._ttl_seconds,
            secure=True,
            httponly=True,
            samesite="lax",
            path=self.COOKIE_PATH,
        )

    def read(
        self,
        value: str | None,
        expected_nonce: str,
        *,
        expected_scope: Literal["discovery", "repository"] = "repository",
        expected_installation_id: int | None = None,
        expected_repository_id: int | None = None,
    ) -> SetupSession | None:
        """Validate cookie age, signed form-state correlation, and selected repository."""
        if value is None:
            return None
        try:
            payload = self._fernet.decrypt(value.encode("ascii"), ttl=self._ttl_seconds)
            session = SetupSession.model_validate_json(payload)
        except InvalidToken, UnicodeError, ValidationError, ValueError:
            raise InvalidSetupSessionError from None
        if (
            not session.access_token.get_secret_value()
            or session.scope != expected_scope
            or not secrets.compare_digest(session.nonce, expected_nonce)
            or (
                expected_scope == "repository"
                and (
                    session.installation_id != expected_installation_id
                    or session.repository_id != expected_repository_id
                )
            )
        ):
            raise InvalidSetupSessionError
        return session

    def clear(self, response: Response) -> None:
        """Remove setup credentials once the reviewed operation has completed."""
        response.delete_cookie(key=self.COOKIE_NAME, path=self.COOKIE_PATH)
