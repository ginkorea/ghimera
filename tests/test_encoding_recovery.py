"""Native SQLite crash/recovery contracts with local protocol doubles; no model quality claims."""

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from ghimera.corpus import EvidenceCorpus
from ghimera.embedding import SelfHostedEncoder
from ghimera.embedding_types import EncodingBatch, EncodingCall, EncodingIntent, encoding_request
from ghimera.model_http import ModelHttpResponse
from tests.test_evidence_corpus import config, harvest


class FixtureHttp:
    """In-memory model wire double: tests exercise the actual native encoder validation."""

    def __init__(self, policy, *, inspect=None, crash=False, wrong_model=False):
        self.config = policy
        self.inspect = inspect
        self.crash = crash
        self.wrong_model = wrong_model
        self.calls = 0

    async def post(self, body):
        self.calls += 1
        if self.inspect is not None:
            self.inspect(body)
        if self.crash:
            os._exit(23)
        request = json.loads(body)
        response = {
            "object": "list",
            "model": "wrong" if self.wrong_model else request["model"],
            "data": [
                {"object": "embedding", "index": i, "embedding": [1.0, 0.0]}
                for i in range(len(request["input"]))
            ],
            "usage": {"prompt_tokens": 4, "total_tokens": 4},
        }
        return ModelHttpResponse(200, json.dumps(response).encode(), "application/json")


def recovery_policy(tmp_path, **changes):
    recovery = {
        "schema": "ghimera.encoding-recovery/1",
        "max_calls": 10,
        "max_input_chars": 10000,
        "max_stored_bytes": 1000000,
    }
    recovery.update(changes)
    return config(tmp_path, 12345, encoding_recovery=recovery)


def open_corpus(policy, *, create=False, passage=None, query=None):
    passage = passage or FixtureHttp(policy.encoder)
    query = query or FixtureHttp(policy.query_encoder)
    return EvidenceCorpus(
        policy,
        encoder=SelfHostedEncoder(policy.encoder, http=passage),
        query_encoder=SelfHostedEncoder(policy.query_encoder, http=query),
        create=create,
    )


def run_crash(policy, material, point):
    program = """
import asyncio,json,os,sys
from ghimera.corpus_config import CorpusConfig
from ghimera.models import Harvest
from tests.test_encoding_recovery import FixtureHttp,open_corpus
raw=json.loads(sys.stdin.read())
policy=CorpusConfig.model_validate(raw['policy'])
material=Harvest.model_validate(raw['material'])
point=raw['point']
http=FixtureHttp(policy.encoder,crash=point=='before_ack')
store=open_corpus(policy,create=True,passage=http)
if point=='after_ack':
    def crash(*args): os._exit(31)
    store._storage.commit=crash
asyncio.run(store.append(material))
if point.startswith('query_'):
    from ghimera.embedding import SelfHostedEncoder
    store._query_encoder=SelfHostedEncoder(policy.query_encoder,http=FixtureHttp(
        policy.query_encoder,crash=point=='query_before_ack'))
    if point=='query_after_ack':
        def crash(*args): os._exit(37)
        store._storage.terminal=crash
    asyncio.run(store.search('ports',top_k=1))
"""
    return subprocess.run(
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


def test_exact_intent_is_durable_before_native_http_contact(tmp_path):
    policy = recovery_policy(tmp_path)

    def inspect(body):
        with sqlite3.connect(policy.directory / "corpus.sqlite") as db:
            payload, status = db.execute(
                "SELECT intent,status FROM encoding_invocations"
            ).fetchone()
        intent = EncodingIntent.model_validate_json(payload)
        assert status == "unknown" and intent.generation == 0
        assert intent.request_sha256 == hashlib.sha256(body).hexdigest()
        assert body == encoding_request(intent.service, intent.texts)
        assert intent.service == policy.encoder and intent.purpose == "passage"

    async def operation():
        http = FixtureHttp(policy.encoder, inspect=inspect)
        store = open_corpus(policy, create=True, passage=http)
        await store.append(await harvest(("zh", "港口")))
        state = store.encoding_recovery_state()
        assert state.reserved_calls == state.acknowledged == 1
        assert state.unresolved == 0
        store.close()

    asyncio.run(operation())


def test_process_death_unknown_refuses_replay_and_retains_lifetime_quota(tmp_path):
    policy = recovery_policy(tmp_path, max_calls=1)
    material = asyncio.run(harvest(("zh", "港口")))
    child = run_crash(policy, material, "before_ack")
    assert child.returncode == 23, child.stderr

    async def operation():
        http = FixtureHttp(policy.encoder)
        store = open_corpus(policy, passage=http)
        state = store.encoding_recovery_state()
        assert state.reserved_calls == state.unresolved == 1 and state.acknowledged == 0
        with pytest.raises(ValueError, match="reconciliation"):
            await store.append(material)
        with pytest.raises(ValueError, match="allowance exhausted"):
            await store.append(await harvest(("en", "another source")))
        assert http.calls == 0
        store.close()
        with pytest.raises(ValueError, match="cannot be disabled"):
            open_corpus(config(tmp_path, 12345))

    asyncio.run(operation())


def test_process_death_after_ack_recovers_exact_vectors_without_new_call(tmp_path):
    policy = recovery_policy(tmp_path, max_calls=1)
    material = asyncio.run(harvest(("zh", "港口")))
    child = run_crash(policy, material, "after_ack")
    assert child.returncode == 31, child.stderr
    with sqlite3.connect(policy.directory / "corpus.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        acknowledged = EncodingBatch.model_validate_json(
            db.execute("SELECT result FROM encoding_invocations").fetchone()[0]
        )

    async def operation():
        http = FixtureHttp(policy.encoder)
        store = open_corpus(policy, passage=http)
        receipt = await store.append(material)
        assert receipt.generation == 1 and receipt.encoding_calls == (acknowledged.call,)
        assert receipt.encoding_recovery[0].reused
        assert http.calls == 0
        _, _, vectors = store._storage.vectors()
        assert vectors == acknowledged.vectors
        assert store.encoding_recovery_state().reserved_calls == 1
        store.close()

    asyncio.run(operation())


def test_query_reuses_ack_locally_and_generation_is_part_of_intent(tmp_path):
    policy = recovery_policy(tmp_path)

    async def operation():
        query = FixtureHttp(policy.query_encoder)
        store = open_corpus(policy, create=True, query=query)
        await store.append(await harvest(("zh", "港口")))
        observations = []
        first = await store.search("ports", top_k=1, encoding_observer=observations.append)
        second = await store.search("ports", top_k=1, encoding_observer=observations.append)
        assert query.calls == 1 and len(observations) == 1
        assert first.model_dump(exclude={"encoding_recovery"}) == second.model_dump(
            exclude={"encoding_recovery"}
        )
        assert first.generation == 1 and not first.encoding_recovery.reused
        assert second.encoding_recovery.reused
        assert first.encoding_recovery.original_call_id == second.encoding_recovery.original_call_id
        store.close()
        store = open_corpus(policy, query=query)
        restored = await store.search("ports", top_k=1)
        assert restored == second
        assert query.calls == 1
        await store.append(await harvest(("en", "new source")))
        third = await store.search("ports", top_k=1)
        assert third.generation == 2 and query.calls == 2
        store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("point,exit_code", [("query_before_ack", 23), ("query_after_ack", 37)])
def test_query_process_death_preserves_snapshot_intent_and_ack(tmp_path, point, exit_code):
    policy = recovery_policy(tmp_path, max_calls=2)
    material = asyncio.run(harvest(("zh", "港口")))
    child = run_crash(policy, material, point)
    assert child.returncode == exit_code, child.stderr

    async def operation():
        query = FixtureHttp(policy.query_encoder)
        store = open_corpus(policy, query=query)
        state = store.encoding_recovery_state()
        assert state.reserved_calls == 2
        if point == "query_before_ack":
            assert state.unresolved == 1
            with pytest.raises(ValueError, match="reconciliation"):
                await store.search("ports", top_k=1)
        else:
            assert state.acknowledged == 2
            result = await store.search("ports", top_k=1)
            assert result.generation == 1 and result.encoding_recovery.reused
            assert result.hits[0].passage.source_sha256 == material.documents[0].sha256
        assert query.calls == 0
        store.close()

    asyncio.run(operation())


def test_ack_corruption_refuses_before_query_contact(tmp_path):
    policy = recovery_policy(tmp_path)

    async def operation():
        store = open_corpus(policy, create=True)
        await store.append(await harvest(("zh", "港口")))
        await store.search("ports", top_k=1)
        store.close()
        with sqlite3.connect(policy.directory / "corpus.sqlite") as db:
            db.execute("UPDATE encoding_invocations SET result=? WHERE call_id=2", (b"{}",))
        http = FixtureHttp(policy.query_encoder)
        store = open_corpus(policy, query=http)
        with pytest.raises(ValueError, match="acknowledgement changed"):
            await store.search("ports", top_k=1)
        assert http.calls == 0
        store.close()

    asyncio.run(operation())


def test_reopen_with_lower_allowance_refuses_and_policy_raise_keeps_prior_spend(tmp_path):
    policy = recovery_policy(tmp_path, max_calls=2)

    async def operation():
        store = open_corpus(policy, create=True)
        await store.append(await harvest(("zh", "港口")))
        await store.search("ports", top_k=1)
        store.close()
        with pytest.raises(ValueError, match="persisted encoding reservations"):
            open_corpus(recovery_policy(tmp_path, max_calls=1))
        store = open_corpus(recovery_policy(tmp_path, max_calls=3))
        assert store.encoding_recovery_state().reserved_calls == 2
        assert (await store.search("ports", top_k=1)).encoding_recovery.reused
        assert store.encoding_recovery_state().reserved_calls == 2
        store.close()

    asyncio.run(operation())


def test_observed_failure_never_becomes_reusable_ack_or_released_quota(tmp_path):
    policy = recovery_policy(tmp_path)

    async def operation():
        from ghimera.refusals import EncodingFailure

        material = await harvest(("zh", "港口"))
        http = FixtureHttp(policy.encoder, wrong_model=True)
        store = open_corpus(policy, create=True, passage=http)
        with pytest.raises(EncodingFailure):
            await store.append(material)
        with pytest.raises(ValueError, match="reconciliation"):
            await store.append(material)
        assert http.calls == 1 and store.encoding_recovery_state().reserved_calls == 1
        store.close()
        with sqlite3.connect(policy.directory / "corpus.sqlite") as db:
            status, result = db.execute("SELECT status,result FROM encoding_invocations").fetchone()
            assert status == "refused" and result is None
            call = EncodingCall.model_validate_json(
                db.execute("SELECT payload FROM calls").fetchone()[0]
            )
            assert call.outcome == "refused" and call.status == 200

    asyncio.run(operation())


def test_disabled_recipe_preserves_legacy_config_identity_and_schema(tmp_path):
    policy = config(tmp_path, 12345)
    legacy = policy.model_dump_json(exclude={"directory", "encoding_recovery"}).encode()
    assert policy.identity == hashlib.sha256(legacy).hexdigest()
    store = open_corpus(policy, create=True)
    assert store.encoding_recovery_state() is None
    assert not store._storage.db.execute(
        "SELECT 1 FROM sqlite_master WHERE name='encoding_invocations'"
    ).fetchone()
    store.close()


def test_ack_precedes_observer_failure_and_can_be_recovered_locally(tmp_path):
    policy = recovery_policy(tmp_path)

    def observer(call):
        raise RuntimeError("downstream accounting interrupted")

    async def operation():
        query = FixtureHttp(policy.query_encoder)
        store = open_corpus(policy, create=True, query=query)
        await store.append(await harvest(("zh", "港口")))
        with pytest.raises(RuntimeError, match="accounting interrupted"):
            await store.search("ports", top_k=1, encoding_observer=observer)
        store.close()
        store = open_corpus(policy, query=query)
        recovered = await store.search("ports", top_k=1, encoding_observer=observer)
        assert recovered.encoding_recovery.reused and query.calls == 1
        assert store.encoding_recovery_state().acknowledged == 2
        store.close()

    asyncio.run(operation())


@pytest.mark.parametrize("limits", [{"max_input_chars": 1}, {"max_stored_bytes": 1}])
def test_durable_character_and_result_capacity_refuse_before_contact(tmp_path, limits):
    policy = recovery_policy(tmp_path, **limits)

    async def operation():
        http = FixtureHttp(policy.encoder)
        store = open_corpus(policy, create=True, passage=http)
        with pytest.raises(ValueError, match="allowance exhausted"):
            await store.append(await harvest(("zh", "港口")))
        assert http.calls == 0 and store.encoding_recovery_state().reserved_calls == 0
        store.close()

    asyncio.run(operation())
