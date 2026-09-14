"""Repository-to-origin binding models and Firestore-backed storage."""

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from google.cloud.firestore_v1 import AsyncClient, async_transactional
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from github_oauth_worker.config import WorkerSettings
from github_oauth_worker.errors import WorkerUnavailableError


class BindingStatus(StrEnum):
    """The operational status of a repository's CMS binding."""

    ACTIVE = "active"
    RECOVERY_REQUIRED = "recovery_required"


class BindingStoreError(WorkerUnavailableError):
    """Report malformed or unavailable binding storage without exposing its details."""


class BindingConflictError(BindingStoreError):
    """Reject a write that would claim an existing repository or CMS origin."""

    public_message = "That repository or CMS origin is already bound to another site."


class BindingNotFoundError(BindingStoreError):
    """Reject an update for a repository that has no binding record."""

    public_message = "The requested repository binding was not found."


class CannotRemoveLastOriginError(BindingStoreError):
    """Keep an active binding reachable until a recovery or replacement flow completes."""

    public_message = "A binding must retain at least one active CMS origin."


_http_url_adapter = TypeAdapter(HttpUrl)


def canonicalize_origin(origin: str) -> str:
    """Validate and normalize an HTTPS browser origin for exact binding comparisons."""
    try:
        url = _http_url_adapter.validate_python(origin)
    except ValidationError as error:
        raise ValueError("CMS origin must be a valid HTTPS origin.") from error

    if (
        url.scheme != "https"
        or url.username is not None
        or url.password is not None
        or url.path not in ("", "/")
        or url.query is not None
        or url.fragment is not None
    ):
        raise ValueError(
            "CMS origin must be an HTTPS origin without credentials or URL components."
        )

    host = url.host
    if host is None:
        raise ValueError("CMS origin must include a host.")
    port_suffix = "" if url.port in (None, 443) else f":{url.port}"
    return f"https://{host}{port_suffix}"


def origin_document_id(origin: str) -> str:
    """Derive a Firestore-safe fixed key without exposing full origins in document paths."""
    canonical_origin = canonicalize_origin(origin)
    return hashlib.sha256(canonical_origin.encode("utf-8")).hexdigest()


class OriginRecord(BaseModel):
    """An active CMS origin associated with one repository binding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: str
    added_at: datetime
    grace_period_ends_at: datetime | None = None

    @model_validator(mode="after")
    def validate_origin_and_timestamp(self) -> OriginRecord:
        """Store a canonical origin and an aware audit timestamp only."""
        canonical_origin = canonicalize_origin(self.origin)
        if canonical_origin != self.origin:
            raise ValueError("Origin records must use a canonical origin.")
        if self.added_at.tzinfo is None or (
            self.grace_period_ends_at is not None
            and self.grace_period_ends_at.tzinfo is None
        ):
            raise ValueError("Origin audit timestamps must include a timezone.")
        if self.grace_period_ends_at is not None and self.grace_period_ends_at <= self.added_at:
            raise ValueError("An origin grace period must end after the origin was added.")
        return self


class PendingOriginRecord(BaseModel):
    """A destination origin that cannot authorize until its own browser proves control."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: str
    repository_id: int = Field(gt=0)
    source_origin: str
    created_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_pending_origin(self) -> PendingOriginRecord:
        """Keep a pending migration unambiguous, bounded, and independent of browser state."""
        if canonicalize_origin(self.origin) != self.origin:
            raise ValueError("Pending origins must use a canonical origin.")
        if canonicalize_origin(self.source_origin) != self.source_origin:
            raise ValueError("Pending source origins must use a canonical origin.")
        if self.origin == self.source_origin:
            raise ValueError("A pending origin must differ from its source origin.")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("Pending-origin timestamps must include a timezone.")
        if self.expires_at <= self.created_at:
            raise ValueError("A pending origin must expire after it is created.")
        return self


class RepositoryBinding(BaseModel):
    """The non-secret repository, origin, installation, and audit record for one site."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repository_id: int = Field(gt=0)
    repository_owner: str = Field(min_length=1)
    repository_name: str = Field(min_length=1)
    installation_id: int = Field(gt=0)
    status: BindingStatus = BindingStatus.ACTIVE
    origins: tuple[OriginRecord, ...] = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    last_verified_at: datetime | None = None

    @model_validator(mode="after")
    def validate_origins_and_timestamps(self) -> RepositoryBinding:
        """Protect the direct origin index from ambiguous or malformed binding records."""
        origin_names = [origin.origin for origin in self.origins]
        if len(origin_names) != len(set(origin_names)):
            raise ValueError("A repository binding cannot contain duplicate origins.")
        timestamps = (self.created_at, self.updated_at, self.last_verified_at)
        if any(timestamp is not None and timestamp.tzinfo is None for timestamp in timestamps):
            raise ValueError("Binding audit timestamps must include a timezone.")
        return self

    @classmethod
    def create(
        cls,
        *,
        repository_id: int,
        repository_owner: str,
        repository_name: str,
        installation_id: int,
        origin: str,
        now: datetime | None = None,
    ) -> RepositoryBinding:
        """Create a new active binding with a single canonical initial origin."""
        timestamp = now or datetime.now(UTC)
        canonical_origin = canonicalize_origin(origin)
        return cls(
            repository_id=repository_id,
            repository_owner=repository_owner,
            repository_name=repository_name,
            installation_id=installation_id,
            origins=(OriginRecord(origin=canonical_origin, added_at=timestamp),),
            created_at=timestamp,
            updated_at=timestamp,
        )


class OriginLookupRecord(BaseModel):
    """A direct index from canonical CMS origin to its repository binding ID."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: str
    repository_id: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_origin(self) -> OriginLookupRecord:
        """Require indexed origins to have the same canonical form as their binding record."""
        if canonicalize_origin(self.origin) != self.origin:
            raise ValueError("Origin lookup records must use a canonical origin.")
        return self


class BindingStore(Protocol):
    """Persistence operations required by authorization and binding-management flows."""

    async def create_binding(self, binding: RepositoryBinding) -> None:
        """Persist a new binding and its direct origin index atomically."""

    async def get_binding(self, repository_id: int) -> RepositoryBinding | None:
        """Return a binding by its immutable GitHub repository identifier."""

    async def get_binding_for_origin(self, origin: str) -> RepositoryBinding | None:
        """Resolve a canonical origin without scanning the binding collection."""

    async def add_origin(self, repository_id: int, origin: str) -> RepositoryBinding:
        """Atomically add an unclaimed canonical origin to an existing binding."""

    async def remove_origin(self, repository_id: int, origin: str) -> RepositoryBinding:
        """Atomically remove a non-final origin from an existing binding."""

    async def begin_origin_migration(
        self,
        repository_id: int,
        source_origin: str,
        target_origin: str,
        expires_at: datetime,
    ) -> PendingOriginRecord:
        """Record a verified-origin request before the destination origin can authorize."""

    async def complete_origin_migration(
        self,
        target_origin: str,
        grace_period: timedelta,
    ) -> RepositoryBinding:
        """Activate an exact pending destination and begin the source-origin grace period."""

    async def mark_recovery_required(self, repository_id: int) -> RepositoryBinding:
        """Disable authorization until a fresh App installation confirmation repairs a binding."""

    async def recover_binding(self, binding: RepositoryBinding) -> RepositoryBinding:
        """Restore an existing repository binding with freshly verified App metadata and origin."""

    async def refresh_repository_metadata(
        self,
        repository_id: int,
        repository_owner: str,
        repository_name: str,
        installation_id: int,
    ) -> RepositoryBinding:
        """Update mutable display and installation metadata after GitHub verification."""


class InMemoryBindingStore(BindingStore):
    """Offline store with the same collision semantics as the Firestore implementation."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._bindings: dict[int, RepositoryBinding] = {}
        self._origin_repository_ids: dict[str, int] = {}
        self._pending_origins: dict[str, PendingOriginRecord] = {}
        self._lock = asyncio.Lock()
        self._now = now or (lambda: datetime.now(UTC))

    async def create_binding(self, binding: RepositoryBinding) -> None:
        """Create paired repository and origin records while holding one local transaction lock."""
        async with self._lock:
            if binding.repository_id in self._bindings:
                raise BindingConflictError
            if any(origin.origin in self._origin_repository_ids for origin in binding.origins):
                raise BindingConflictError
            self._bindings[binding.repository_id] = binding
            for origin in binding.origins:
                self._origin_repository_ids[origin.origin] = binding.repository_id

    async def get_binding(self, repository_id: int) -> RepositoryBinding | None:
        """Read the immutable repository-keyed binding record."""
        async with self._lock:
            return self._bindings.get(repository_id)

    async def get_binding_for_origin(self, origin: str) -> RepositoryBinding | None:
        """Resolve an origin index entry, then confirm its binding remains present."""
        canonical_origin = canonicalize_origin(origin)
        async with self._lock:
            repository_id = self._origin_repository_ids.get(canonical_origin)
            binding = self._bindings.get(repository_id) if repository_id is not None else None
            if binding is None:
                return None
            origin_record = _find_origin_record(binding, canonical_origin)
            if origin_record is None or not _origin_is_active(origin_record, self._now()):
                if origin_record is not None and len(binding.origins) > 1:
                    self._bindings[binding.repository_id] = binding.model_copy(
                        update={
                            "origins": tuple(
                                record
                                for record in binding.origins
                                if record.origin != canonical_origin
                            ),
                            "updated_at": self._now(),
                        }
                    )
                    del self._origin_repository_ids[canonical_origin]
                return None
            return binding

    async def add_origin(self, repository_id: int, origin: str) -> RepositoryBinding:
        """Create an origin record only when it is not claimed by another repository."""
        canonical_origin = canonicalize_origin(origin)
        async with self._lock:
            binding = self._require_binding(repository_id)
            claimed_by = self._origin_repository_ids.get(canonical_origin)
            if claimed_by is not None and claimed_by != repository_id:
                raise BindingConflictError
            if claimed_by == repository_id:
                return binding

            updated_binding = binding.model_copy(
                update={
                    "origins": (
                        *binding.origins,
                        OriginRecord(origin=canonical_origin, added_at=self._now()),
                    ),
                    "updated_at": self._now(),
                }
            )
            self._bindings[repository_id] = updated_binding
            self._origin_repository_ids[canonical_origin] = repository_id
            return updated_binding

    async def remove_origin(self, repository_id: int, origin: str) -> RepositoryBinding:
        """Remove both documents together while refusing to orphan the binding."""
        canonical_origin = canonicalize_origin(origin)
        async with self._lock:
            binding = self._require_binding(repository_id)
            if canonical_origin not in self._origin_repository_ids:
                return binding
            if self._origin_repository_ids[canonical_origin] != repository_id:
                raise BindingConflictError
            if len(binding.origins) == 1:
                raise CannotRemoveLastOriginError

            updated_binding = binding.model_copy(
                update={
                    "origins": tuple(
                        record for record in binding.origins if record.origin != canonical_origin
                    ),
                    "updated_at": self._now(),
                }
            )
            self._bindings[repository_id] = updated_binding
            del self._origin_repository_ids[canonical_origin]
            return updated_binding

    async def begin_origin_migration(
        self,
        repository_id: int,
        source_origin: str,
        target_origin: str,
        expires_at: datetime,
    ) -> PendingOriginRecord:
        """Persist a target-origin claim only after checking the active source binding."""
        canonical_source = canonicalize_origin(source_origin)
        canonical_target = canonicalize_origin(target_origin)
        async with self._lock:
            binding = self._require_binding(repository_id)
            source_record = _find_origin_record(binding, canonical_source)
            if (
                binding.status is not BindingStatus.ACTIVE
                or source_record is None
                or not _origin_is_active(source_record, self._now())
            ):
                raise BindingNotFoundError
            if (
                canonical_target in self._origin_repository_ids
                or canonical_target in self._pending_origins
            ):
                raise BindingConflictError
            pending = PendingOriginRecord(
                origin=canonical_target,
                repository_id=repository_id,
                source_origin=canonical_source,
                created_at=self._now(),
                expires_at=expires_at,
            )
            self._pending_origins[canonical_target] = pending
            return pending

    async def complete_origin_migration(
        self,
        target_origin: str,
        grace_period: timedelta,
    ) -> RepositoryBinding:
        """Activate only the exact pending target and make its source temporarily dual-homed."""
        canonical_target = canonicalize_origin(target_origin)
        if grace_period <= timedelta(0):
            raise ValueError("Origin migration grace period must be positive.")
        async with self._lock:
            pending = self._pending_origins.get(canonical_target)
            if pending is None:
                raise BindingNotFoundError
            timestamp = self._now()
            if pending.expires_at <= timestamp:
                del self._pending_origins[canonical_target]
                raise BindingNotFoundError
            binding = self._require_binding(pending.repository_id)
            source_record = _find_origin_record(binding, pending.source_origin)
            if (
                binding.status is not BindingStatus.ACTIVE
                or source_record is None
                or not _origin_is_active(source_record, timestamp)
                or canonical_target in self._origin_repository_ids
            ):
                raise BindingConflictError
            updated_source = source_record.model_copy(
                update={"grace_period_ends_at": timestamp + grace_period}
            )
            updated_binding = binding.model_copy(
                update={
                    "origins": tuple(
                        updated_source if record.origin == pending.source_origin else record
                        for record in binding.origins
                    )
                    + (OriginRecord(origin=canonical_target, added_at=timestamp),),
                    "updated_at": timestamp,
                }
            )
            self._bindings[binding.repository_id] = updated_binding
            self._origin_repository_ids[canonical_target] = binding.repository_id
            del self._pending_origins[canonical_target]
            return updated_binding

    async def mark_recovery_required(self, repository_id: int) -> RepositoryBinding:
        """Make a stale App installation unusable until its owner completes recovery."""
        async with self._lock:
            binding = self._require_binding(repository_id)
            if binding.status is BindingStatus.RECOVERY_REQUIRED:
                return binding
            updated_binding = binding.model_copy(
                update={"status": BindingStatus.RECOVERY_REQUIRED, "updated_at": self._now()}
            )
            self._bindings[repository_id] = updated_binding
            return updated_binding

    async def recover_binding(self, binding: RepositoryBinding) -> RepositoryBinding:
        """Refresh a recoverable repository while atomically claiming its new CMS origin."""
        async with self._lock:
            existing = self._require_binding(binding.repository_id)
            canonical_origin = binding.origins[0].origin
            claimed_by = self._origin_repository_ids.get(canonical_origin)
            if claimed_by is not None and claimed_by != binding.repository_id:
                raise BindingConflictError
            origins = existing.origins
            if _find_origin_record(existing, canonical_origin) is None:
                origins = (*origins, binding.origins[0])
                self._origin_repository_ids[canonical_origin] = binding.repository_id
            updated_binding = existing.model_copy(
                update={
                    "repository_owner": binding.repository_owner,
                    "repository_name": binding.repository_name,
                    "installation_id": binding.installation_id,
                    "status": BindingStatus.ACTIVE,
                    "origins": origins,
                    "updated_at": self._now(),
                    "last_verified_at": self._now(),
                }
            )
            self._bindings[binding.repository_id] = updated_binding
            return updated_binding

    async def refresh_repository_metadata(
        self,
        repository_id: int,
        repository_owner: str,
        repository_name: str,
        installation_id: int,
    ) -> RepositoryBinding:
        """Refresh GitHub-derived display metadata without changing binding identity or origins."""
        async with self._lock:
            binding = self._require_binding(repository_id)
            timestamp = self._now()
            updated_binding = binding.model_copy(
                update={
                    "repository_owner": repository_owner,
                    "repository_name": repository_name,
                    "installation_id": installation_id,
                    "updated_at": timestamp,
                    "last_verified_at": timestamp,
                }
            )
            self._bindings[repository_id] = updated_binding
            return updated_binding

    def _require_binding(self, repository_id: int) -> RepositoryBinding:
        """Return an existing binding or raise the store's safe not-found error."""
        binding = self._bindings.get(repository_id)
        if binding is None:
            raise BindingNotFoundError
        return binding


class FirestoreBindingStore(BindingStore):
    """Firestore implementation using paired direct keys and transactions for every mutation."""

    _BINDINGS_COLLECTION = "bindings"
    _ORIGINS_COLLECTION = "origins"
    _PENDING_ORIGINS_COLLECTION = "pending_origins"

    def __init__(self, client: AsyncClient, now: Callable[[], datetime] | None = None) -> None:
        self._client = client
        self._now = now or (lambda: datetime.now(UTC))

    @classmethod
    def from_settings(cls, settings: WorkerSettings) -> FirestoreBindingStore:
        """Create a database-specific client only after ready-mode settings were validated."""
        firestore = settings.firestore
        return cls(AsyncClient(project=firestore.project_id, database=firestore.database_id))

    async def create_binding(self, binding: RepositoryBinding) -> None:
        """Create repository and direct-origin documents in a single retrying transaction."""
        binding_reference = self._binding_reference(binding.repository_id)
        origin_references = [self._origin_reference(record.origin) for record in binding.origins]

        @async_transactional
        async def write(transaction: object) -> None:
            if (await binding_reference.get(transaction=transaction)).exists:
                raise BindingConflictError
            for origin_reference in origin_references:
                if (await origin_reference.get(transaction=transaction)).exists:
                    raise BindingConflictError
            transaction.create(binding_reference, self._binding_document(binding))
            for origin_record, origin_reference in zip(
                binding.origins,
                origin_references,
                strict=True,
            ):
                transaction.create(
                    origin_reference,
                    self._origin_document(OriginLookupRecord(
                        origin=origin_record.origin,
                        repository_id=binding.repository_id,
                    )),
                )

        await write(self._client.transaction())

    async def get_binding(self, repository_id: int) -> RepositoryBinding | None:
        """Read one repository binding document by immutable repository ID."""
        snapshot = await self._binding_reference(repository_id).get()
        return self._binding_from_snapshot(snapshot) if snapshot.exists else None

    async def get_binding_for_origin(self, origin: str) -> RepositoryBinding | None:
        """Read the direct origin index first, without a collection query or scan."""
        canonical_origin = canonicalize_origin(origin)
        origin_snapshot = await self._origin_reference(canonical_origin).get()
        if not origin_snapshot.exists:
            return None
        lookup = self._origin_lookup_from_snapshot(origin_snapshot)
        if lookup.origin != canonical_origin:
            raise BindingStoreError
        binding = await self.get_binding(lookup.repository_id)
        if binding is None:
            return None
        origin_record = _find_origin_record(binding, canonical_origin)
        if origin_record is None or not _origin_is_active(origin_record, self._now()):
            if origin_record is not None:
                await self._remove_expired_origin(lookup.repository_id, canonical_origin)
            return None
        return binding

    async def add_origin(self, repository_id: int, origin: str) -> RepositoryBinding:
        """Atomically claim a free origin and add it to its repository binding document."""
        canonical_origin = canonicalize_origin(origin)
        binding_reference = self._binding_reference(repository_id)
        origin_reference = self._origin_reference(canonical_origin)

        @async_transactional
        async def write(transaction: object) -> RepositoryBinding:
            binding_snapshot = await binding_reference.get(transaction=transaction)
            if not binding_snapshot.exists:
                raise BindingNotFoundError
            binding = self._binding_from_snapshot(binding_snapshot)
            origin_snapshot = await origin_reference.get(transaction=transaction)
            if origin_snapshot.exists:
                lookup = self._origin_lookup_from_snapshot(origin_snapshot)
                if lookup.repository_id != repository_id:
                    raise BindingConflictError
                return binding

            updated_binding = self._binding_with_added_origin(binding, canonical_origin)
            transaction.set(binding_reference, self._binding_document(updated_binding))
            transaction.create(
                origin_reference,
                self._origin_document(
                    OriginLookupRecord(origin=canonical_origin, repository_id=repository_id)
                ),
            )
            return updated_binding

        return await write(self._client.transaction())

    async def remove_origin(self, repository_id: int, origin: str) -> RepositoryBinding:
        """Atomically remove a non-final direct origin mapping and binding record entry."""
        canonical_origin = canonicalize_origin(origin)
        binding_reference = self._binding_reference(repository_id)
        origin_reference = self._origin_reference(canonical_origin)

        @async_transactional
        async def write(transaction: object) -> RepositoryBinding:
            binding_snapshot = await binding_reference.get(transaction=transaction)
            if not binding_snapshot.exists:
                raise BindingNotFoundError
            binding = self._binding_from_snapshot(binding_snapshot)
            origin_snapshot = await origin_reference.get(transaction=transaction)
            if not origin_snapshot.exists:
                return binding
            lookup = self._origin_lookup_from_snapshot(origin_snapshot)
            if lookup.repository_id != repository_id:
                raise BindingConflictError
            if len(binding.origins) == 1:
                raise CannotRemoveLastOriginError

            updated_binding = self._binding_with_removed_origin(binding, canonical_origin)
            transaction.set(binding_reference, self._binding_document(updated_binding))
            transaction.delete(origin_reference)
            return updated_binding

        return await write(self._client.transaction())

    async def begin_origin_migration(
        self,
        repository_id: int,
        source_origin: str,
        target_origin: str,
        expires_at: datetime,
    ) -> PendingOriginRecord:
        """Create a direct pending-target record after proving the source is currently active."""
        canonical_source = canonicalize_origin(source_origin)
        canonical_target = canonicalize_origin(target_origin)
        binding_reference = self._binding_reference(repository_id)
        target_reference = self._pending_origin_reference(canonical_target)
        active_target_reference = self._origin_reference(canonical_target)

        @async_transactional
        async def write(transaction: object) -> PendingOriginRecord:
            binding_snapshot = await binding_reference.get(transaction=transaction)
            if not binding_snapshot.exists:
                raise BindingNotFoundError
            binding = self._binding_from_snapshot(binding_snapshot)
            source_record = _find_origin_record(binding, canonical_source)
            if (
                binding.status is not BindingStatus.ACTIVE
                or source_record is None
                or not _origin_is_active(source_record, self._now())
            ):
                raise BindingNotFoundError
            if (
                (await target_reference.get(transaction=transaction)).exists
                or (await active_target_reference.get(transaction=transaction)).exists
            ):
                raise BindingConflictError
            pending = PendingOriginRecord(
                origin=canonical_target,
                repository_id=repository_id,
                source_origin=canonical_source,
                created_at=self._now(),
                expires_at=expires_at,
            )
            transaction.create(target_reference, self._pending_origin_document(pending))
            return pending

        return await write(self._client.transaction())

    async def complete_origin_migration(
        self,
        target_origin: str,
        grace_period: timedelta,
    ) -> RepositoryBinding:
        """Turn a matching pending target into an active origin in one paired transaction."""
        canonical_target = canonicalize_origin(target_origin)
        if grace_period <= timedelta(0):
            raise ValueError("Origin migration grace period must be positive.")
        pending_reference = self._pending_origin_reference(canonical_target)
        target_reference = self._origin_reference(canonical_target)

        @async_transactional
        async def write(transaction: object) -> RepositoryBinding:
            pending_snapshot = await pending_reference.get(transaction=transaction)
            if not pending_snapshot.exists:
                raise BindingNotFoundError
            pending = self._pending_origin_from_snapshot(pending_snapshot)
            timestamp = self._now()
            if pending.expires_at <= timestamp:
                transaction.delete(pending_reference)
                raise BindingNotFoundError
            binding_reference = self._binding_reference(pending.repository_id)
            binding_snapshot = await binding_reference.get(transaction=transaction)
            if not binding_snapshot.exists:
                raise BindingNotFoundError
            binding = self._binding_from_snapshot(binding_snapshot)
            source_record = _find_origin_record(binding, pending.source_origin)
            if (
                binding.status is not BindingStatus.ACTIVE
                or source_record is None
                or not _origin_is_active(source_record, timestamp)
                or (await target_reference.get(transaction=transaction)).exists
            ):
                raise BindingConflictError
            updated_source = source_record.model_copy(
                update={"grace_period_ends_at": timestamp + grace_period}
            )
            updated_binding = binding.model_copy(
                update={
                    "origins": tuple(
                        updated_source if record.origin == pending.source_origin else record
                        for record in binding.origins
                    )
                    + (OriginRecord(origin=canonical_target, added_at=timestamp),),
                    "updated_at": timestamp,
                }
            )
            transaction.set(binding_reference, self._binding_document(updated_binding))
            transaction.create(
                target_reference,
                self._origin_document(
                    OriginLookupRecord(origin=canonical_target, repository_id=binding.repository_id)
                ),
            )
            transaction.delete(pending_reference)
            return updated_binding

        return await write(self._client.transaction())

    async def mark_recovery_required(self, repository_id: int) -> RepositoryBinding:
        """Persist a fail-closed status after GitHub no longer verifies the selected repository."""
        binding_reference = self._binding_reference(repository_id)

        @async_transactional
        async def write(transaction: object) -> RepositoryBinding:
            binding_snapshot = await binding_reference.get(transaction=transaction)
            if not binding_snapshot.exists:
                raise BindingNotFoundError
            binding = self._binding_from_snapshot(binding_snapshot)
            if binding.status is BindingStatus.RECOVERY_REQUIRED:
                return binding
            updated_binding = binding.model_copy(
                update={"status": BindingStatus.RECOVERY_REQUIRED, "updated_at": self._now()}
            )
            transaction.set(binding_reference, self._binding_document(updated_binding))
            return updated_binding

        return await write(self._client.transaction())

    async def recover_binding(self, binding: RepositoryBinding) -> RepositoryBinding:
        """Restore a repository from a verified setup flow while retaining its known origins."""
        binding_reference = self._binding_reference(binding.repository_id)
        new_origin = binding.origins[0].origin
        origin_reference = self._origin_reference(new_origin)

        @async_transactional
        async def write(transaction: object) -> RepositoryBinding:
            binding_snapshot = await binding_reference.get(transaction=transaction)
            if not binding_snapshot.exists:
                raise BindingNotFoundError
            existing = self._binding_from_snapshot(binding_snapshot)
            origin_snapshot = await origin_reference.get(transaction=transaction)
            if origin_snapshot.exists:
                lookup = self._origin_lookup_from_snapshot(origin_snapshot)
                if lookup.repository_id != binding.repository_id:
                    raise BindingConflictError
            origins = existing.origins
            if _find_origin_record(existing, new_origin) is None:
                origins = (*origins, binding.origins[0])
                transaction.create(
                    origin_reference,
                    self._origin_document(
                        OriginLookupRecord(origin=new_origin, repository_id=binding.repository_id)
                    ),
                )
            timestamp = self._now()
            updated_binding = existing.model_copy(
                update={
                    "repository_owner": binding.repository_owner,
                    "repository_name": binding.repository_name,
                    "installation_id": binding.installation_id,
                    "status": BindingStatus.ACTIVE,
                    "origins": origins,
                    "updated_at": timestamp,
                    "last_verified_at": timestamp,
                }
            )
            transaction.set(binding_reference, self._binding_document(updated_binding))
            return updated_binding

        return await write(self._client.transaction())

    async def _remove_expired_origin(self, repository_id: int, origin: str) -> None:
        """Lazily remove a completed migration's source index after its grace period ends."""
        binding_reference = self._binding_reference(repository_id)
        origin_reference = self._origin_reference(origin)

        @async_transactional
        async def write(transaction: object) -> None:
            binding_snapshot = await binding_reference.get(transaction=transaction)
            if not binding_snapshot.exists:
                return
            binding = self._binding_from_snapshot(binding_snapshot)
            record = _find_origin_record(binding, origin)
            if (
                record is None
                or _origin_is_active(record, self._now())
                or len(binding.origins) == 1
            ):
                return
            transaction.set(
                binding_reference,
                self._binding_document(self._binding_with_removed_origin(binding, origin)),
            )
            transaction.delete(origin_reference)

        await write(self._client.transaction())

    async def refresh_repository_metadata(
        self,
        repository_id: int,
        repository_owner: str,
        repository_name: str,
        installation_id: int,
    ) -> RepositoryBinding:
        """Refresh mutable GitHub metadata without changing its repository or origin identities."""
        binding_reference = self._binding_reference(repository_id)

        @async_transactional
        async def write(transaction: object) -> RepositoryBinding:
            binding_snapshot = await binding_reference.get(transaction=transaction)
            if not binding_snapshot.exists:
                raise BindingNotFoundError
            binding = self._binding_from_snapshot(binding_snapshot)
            timestamp = self._now()
            updated_binding = binding.model_copy(
                update={
                    "repository_owner": repository_owner,
                    "repository_name": repository_name,
                    "installation_id": installation_id,
                    "updated_at": timestamp,
                    "last_verified_at": timestamp,
                }
            )
            transaction.set(binding_reference, self._binding_document(updated_binding))
            return updated_binding

        return await write(self._client.transaction())

    def _binding_reference(self, repository_id: int) -> object:
        """Return the repository-ID document key without exposing mutable names in paths."""
        return self._client.collection(self._BINDINGS_COLLECTION).document(str(repository_id))

    def _origin_reference(self, origin: str) -> object:
        """Return the fixed digest document key for one canonical origin."""
        return self._client.collection(self._ORIGINS_COLLECTION).document(
            origin_document_id(origin)
        )

    def _pending_origin_reference(self, origin: str) -> object:
        """Use a separate direct index so an unverified target cannot authorize prematurely."""
        return self._client.collection(self._PENDING_ORIGINS_COLLECTION).document(
            origin_document_id(origin)
        )

    @staticmethod
    def _binding_document(binding: RepositoryBinding) -> dict[str, object]:
        """Serialize validated data using Firestore's native timestamp support."""
        return binding.model_dump(mode="python")

    @staticmethod
    def _origin_document(lookup: OriginLookupRecord) -> dict[str, object]:
        """Serialize the minimal non-secret direct-origin lookup record."""
        return lookup.model_dump(mode="python")

    @staticmethod
    def _pending_origin_document(pending: PendingOriginRecord) -> dict[str, object]:
        """Serialize the short-lived migration record without browser state or credentials."""
        return pending.model_dump(mode="python")

    @staticmethod
    def _binding_from_snapshot(snapshot: object) -> RepositoryBinding:
        """Validate Firestore data before it enters worker authorization logic."""
        try:
            return RepositoryBinding.model_validate(snapshot.to_dict())
        except (AttributeError, ValidationError):
            raise BindingStoreError from None

    @staticmethod
    def _origin_lookup_from_snapshot(snapshot: object) -> OriginLookupRecord:
        """Validate Firestore index data before using its repository identifier."""
        try:
            return OriginLookupRecord.model_validate(snapshot.to_dict())
        except (AttributeError, ValidationError):
            raise BindingStoreError from None

    @staticmethod
    def _pending_origin_from_snapshot(snapshot: object) -> PendingOriginRecord:
        """Validate pending-origin data before it determines an activation target."""
        try:
            return PendingOriginRecord.model_validate(snapshot.to_dict())
        except (AttributeError, ValidationError):
            raise BindingStoreError from None

    def _binding_with_added_origin(
        self,
        binding: RepositoryBinding,
        canonical_origin: str,
    ) -> RepositoryBinding:
        """Return an immutable binding copy containing a newly active origin record."""
        timestamp = self._now()
        return binding.model_copy(
            update={
                "origins": (
                    *binding.origins,
                    OriginRecord(origin=canonical_origin, added_at=timestamp),
                ),
                "updated_at": timestamp,
            }
        )

    def _binding_with_removed_origin(
        self,
        binding: RepositoryBinding,
        canonical_origin: str,
    ) -> RepositoryBinding:
        """Return an immutable binding copy after a transaction removes one non-final origin."""
        return binding.model_copy(
            update={
                "origins": tuple(
                    record for record in binding.origins if record.origin != canonical_origin
                ),
                "updated_at": self._now(),
            }
        )


def _find_origin_record(binding: RepositoryBinding, origin: str) -> OriginRecord | None:
    """Return one canonical origin record without accidentally accepting an index-only entry."""
    return next((record for record in binding.origins if record.origin == origin), None)


def _origin_is_active(record: OriginRecord, now: datetime) -> bool:
    """Treat a completed migration's source origin as inactive once its grace period expires."""
    return record.grace_period_ends_at is None or now < record.grace_period_ends_at
