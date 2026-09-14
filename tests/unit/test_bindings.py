"""Offline tests for repository-to-origin binding behavior."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from github_oauth_worker.bindings import (
    BindingConflictError,
    BindingNotFoundError,
    BindingStatus,
    CannotRemoveLastOriginError,
    FirestoreBindingStore,
    InMemoryBindingStore,
    RepositoryBinding,
    canonicalize_origin,
    origin_document_id,
)


def test_canonicalize_origin_rejects_non_origin_components() -> None:
    assert canonicalize_origin("https://CMS.Example.com:443/") == "https://cms.example.com"

    for invalid_origin in (
        "http://cms.example.com",
        "https://cms.example.com/admin",
        "https://user@cms.example.com",
        "https://cms.example.com?query=value",
    ):
        with pytest.raises(ValueError, match="CMS origin"):
            canonicalize_origin(invalid_origin)


def test_origin_document_id_uses_the_canonical_origin() -> None:
    assert origin_document_id("https://CMS.Example.com/") == origin_document_id(
        "https://cms.example.com"
    )
    assert len(origin_document_id("https://cms.example.com")) == 64


def test_in_memory_store_enforces_repository_and_origin_uniqueness() -> None:
    async def scenario() -> None:
        store = InMemoryBindingStore()
        binding = _binding(repository_id=101, origin="https://one.example.com")
        await store.create_binding(binding)

        with pytest.raises(BindingConflictError):
            await store.create_binding(binding)
        with pytest.raises(BindingConflictError):
            await store.create_binding(_binding(repository_id=102, origin="https://one.example.com"))

    asyncio.run(scenario())


def test_in_memory_store_resolves_origins_without_a_collection_scan() -> None:
    async def scenario() -> None:
        store = InMemoryBindingStore()
        binding = _binding(repository_id=101, origin="https://one.example.com")
        await store.create_binding(binding)

        found_binding = await store.get_binding_for_origin("https://ONE.example.com/")

        assert found_binding == binding
        assert await store.get_binding_for_origin("https://unknown.example.com") is None

    asyncio.run(scenario())


def test_in_memory_store_adds_and_removes_origins_atomically() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 14, tzinfo=UTC)
        store = InMemoryBindingStore(now=lambda: now)
        await store.create_binding(
            _binding(repository_id=101, origin="https://one.example.com", now=now)
        )

        updated_binding = await store.add_origin(101, "https://two.example.com")
        assert {record.origin for record in updated_binding.origins} == {
            "https://one.example.com",
            "https://two.example.com",
        }
        assert (await store.get_binding_for_origin("https://two.example.com")).repository_id == 101

        updated_binding = await store.remove_origin(101, "https://two.example.com")
        assert [record.origin for record in updated_binding.origins] == ["https://one.example.com"]
        assert await store.get_binding_for_origin("https://two.example.com") is None

    asyncio.run(scenario())


def test_in_memory_store_refuses_to_orphan_or_cross_claim_an_origin() -> None:
    async def scenario() -> None:
        store = InMemoryBindingStore()
        await store.create_binding(_binding(repository_id=101, origin="https://one.example.com"))
        await store.create_binding(_binding(repository_id=102, origin="https://two.example.com"))

        with pytest.raises(CannotRemoveLastOriginError):
            await store.remove_origin(101, "https://one.example.com")
        with pytest.raises(BindingConflictError):
            await store.add_origin(101, "https://two.example.com")
        with pytest.raises(BindingNotFoundError):
            await store.add_origin(404, "https://three.example.com")

    asyncio.run(scenario())


def test_in_memory_store_refreshes_only_mutable_github_metadata() -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 14, tzinfo=UTC)
        later = now + timedelta(minutes=1)
        store = InMemoryBindingStore(now=lambda: later)
        binding = _binding(repository_id=101, origin="https://one.example.com", now=now)
        await store.create_binding(binding)

        updated_binding = await store.refresh_repository_metadata(
            101,
            "new-owner",
            "new-name",
            987,
        )

        assert updated_binding.repository_id == binding.repository_id
        assert updated_binding.origins == binding.origins
        assert updated_binding.repository_owner == "new-owner"
        assert updated_binding.repository_name == "new-name"
        assert updated_binding.installation_id == 987
        assert updated_binding.last_verified_at == later

    asyncio.run(scenario())


def test_in_memory_store_activates_only_the_exact_pending_origin_and_expires_the_source() -> None:
    async def scenario() -> None:
        timestamp = datetime(2026, 9, 14, tzinfo=UTC)
        clock = [timestamp]
        store = InMemoryBindingStore(now=lambda: clock[0])
        await store.create_binding(
            _binding(repository_id=101, origin="https://old.example.com", now=timestamp)
        )

        pending = await store.begin_origin_migration(
            101,
            "https://old.example.com",
            "https://new.example.com",
            timestamp + timedelta(minutes=10),
        )
        assert pending.origin == "https://new.example.com"
        assert await store.get_binding_for_origin("https://new.example.com") is None

        clock[0] += timedelta(minutes=1)
        binding = await store.complete_origin_migration(
            "https://new.example.com",
            timedelta(hours=1),
        )
        source = next(record for record in binding.origins if record.origin == "https://old.example.com")
        assert source.grace_period_ends_at == clock[0] + timedelta(hours=1)
        assert await store.get_binding_for_origin("https://old.example.com") is not None
        assert await store.get_binding_for_origin("https://new.example.com") is not None

        clock[0] += timedelta(hours=1)
        assert await store.get_binding_for_origin("https://old.example.com") is None
        assert await store.get_binding_for_origin("https://new.example.com") is not None
        assert [record.origin for record in (await store.get_binding(101)).origins] == [
            "https://new.example.com"
        ]

    asyncio.run(scenario())


def test_in_memory_store_marks_and_recovers_a_binding_after_github_access_changes() -> None:
    async def scenario() -> None:
        store = InMemoryBindingStore()
        await store.create_binding(_binding(repository_id=101, origin="https://old.example.com"))

        unavailable = await store.mark_recovery_required(101)
        assert unavailable.status is BindingStatus.RECOVERY_REQUIRED

        recovered = await store.recover_binding(
            RepositoryBinding.create(
                repository_id=101,
                repository_owner="new-owner",
                repository_name="new-comic",
                installation_id=456,
                origin="https://new.example.com",
            )
        )
        assert recovered.status is BindingStatus.ACTIVE
        assert recovered.installation_id == 456
        assert recovered.repository_owner == "new-owner"
        assert {record.origin for record in recovered.origins} == {
            "https://old.example.com",
            "https://new.example.com",
        }

    asyncio.run(scenario())


def test_firestore_store_uses_paired_direct_keys_for_each_atomic_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        client = _FakeFirestoreClient()
        monkeypatch.setattr(
            "github_oauth_worker.bindings.async_transactional",
            lambda operation: operation,
        )
        store = FirestoreBindingStore(client)
        binding = _binding(repository_id=101, origin="https://one.example.com")

        await store.create_binding(binding)
        assert await store.get_binding_for_origin("https://one.example.com") == binding

        with pytest.raises(BindingConflictError):
            await store.create_binding(_binding(repository_id=102, origin="https://one.example.com"))

        updated_binding = await store.add_origin(101, "https://two.example.com")
        assert len(updated_binding.origins) == 2
        assert (await store.get_binding_for_origin("https://two.example.com")).repository_id == 101

        updated_binding = await store.remove_origin(101, "https://two.example.com")
        assert len(updated_binding.origins) == 1
        assert await store.get_binding_for_origin("https://two.example.com") is None

        await store.begin_origin_migration(
            101,
            "https://one.example.com",
            "https://new.example.com",
            datetime.now(UTC) + timedelta(minutes=10),
        )
        migrated_binding = await store.complete_origin_migration(
            "https://new.example.com",
            timedelta(hours=1),
        )
        assert {record.origin for record in migrated_binding.origins} == {
            "https://one.example.com",
            "https://new.example.com",
        }
        assert (await store.get_binding_for_origin("https://new.example.com")).repository_id == 101

        recovered_binding = await store.recover_binding(
            RepositoryBinding.create(
                repository_id=101,
                repository_owner="new-owner",
                repository_name="new-comic",
                installation_id=789,
                origin="https://recovery.example.com",
            )
        )
        assert recovered_binding.status is BindingStatus.ACTIVE
        assert recovered_binding.installation_id == 789
        recovery_lookup = await store.get_binding_for_origin("https://recovery.example.com")
        assert recovery_lookup is not None
        assert recovery_lookup.repository_id == 101

        refreshed_binding = await store.refresh_repository_metadata(
            101,
            "new-owner",
            "new-name",
            456,
        )
        assert refreshed_binding.repository_owner == "new-owner"
        assert refreshed_binding.repository_name == "new-name"
        assert refreshed_binding.installation_id == 456

    asyncio.run(scenario())


def _binding(repository_id: int, origin: str, now: datetime | None = None) -> RepositoryBinding:
    return RepositoryBinding.create(
        repository_id=repository_id,
        repository_owner="owner",
        repository_name="comic",
        installation_id=123,
        origin=origin,
        now=now,
    )


class _FakeFirestoreClient:
    """Minimal offline Firestore double that records transaction-backed document changes."""

    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], dict[str, object]] = {}

    def collection(self, collection_name: str) -> _FakeFirestoreCollection:
        return _FakeFirestoreCollection(self, collection_name)

    def transaction(self) -> _FakeFirestoreTransaction:
        return _FakeFirestoreTransaction(self)


class _FakeFirestoreCollection:
    """Resolve document references in the fake client's two collections."""

    def __init__(self, client: _FakeFirestoreClient, collection_name: str) -> None:
        self._client = client
        self._collection_name = collection_name

    def document(self, document_id: str) -> _FakeFirestoreDocumentReference:
        return _FakeFirestoreDocumentReference(self._client, self._collection_name, document_id)


class _FakeFirestoreDocumentReference:
    """Expose the asynchronous document read shape used by the production adapter."""

    def __init__(
        self,
        client: _FakeFirestoreClient,
        collection_name: str,
        document_id: str,
    ) -> None:
        self._client = client
        self._key = (collection_name, document_id)

    async def get(self, transaction: object | None = None) -> _FakeFirestoreSnapshot:
        del transaction
        return _FakeFirestoreSnapshot(self._client.documents.get(self._key))


class _FakeFirestoreSnapshot:
    """Represent an existing or missing Firestore document."""

    def __init__(self, data: dict[str, object] | None) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, object] | None:
        return self._data.copy() if self._data is not None else None


class _FakeFirestoreTransaction:
    """Apply fake transaction mutations after the adapter has read all required records."""

    def __init__(self, client: _FakeFirestoreClient) -> None:
        self._client = client

    def create(self, reference: _FakeFirestoreDocumentReference, data: dict[str, object]) -> None:
        if reference._key in self._client.documents:
            raise AssertionError("Fake Firestore create must not overwrite a document.")
        self._client.documents[reference._key] = data.copy()

    def set(self, reference: _FakeFirestoreDocumentReference, data: dict[str, object]) -> None:
        self._client.documents[reference._key] = data.copy()

    def delete(self, reference: _FakeFirestoreDocumentReference) -> None:
        self._client.documents.pop(reference._key, None)
