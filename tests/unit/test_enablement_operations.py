"""Offline tests for non-secret idempotent CMS enablement operation persistence."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from github_oauth_worker.enablement_operations import (
    EnablementOperation,
    EnablementOperationStatus,
    FirestoreEnablementOperationStore,
    InMemoryEnablementOperationStore,
    operation_document_id,
)

SHA_A = "a" * 40
SHA_B = "b" * 40


@pytest.mark.parametrize(
    "store_type", [InMemoryEnablementOperationStore, FirestoreEnablementOperationStore]
)
def test_operation_store_claims_one_write_and_retains_completed_pr(
    store_type: type[InMemoryEnablementOperationStore] | type[FirestoreEnablementOperationStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        now = datetime(2026, 9, 17, tzinfo=UTC)
        if store_type is FirestoreEnablementOperationStore:
            monkeypatch.setattr(
                "github_oauth_worker.enablement_operations.async_transactional",
                lambda operation: operation,
            )
            store = store_type(_FakeFirestoreClient(), now=lambda: now)
        else:
            store = store_type(now=lambda: now)
        planned = await store.put_planned(_operation(now))

        claim = await store.claim_writing(123, SHA_A)
        repeat_claim = await store.claim_writing(123, SHA_A)
        completed = await store.complete(123, SHA_A, 4, "https://github.com/owner/comic/pull/4")

        assert planned.status is EnablementOperationStatus.PLANNED
        assert claim.acquired
        assert not repeat_claim.acquired
        assert repeat_claim.operation.status is EnablementOperationStatus.WRITING
        assert completed.status is EnablementOperationStatus.COMPLETED
        assert completed.pull_request_number == 4
        assert (await store.get(123, SHA_A)) == completed

    asyncio.run(scenario())


def test_new_plan_refreshes_only_a_non_writing_operation() -> None:
    async def scenario() -> None:
        earlier = datetime(2026, 9, 17, tzinfo=UTC)
        later = earlier + timedelta(minutes=1)
        store = InMemoryEnablementOperationStore(now=lambda: later)
        await store.put_planned(_operation(earlier))
        refreshed = await store.put_planned(_operation(later, engine_commit_sha=SHA_B))

        assert refreshed.created_at == earlier
        assert refreshed.updated_at == later
        assert refreshed.engine_commit_sha == SHA_B

    asyncio.run(scenario())


def test_operation_document_key_has_only_immutable_identifiers() -> None:
    assert operation_document_id(123, SHA_A) == f"123-{SHA_A}"
    with pytest.raises(ValueError):
        operation_document_id(0, SHA_A)


def _operation(now: datetime, engine_commit_sha: str = SHA_B) -> EnablementOperation:
    return EnablementOperation.create(
        repository_id=123,
        base_commit_sha=SHA_A,
        engine_selector="cms",
        engine_commit_sha=engine_commit_sha,
        branch_name="comic-git/cms-enable/aaaaaaaaaaaa",
        now=now,
    )


class _FakeFirestoreClient:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str], dict[str, object]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self, name)

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)


class _FakeCollection:
    def __init__(self, client: _FakeFirestoreClient, name: str) -> None:
        self._client = client
        self._name = name

    def document(self, key: str) -> _FakeDocument:
        return _FakeDocument(self._client, (self._name, key))


class _FakeDocument:
    def __init__(self, client: _FakeFirestoreClient, key: tuple[str, str]) -> None:
        self._client = client
        self._key = key

    async def get(self, transaction: object | None = None) -> _FakeSnapshot:
        del transaction
        return _FakeSnapshot(self._client.documents.get(self._key))


class _FakeSnapshot:
    def __init__(self, data: dict[str, object] | None) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, object] | None:
        return self._data.copy() if self._data is not None else None


class _FakeTransaction:
    def __init__(self, client: _FakeFirestoreClient) -> None:
        self._client = client

    def create(self, reference: _FakeDocument, data: dict[str, object]) -> None:
        self._client.documents[reference._key] = data.copy()

    def set(self, reference: _FakeDocument, data: dict[str, object]) -> None:
        self._client.documents[reference._key] = data.copy()
