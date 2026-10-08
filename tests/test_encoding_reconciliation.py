"""Explicit native UNKNOWN decisions; real SQLite/process crashes, local wire doubles."""

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from ghimera.embedding import SelfHostedEncoder
from ghimera.embedding_types import EncodingIntent, encoding_request
from ghimera.encoding_reconciliation import EncodingReconciliationDecision
from tests.test_encoding_recovery import FixtureHttp, open_corpus, recovery_policy
from tests.test_evidence_corpus import config, harvest


def interrupted(body):
    raise RuntimeError("fixture outcome unknown")


def invocation_id(policy, purpose, store, texts):
    service = policy.encoder if purpose == "passage" else policy.query_encoder
    return EncodingIntent(
        schema="ghimera.encoding-intent/1",
        corpus_id=store.identity,
        generation=store._storage.generation(),
        purpose=purpose,
        service=service,
        texts=texts,
        request_sha256=hashlib.sha256(encoding_request(service, texts)).hexdigest(),
    ).identity


def decision(store, invocation, reason="caller abandons uncertain fixture outcome"):
    observed = store.observe_encoding_invocation(invocation)
    return EncodingReconciliationDecision(
        schema="ghimera.encoding-reconciliation/1",
        action="abandon_and_authorize_new_attempt",
        caller="owned native test caller",
        reason=reason,
        observed=observed,
        observed_sha256=observed.sha256,
    )


async def unknown_append(policy):
    material = await harvest(("zh", "港口"))
    wire = FixtureHttp(policy.encoder, inspect=interrupted)
    store = open_corpus(policy, create=True, passage=wire)
    with pytest.raises(RuntimeError, match="outcome unknown"):
        await store.append(material)
    identity = invocation_id(policy, "passage", store, ("港口",))
    return store, material, identity


async def unknown_query(policy):
    material = await harvest(("zh", "港口"))
    wire = FixtureHttp(policy.query_encoder, inspect=interrupted)
    store = open_corpus(policy, create=True, query=wire)
    await store.append(material)
    with pytest.raises(RuntimeError, match="outcome unknown"):
        await store.search("ports", top_k=1)
    identity = invocation_id(policy, "query", store, ("ports",))
    return store, material, identity


def test_append_decision_and_separate_charge_are_atomic_then_one_shot(tmp_path):
    policy = recovery_policy(tmp_path, max_calls=2)

    async def operation():
        store, material, original_id = await unknown_append(policy)
        original = store.observe_encoding_invocation(original_id)
        assert original.status == "unknown" and original.admission.configuration == policy
        chosen = decision(store, original_id)
        authorized = store.reconcile_encoding(chosen)
        assert (
            store.reconcile_encoding(chosen) == authorized
        )  # Exact receipt retry adds no attempt.
        state = store.encoding_recovery_state()
        assert state.reserved_calls == state.unresolved == 2
        assert state.reserved_input_chars == 2 * original.intent.input_chars
        retained = store.observe_encoding_invocation(original_id)
        assert retained.status == "unknown" and retained.reserved_bytes == original.reserved_bytes
        fresh = store.observe_encoding_invocation(authorized.invocation_sha256)
        assert fresh.status == "unknown" and fresh.attempt_consumed is False
        assert fresh.admission == original.admission
        assert store.encoding_reconciliation_history() == (chosen,)
        store.close()

        def inspect(body):
            with sqlite3.connect(policy.directory / "corpus.sqlite") as db:
                rows = db.execute(
                    "SELECT id,status FROM encoding_invocations ORDER BY rowid"
                ).fetchall()
                consumed = db.execute("SELECT consumed FROM encoding_decisions").fetchone()[0]
            assert rows == [(original_id, "unknown"), (authorized.invocation_sha256, "unknown")]
            assert consumed == 1

        wire = FixtureHttp(policy.encoder, inspect=inspect)
        store = open_corpus(policy, passage=wire)
        with pytest.raises(ValueError, match="reconciliation"):
            await store.append(material)
        assert wire.calls == 0
        receipt = await store.append(material, encoding_authorizations=(authorized,))
        assert wire.calls == 1 and receipt.generation == 1
        assert receipt.encoding_recovery[0].invocation_sha256 == authorized.invocation_sha256
        state = store.encoding_recovery_state()
        assert state.reserved_calls == 2 and state.unresolved == state.acknowledged == 1
        assert store.observe_encoding_invocation(original_id).status == "unknown"
        assert store.encoding_reconciliation_history() == (chosen,)
        _, _, vectors = store._storage.vectors()
        assert vectors == ((1.0, 0.0),)
        store.close()

    asyncio.run(operation())


def test_query_authorization_reuses_its_own_ack_without_erasing_unknown(tmp_path):
    policy = recovery_policy(tmp_path, max_calls=3)

    async def operation():
        store, material, original = await unknown_query(policy)
        chosen = decision(store, original)
        authorized = store.reconcile_encoding(chosen)
        store.close()
        query = FixtureHttp(policy.query_encoder)
        store = open_corpus(policy, query=query)
        observations = []
        first = await store.search(
            "ports",
            top_k=1,
            encoding_authorization=authorized,
            encoding_observer=observations.append,
        )
        second = await store.search(
            "ports",
            top_k=1,
            encoding_authorization=authorized,
            encoding_observer=observations.append,
        )
        assert query.calls == 1 and len(observations) == 1
        assert first.encoding_call == second.encoding_call
        assert not first.encoding_recovery.reused and second.encoding_recovery.reused
        assert second.encoding_recovery.invocation_sha256 == authorized.invocation_sha256
        assert second.hits[0].passage.source_sha256 == material.documents[0].sha256
        assert store.observe_encoding_invocation(original).status == "unknown"
        assert store.encoding_recovery_state().reserved_calls == 3
        with pytest.raises(ValueError, match="reconciliation"):
            await store.search("ports", top_k=1)
        assert query.calls == 1
        store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("limit", ["calls", "chars", "bytes"])
def test_original_remaining_quota_cannot_be_extended_by_decision(tmp_path, limit):
    limits = (
        {"max_calls": 1}
        if limit == "calls"
        else {"max_input_chars": 2}
        if limit == "chars"
        else {"max_stored_bytes": 30000}
    )
    policy = recovery_policy(tmp_path, **limits)

    async def operation():
        store, _, original = await unknown_append(policy)
        before = store.encoding_recovery_state()
        with pytest.raises(ValueError, match="allowance exhausted"):
            store.reconcile_encoding(decision(store, original))
        assert store.encoding_recovery_state() == before
        assert not store.encoding_reconciliation_history()
        assert store.observe_encoding_invocation(original).status == "unknown"
        store.close()

    asyncio.run(operation())


def test_later_policy_raise_is_compatible_normally_but_never_original_reconciliation_quota(
    tmp_path,
):
    policy = recovery_policy(tmp_path, max_calls=2)

    async def operation():
        store, material, original = await unknown_append(policy)
        chosen = decision(store, original)
        store.close()
        raised = recovery_policy(tmp_path, max_calls=10)
        wire = FixtureHttp(raised.encoder)
        store = open_corpus(raised, passage=wire)
        with pytest.raises(ValueError, match="configuration drift"):
            store.reconcile_encoding(chosen)
        with pytest.raises(ValueError, match="configuration drift"):
            store.reconcile_encoding(decision(store, original))
        assert wire.calls == 0 and store.encoding_recovery_state().reserved_calls == 1
        await store.append(await harvest(("en", "distinct material")))
        assert wire.calls == 1  # Existing ordinary policy updates remain supported.
        store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("field", ["service", "corpus", "generation", "input", "observed_digest"])
def test_foreign_or_changed_decision_binding_is_denied_before_contact(tmp_path, field):
    policy = recovery_policy(tmp_path)

    async def operation():
        store, _, original = await unknown_append(policy)
        selected = decision(store, original)
        raw = selected.model_dump()
        observed = raw["observed"]
        if field == "observed_digest":
            raw["observed_sha256"] = "0" * 64
        else:
            intent = observed["intent"]
            if field == "service":
                intent["service"]["model_id"] = "foreign-model"
            elif field == "corpus":
                intent["corpus_id"] = "0" * 32
            elif field == "generation":
                intent["generation"] = 1
            else:
                intent["texts"] = ("foreign",)
                from ghimera.corpus_storage import digest

                intent["request_sha256"] = digest(encoding_request(policy.encoder, ("foreign",)))
            from ghimera.encoding_reconciliation import EncodingInvocationObservation

            raw["observed_sha256"] = EncodingInvocationObservation.model_validate(observed).sha256
        wire = FixtureHttp(policy.encoder)
        store._encoder = SelfHostedEncoder(policy.encoder, http=wire)
        with pytest.raises(ValueError):
            store.reconcile_encoding(EncodingReconciliationDecision.model_validate(raw))
        assert wire.calls == 0 and store.encoding_recovery_state().reserved_calls == 1
        assert not store.encoding_reconciliation_history()
        store.close()

    asyncio.run(operation())


def test_stale_decision_and_authorization_input_generation_drift_refuse(tmp_path):
    policy = recovery_policy(tmp_path)

    async def operation():
        store, material, original = await unknown_query(policy)
        first = decision(store, original)
        stale = decision(store, original, reason="another caller decision on the old observation")
        authorized = store.reconcile_encoding(first)
        with pytest.raises(ValueError, match="stale"):
            store.reconcile_encoding(stale)
        query = FixtureHttp(policy.query_encoder)
        from ghimera.embedding import SelfHostedEncoder

        store._query_encoder = SelfHostedEncoder(policy.query_encoder, http=query)
        with pytest.raises(ValueError, match="exact requested intent"):
            await store.search("different", top_k=1, encoding_authorization=authorized)
        await store.append(await harvest(("en", "new generation")))
        with pytest.raises(ValueError, match="drift"):
            store.reconcile_encoding(first)
        with pytest.raises(ValueError, match="exact requested intent"):
            await store.search("ports", top_k=1, encoding_authorization=authorized)
        assert query.calls == 0
        store.close()

    asyncio.run(operation())


def test_inflight_native_query_holds_writer_against_concurrent_decision(tmp_path):
    policy = recovery_policy(tmp_path)

    async def operation():
        store = open_corpus(policy, create=True)
        await store.append(await harvest(("zh", "港口")))
        entered, release = asyncio.Event(), asyncio.Event()

        class PendingWire(FixtureHttp):
            async def post(self, body):
                entered.set()
                await release.wait()
                return await super().post(body)

        from ghimera.embedding import SelfHostedEncoder

        store._query_encoder = SelfHostedEncoder(
            policy.query_encoder, http=PendingWire(policy.query_encoder)
        )
        original = invocation_id(policy, "query", store, ("ports",))
        task = asyncio.create_task(store.search("ports", top_k=1))
        await entered.wait()
        competitor = open_corpus(policy)
        with pytest.raises(BlockingIOError):
            competitor.observe_encoding_invocation(original)
        release.set()
        await task
        assert competitor.observe_encoding_invocation(original).status == "acknowledged"
        competitor.close()
        store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("point", ["after_decision", "after_consumption", "after_ack"])
def test_real_process_death_decision_receipt_and_consumption_survive_reopen(tmp_path, point):
    policy = recovery_policy(tmp_path, max_calls=2)
    material = asyncio.run(harvest(("zh", "港口")))
    program = """
import asyncio,json,os,sys
from ghimera.corpus_config import CorpusConfig
from ghimera.models import Harvest
from tests.test_encoding_recovery import FixtureHttp,open_corpus
from tests.test_encoding_reconciliation import interrupted,invocation_id,decision
raw=json.loads(sys.stdin.read())
policy=CorpusConfig.model_validate(raw['policy'])
material=Harvest.model_validate(raw['material'])
async def main():
    store=open_corpus(policy,create=True,passage=FixtureHttp(policy.encoder,inspect=interrupted))
    try: await store.append(material)
    except RuntimeError: pass
    original=invocation_id(policy,'passage',store,('港口',))
    authorized=store.reconcile_encoding(decision(store,original))
    if raw['point']=='after_decision': os._exit(41)
    from ghimera.embedding import SelfHostedEncoder
    store._encoder=SelfHostedEncoder(policy.encoder,http=FixtureHttp(policy.encoder,crash=raw['point']=='after_consumption'))
    if raw['point']=='after_ack':
        def crash(*args): os._exit(47)
        store._storage.commit=crash
    await store.append(material,encoding_authorizations=(authorized,))
asyncio.run(main())
"""
    child = subprocess.run(
        [sys.executable, "-c", program],
        input=json.dumps(
            {
                "policy": policy.model_dump(mode="json"),
                "material": material.model_dump(mode="json"),
                "point": point,
            }
        ),
        text=True,
        capture_output=True,
        timeout=20,
        env=os.environ.copy(),
    )
    assert (
        child.returncode == {"after_decision": 41, "after_consumption": 23, "after_ack": 47}[point]
    ), child.stderr

    async def operation():
        wire = FixtureHttp(policy.encoder)
        store = open_corpus(policy, passage=wire)
        (chosen,) = store.encoding_reconciliation_history()
        authorized = store.reconcile_encoding(chosen)
        assert store.encoding_recovery_state().reserved_calls == 2
        if point == "after_decision":
            await store.append(material, encoding_authorizations=(authorized,))
            assert wire.calls == 1
        elif point == "after_ack":
            recovered = await store.append(material, encoding_authorizations=(authorized,))
            assert wire.calls == 0 and recovered.encoding_recovery[0].reused
            assert recovered.encoding_recovery[0].invocation_sha256 == authorized.invocation_sha256
        else:
            with pytest.raises(ValueError, match="was consumed"):
                await store.append(material, encoding_authorizations=(authorized,))
            assert wire.calls == 0 and store.encoding_recovery_state().unresolved == 2
        assert (
            store.observe_encoding_invocation(chosen.observed.invocation_sha256).status == "unknown"
        )
        assert store.encoding_reconciliation_history() == (chosen,)
        store.close()

    asyncio.run(operation())


def test_concurrent_native_decisions_create_one_charged_attempt(tmp_path):
    policy = recovery_policy(tmp_path, max_calls=2)

    async def prepare():
        store, _, original = await unknown_append(policy)
        selected = decision(store, original)
        store.close()
        return selected

    selected = asyncio.run(prepare())
    barrier = threading.Barrier(2)

    def decide(caller):
        store = open_corpus(policy)
        try:
            barrier.wait(timeout=5)
            competing = selected.model_copy(update={"caller": caller})
            try:
                return store.reconcile_encoding(competing)
            except (ValueError, BlockingIOError) as exc:
                return exc
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(decide, ("caller one", "caller two")))
    assert sum(not isinstance(value, Exception) for value in results) == 1
    store = open_corpus(policy)
    assert store.encoding_recovery_state().reserved_calls == 2
    assert len(store.encoding_reconciliation_history()) == 1
    store.close()


def test_legacy_unknown_missing_original_admission_stays_refused(tmp_path):
    policy = recovery_policy(tmp_path)

    async def operation():
        store, _, original = await unknown_append(policy)
        store.close()
        # An old native row has no admission record; no policy is backfilled at open.
        with sqlite3.connect(policy.directory / "corpus.sqlite") as db:
            db.execute("DELETE FROM encoding_admissions")
        store = open_corpus(policy)
        observed = store.observe_encoding_invocation(original)
        assert observed.admission is None and observed.status == "unknown"
        with pytest.raises(ValueError, match="quota is unavailable"):
            store.reconcile_encoding(decision(store, original))
        assert store.encoding_recovery_state().reserved_calls == 1
        assert not store.encoding_reconciliation_history()
        store.close()

    asyncio.run(operation())


def test_disabled_legacy_does_not_gain_admission_or_decision_tables(tmp_path):
    policy = config(tmp_path, 12345)
    store = open_corpus(policy, create=True)
    for table in ("encoding_admissions", "encoding_decisions"):
        assert not store._storage.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (table,)
        ).fetchone()
    with pytest.raises(ValueError, match="not configured"):
        store.observe_encoding_invocation("0" * 64)
    store.close()


@pytest.mark.parametrize("target", ["original", "authorized_attempt"])
def test_coherently_changed_unknown_intent_cannot_retain_its_invocation_key(tmp_path, target):
    policy = recovery_policy(tmp_path)

    async def operation():
        store, material, original = await unknown_append(policy)
        chosen = decision(store, original)
        authorized = store.reconcile_encoding(chosen) if target == "authorized_attempt" else None
        key = original if authorized is None else authorized.invocation_sha256
        observed = store.observe_encoding_invocation(key)
        changed = observed.intent.model_dump()
        changed["texts"] = ("港湾",)  # Same chars/byte count; fully coherent changed wire digest.
        changed["request_sha256"] = hashlib.sha256(
            encoding_request(policy.encoder, ("港湾",))
        ).hexdigest()
        corrupted = EncodingIntent.model_validate(changed)
        store.close()
        with sqlite3.connect(policy.directory / "corpus.sqlite") as db:
            db.execute(
                "UPDATE encoding_invocations SET intent=? WHERE id=?",
                (corrupted.model_dump_json().encode(), key),
            )
        wire = FixtureHttp(policy.encoder)
        store = open_corpus(policy, passage=wire)
        with pytest.raises(ValueError, match="identity does not bind"):
            store.observe_encoding_invocation(key)
        if authorized is None:
            with pytest.raises(ValueError, match="identity does not bind"):
                store.reconcile_encoding(chosen)
        else:
            with pytest.raises(ValueError, match="identity does not bind"):
                await store.append(material, encoding_authorizations=(authorized,))
        assert wire.calls == 0
        store.close()

    asyncio.run(operation())
