"""Typed configuration and startup validation for the OAuth worker."""

import re
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    HttpUrl,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class AccessPolicyMode(StrEnum):
    """Supported authorization policies for GitHub users."""

    GITHUB_LOGIN_WHITELIST = "github_login_whitelist"
    PUBLIC = "public"


class Environment(StrEnum):
    """Deployment environments with separate workers and Firestore databases."""

    TEST = "test"
    PRODUCTION = "production"


class ServiceMode(StrEnum):
    """Worker availability states used during secure deployment bootstrap."""

    BOOTSTRAP = "bootstrap"
    READY = "ready"


class ConfigurationError(RuntimeError):
    """A startup failure whose public message cannot expose configuration values."""

    def __init__(self) -> None:
        super().__init__("Worker configuration is invalid.")


class FirestoreConfiguration(BaseModel):
    """Validated Firestore connection identifiers without a live client dependency."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: str
    database_id: str


class WorkerSettings(BaseSettings):
    """Read worker settings once and validate ready-mode security invariants."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    service_mode: ServiceMode = ServiceMode.BOOTSTRAP
    environment: Environment = Environment.TEST
    gcp_project_id: str | None = None
    firestore_database_id: str = "test"
    public_base_url: HttpUrl | None = None
    github_app_client_id: str | None = None
    github_app_client_secret: SecretStr | None = None
    state_signing_secret: SecretStr | None = None
    access_policy: AccessPolicyMode = AccessPolicyMode.GITHUB_LOGIN_WHITELIST
    github_login_whitelist: Annotated[frozenset[str], NoDecode] = frozenset()
    cms_minimum_engine_version: str = "1.2"
    cms_allowed_engine_branches: Annotated[frozenset[str], NoDecode] = frozenset(
        {"latest", "master"}
    )
    oauth_state_ttl_seconds: int = 600
    origin_grace_period_seconds: int = 86_400

    @field_validator("github_login_whitelist", mode="before")
    @classmethod
    def parse_github_login_whitelist(cls, value: object) -> frozenset[str]:
        """Accept the documented comma-separated environment-variable format."""
        if value is None or value == "":
            return frozenset()
        if isinstance(value, str):
            return frozenset(
                login.strip().casefold() for login in value.split(",") if login.strip()
            )
        if isinstance(value, (list, tuple, set, frozenset)):
            return frozenset(str(login).strip().casefold() for login in value if str(login).strip())
        raise ValueError("GITHUB_LOGIN_WHITELIST must be comma-separated text.")

    @field_validator("cms_allowed_engine_branches", mode="before")
    @classmethod
    def parse_cms_allowed_engine_branches(cls, value: object) -> frozenset[str]:
        """Accept deployment-defined branch names without changing Git case-sensitive semantics."""
        if isinstance(value, str):
            values = value.split(",")
        elif isinstance(value, (list, tuple, set, frozenset)):
            values = value
        else:
            raise ValueError("CMS_ALLOWED_ENGINE_BRANCHES must be comma-separated text.")
        branches = frozenset(str(branch).strip() for branch in values if str(branch).strip())
        if not branches:
            raise ValueError("CMS_ALLOWED_ENGINE_BRANCHES must contain at least one branch.")
        for branch in branches:
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch):
                raise ValueError(f"Invalid CMS_ALLOWED_ENGINE_BRANCHES value: {branch!r}.")
            if ".." in branch or "//" in branch or branch.endswith(("/", ".lock")):
                raise ValueError(f"Invalid CMS_ALLOWED_ENGINE_BRANCHES value: {branch!r}.")
        return branches

    @field_validator("cms_minimum_engine_version")
    @classmethod
    def require_canonical_cms_minimum_engine_version(cls, value: str) -> str:
        """Keep release policy comparisons independent from arbitrary Git ref syntax."""
        if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:\.(?:0|[1-9]\d*))?", value):
            raise ValueError(
                "CMS_MINIMUM_ENGINE_VERSION must be a canonical X.Y or X.Y.Z version."
            )
        return value

    @field_validator("public_base_url")
    @classmethod
    def require_https_origin(cls, value: HttpUrl | None) -> HttpUrl | None:
        """Require a canonical HTTPS origin for browser OAuth callbacks."""
        if value is None:
            return None
        has_non_origin_component = value.path not in ("", "/") or value.query or value.fragment
        if value.scheme != "https" or has_non_origin_component:
            raise ValueError(
                "PUBLIC_BASE_URL must be an HTTPS origin without a path, query, or fragment."
            )
        return value

    @field_validator("origin_grace_period_seconds")
    @classmethod
    def require_positive_origin_grace_period(cls, value: int) -> int:
        """Reject migration windows that would silently disable or never expire an old origin."""
        if value <= 0:
            raise ValueError("ORIGIN_GRACE_PERIOD_SECONDS must be positive.")
        return value

    @field_validator("oauth_state_ttl_seconds")
    @classmethod
    def require_short_lived_oauth_state(cls, value: int) -> int:
        """Limit OAuth state lifetime so a captured browser flow cannot be replayed later."""
        if not 60 <= value <= 3_600:
            raise ValueError("OAUTH_STATE_TTL_SECONDS must be between 60 and 3600.")
        return value

    @model_validator(mode="after")
    def validate_ready_mode(self) -> WorkerSettings:
        """Keep OAuth unavailable until every required runtime value is present and coherent."""
        if self.service_mode is ServiceMode.BOOTSTRAP:
            return self

        required_values = {
            "GCP_PROJECT_ID": self.gcp_project_id,
            "PUBLIC_BASE_URL": self.public_base_url,
            "GITHUB_APP_CLIENT_ID": self.github_app_client_id,
            "GITHUB_APP_CLIENT_SECRET": self.github_app_client_secret,
            "STATE_SIGNING_SECRET": self.state_signing_secret,
        }
        missing_names = [
            name
            for name, value in required_values.items()
            if value is None
            or value == ""
            or (isinstance(value, SecretStr) and not value.get_secret_value())
        ]
        if missing_names:
            raise ValueError(f"Ready mode requires {', '.join(missing_names)}.")

        expected_database_id = "test" if self.environment is Environment.TEST else "(default)"
        if self.firestore_database_id != expected_database_id:
            raise ValueError(
                f"{self.environment.value} must use Firestore database {expected_database_id!r}."
            )
        return self

    @property
    def firestore(self) -> FirestoreConfiguration:
        """Return the only validated Firestore identifiers a client may consume."""
        if self.gcp_project_id is None:
            raise ConfigurationError
        return FirestoreConfiguration(
            project_id=self.gcp_project_id,
            database_id=self.firestore_database_id,
        )


def load_settings() -> WorkerSettings:
    """Load settings while hiding validation details that could include sensitive input."""
    try:
        return WorkerSettings()
    except ValidationError:
        raise ConfigurationError from None
