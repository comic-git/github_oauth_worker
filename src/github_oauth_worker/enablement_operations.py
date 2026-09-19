"""Non-secret, idempotent persistence for CMS enablement pull-request operations."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from google.cloud.firestore_v1 import AsyncClient, async_transactional
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from github_oauth_worker.config import WorkerSettings
from github_oauth_worker.errors import WorkerUnavailableError


class EnablementOperationStatus(StrEnum):
    """Progress states for one immutable-base CMS migration operation."""

    PLANNED = "planned"
    WRITING = "writing"
    COMPLETED = "completed"


class EnablementOperationStoreError(WorkerUnavailableError):
    """Hide malformed or unavailable operation records from browser clients."""


class EnablementOperationConflictError(EnablementOperationStoreError):
    """Reject an impossible state transition without exposing operation internals."""

    public_message = "The CMS migration state changed. Please restart setup."


class EnablementOperation(BaseModel):
    """Auditable non-secret state for one repository revision's CMS migration PR."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: int = Field(gt=0)
    base_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    engine_selector: str = Field(min_length=1)
    engine_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    branch_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    status: EnablementOperationStatus = EnablementOperationStatus.PLANNED
    pull_request_number: int | None = Field(default=None, gt=0)
    pull_request_url: str | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_completion_fields(self) -> EnablementOperation:
        """Keep partial GitHub-write state explicit and prevent ambiguous completion records."""
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("Operation timestamps must include a timezone.")
        has_pull_request = (
            self.pull_request_number is not None and self.pull_request_url is not None
        )
        if self.status is EnablementOperationStatus.COMPLETED and not has_pull_request:
            raise ValueError("Completed operations require pull request details.")
        if self.status is not EnablementOperationStatus.COMPLETED and has_pull_request:
            raise ValueError("Incomplete operations cannot contain pull request details.")
        return self

    @classmethod
    def create(
        cls,
        *,
        repository_id: int,
        base_commit_sha: str,
        engine_selector: str,
        engine_commit_sha: str,
        branch_name: str,
        now: datetime | None = None,
    ) -> EnablementOperation:
        """Create a planned operation without storing source content or credentials."""
        timestamp = now or datetime.now(UTC)
        return cls(
            repository_id=repository_id,
            base_commit_sha=base_commit_sha,
            engine_selector=engine_selector,
            engine_commit_sha=engine_commit_sha,
            branch_name=branch_name,
            created_at=timestamp,
            updated_at=timestamp,
        )


@dataclass(frozen=True)
class EnablementOperationClaim:
    """One operation plus whether this request exclusively owns its GitHub write attempt."""

    operation: EnablementOperation
    acquired: bool


class EnablementOperationStore(Protocol):
    """Persistence required to avoid duplicate migration branches and pull requests."""

    async def put_planned(self, operation: EnablementOperation) -> EnablementOperation:
        """Create or refresh a non-writing plan for one repository and base revision."""

    async def get(self, repository_id: int, base_commit_sha: str) -> EnablementOperation | None:
        """Return the record for one immutable repository revision."""

    async def claim_writing(
        self, repository_id: int, base_commit_sha: str
    ) -> EnablementOperationClaim:
        """Atomically acquire the one permitted GitHub write attempt for a plan."""

    async def complete(
        self,
        repository_id: int,
        base_commit_sha: str,
        pull_request_number: int,
        pull_request_url: str,
    ) -> EnablementOperation:
        """Record a completed pull request after its GitHub write succeeds."""


class InMemoryEnablementOperationStore(EnablementOperationStore):
    """Offline store with the same immutable-key and claim behavior as Firestore."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._operations: dict[str, EnablementOperation] = {}
        self._lock = asyncio.Lock()
        self._now = now or (lambda: datetime.now(UTC))

    async def put_planned(self, operation: EnablementOperation) -> EnablementOperation:
        async with self._lock:
            key = operation_document_id(operation.repository_id, operation.base_commit_sha)
            existing = self._operations.get(key)
            if existing is not None and existing.status is not EnablementOperationStatus.PLANNED:
                return existing
            if existing is not None:
                operation = operation.model_copy(update={"created_at": existing.created_at})
            self._operations[key] = operation
            return operation

    async def get(self, repository_id: int, base_commit_sha: str) -> EnablementOperation | None:
        async with self._lock:
            return self._operations.get(operation_document_id(repository_id, base_commit_sha))

    async def claim_writing(
        self, repository_id: int, base_commit_sha: str
    ) -> EnablementOperationClaim:
        async with self._lock:
            key = operation_document_id(repository_id, base_commit_sha)
            operation = self._operations.get(key)
            if operation is None:
                raise EnablementOperationConflictError
            if operation.status is EnablementOperationStatus.PLANNED:
                operation = operation.model_copy(
                    update={"status": EnablementOperationStatus.WRITING, "updated_at": self._now()}
                )
                self._operations[key] = operation
                return EnablementOperationClaim(operation, acquired=True)
            return EnablementOperationClaim(operation, acquired=False)

    async def complete(
        self,
        repository_id: int,
        base_commit_sha: str,
        pull_request_number: int,
        pull_request_url: str,
    ) -> EnablementOperation:
        async with self._lock:
            key = operation_document_id(repository_id, base_commit_sha)
            operation = self._operations.get(key)
            if operation is None:
                raise EnablementOperationConflictError
            if operation.status is EnablementOperationStatus.COMPLETED:
                return operation
            if operation.status is not EnablementOperationStatus.WRITING:
                raise EnablementOperationConflictError
            completed = operation.model_copy(
                update={
                    "status": EnablementOperationStatus.COMPLETED,
                    "pull_request_number": pull_request_number,
                    "pull_request_url": pull_request_url,
                    "updated_at": self._now(),
                }
            )
            self._operations[key] = completed
            return completed


class FirestoreEnablementOperationStore(EnablementOperationStore):
    """Firestore implementation with a direct immutable operation key and transactional claims."""

    _COLLECTION = "cms_enablement_operations"

    def __init__(self, client: AsyncClient, now: Callable[[], datetime] | None = None) -> None:
        self._client = client
        self._now = now or (lambda: datetime.now(UTC))

    @classmethod
    def from_settings(cls, settings: WorkerSettings) -> FirestoreEnablementOperationStore:
        firestore = settings.firestore
        return cls(AsyncClient(project=firestore.project_id, database=firestore.database_id))

    async def put_planned(self, operation: EnablementOperation) -> EnablementOperation:
        reference = self._reference(operation.repository_id, operation.base_commit_sha)

        @async_transactional
        async def write(transaction: object) -> EnablementOperation:
            snapshot = await reference.get(transaction=transaction)
            if snapshot.exists:
                existing = self._from_snapshot(snapshot)
                if existing.status is not EnablementOperationStatus.PLANNED:
                    return existing
                operation_to_write = operation.model_copy(
                    update={"created_at": existing.created_at}
                )
                transaction.set(reference, operation_to_write.model_dump(mode="python"))
                return operation_to_write
            transaction.create(reference, operation.model_dump(mode="python"))
            return operation

        return await write(self._client.transaction())

    async def get(self, repository_id: int, base_commit_sha: str) -> EnablementOperation | None:
        snapshot = await self._reference(repository_id, base_commit_sha).get()
        return self._from_snapshot(snapshot) if snapshot.exists else None

    async def claim_writing(
        self, repository_id: int, base_commit_sha: str
    ) -> EnablementOperationClaim:
        reference = self._reference(repository_id, base_commit_sha)

        @async_transactional
        async def write(transaction: object) -> EnablementOperationClaim:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise EnablementOperationConflictError
            operation = self._from_snapshot(snapshot)
            if operation.status is not EnablementOperationStatus.PLANNED:
                return EnablementOperationClaim(operation, acquired=False)
            claimed = operation.model_copy(
                update={"status": EnablementOperationStatus.WRITING, "updated_at": self._now()}
            )
            transaction.set(reference, claimed.model_dump(mode="python"))
            return EnablementOperationClaim(claimed, acquired=True)

        return await write(self._client.transaction())

    async def complete(
        self,
        repository_id: int,
        base_commit_sha: str,
        pull_request_number: int,
        pull_request_url: str,
    ) -> EnablementOperation:
        reference = self._reference(repository_id, base_commit_sha)

        @async_transactional
        async def write(transaction: object) -> EnablementOperation:
            snapshot = await reference.get(transaction=transaction)
            if not snapshot.exists:
                raise EnablementOperationConflictError
            operation = self._from_snapshot(snapshot)
            if operation.status is EnablementOperationStatus.COMPLETED:
                return operation
            if operation.status is not EnablementOperationStatus.WRITING:
                raise EnablementOperationConflictError
            completed = operation.model_copy(
                update={
                    "status": EnablementOperationStatus.COMPLETED,
                    "pull_request_number": pull_request_number,
                    "pull_request_url": pull_request_url,
                    "updated_at": self._now(),
                }
            )
            transaction.set(reference, completed.model_dump(mode="python"))
            return completed

        return await write(self._client.transaction())

    def _reference(self, repository_id: int, base_commit_sha: str) -> object:
        return self._client.collection(self._COLLECTION).document(
            operation_document_id(repository_id, base_commit_sha)
        )

    @staticmethod
    def _from_snapshot(snapshot: object) -> EnablementOperation:
        try:
            return EnablementOperation.model_validate(snapshot.to_dict())
        except AttributeError, ValidationError:
            raise EnablementOperationStoreError from None


def operation_document_id(repository_id: int, base_commit_sha: str) -> str:
    """Create a direct Firestore-safe key from immutable identifiers without repository content."""
    if (
        repository_id <= 0
        or len(base_commit_sha) != 40
        or any(c not in "0123456789abcdef" for c in base_commit_sha)
    ):
        raise ValueError(
            "Operation keys require a positive repository ID and lowercase commit SHA."
        )
    return f"{repository_id}-{base_commit_sha}"
