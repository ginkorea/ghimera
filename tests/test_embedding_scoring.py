"""Real bounded HTTP embedding protocol fixtures, not model-quality evidence."""

import asyncio
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.embedding import SelfHostedEncoder
from ghimera.embedding_types import EmbeddingReferences, unit_vector
from ghimera.fetch import FetchLadder
from ghimera.ledger import Ledger
from ghimera.loop import GoalLoop
from ghimera.model_config import EmbeddingServiceConfig
from ghimera.model_http import ModelHttpResponse
from ghimera.models import Extracted, Goal, Harvest, LinkCandidate, Scope
from ghimera.refusals import EncodingCancelled, EncodingFailure, GhimeraRefused
from ghimera.scoring_config import ScoringConfig
from ghimera.semantic_scoring import EmbeddingScorer


def service(port, **changes):
    raw = {
        "schema": "chimera.embedding-service/1",
        "endpoint": f"http://127.0.0.1:{port}/v1/embeddings",
        "approved_addresses": ["127.0.0.1"],
        "allow_plaintext": True,
        "allow_plaintext_credentials": False,
        "authorization": "none",
        "model_id": "encoding-protocol-fixture",
        "revision": "fixture-1",
        "served_model": "fixture-encoder",
        "timeout_seconds": 2.0,
        "max_request_bytes": 20000,
        "max_response_bytes": 20000,
        "max_header_bytes": 8192,
        "require_usage": True,
        "max_input_chars": 1000,
        "dimensions": 2,
        "request_dimensions": False,
        "max_batch_texts": 2,
        "max_text_chars": 300,
        "text_prefix": "",
    }
    raw.update(changes)
    return EmbeddingServiceConfig.model_validate(raw)


def references(cfg, **changes):
    raw = {
        "schema": "chimera.embedding-references/1",
        "model_id": cfg.model_id,
        "revision": cfg.revision,
        "text_prefix": cfg.text_prefix,
        "dimensions": cfg.dimensions,
        "chunks": [{"source_id": "fixture:shelf", "text_sha256": "a" * 64, "vector": [1.0, 0.0]}],
    }
    raw.update(changes)
    return EmbeddingReferences.model_validate(raw)


def policy(cfg, refs, **changes):
    raw = {
        "schema": "chimera.scoring/1",
        "encoder": cfg,
        "references_sha256": refs.sha256,
        "encoding_call_budget": 30,
        "encoding_char_budget": 20000,
        "window_chars": 100,
        "overlap_chars": 10,
        "max_windows": 3,
        "max_links": 10,
        "max_reference_chunks": 20,
        "max_anchor_chars": 30,
        "keyword_weight": 0.2,
    }
    raw.update(changes)
    return ScoringConfig.model_validate(raw)


def run_config(scoring):
    return GhimeraConfig.model_validate(
        dict(GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(), scoring=scoring)
    )


def vector_for(text):
    if "港口" in text or "ports" in text:
        return [1.0, 0.0]
    if "opposite" in text:
        return [-1.0, 0.0]
    return [0.0, 1.0]


@pytest.fixture
def endpoint():
    seen = []
    state = {"failure": None, "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            request = json.loads(raw)
            seen.append((self.path, request, self.headers.get("Authorization"), raw))
            data = [
                {"object": "embedding", "index": index, "embedding": vector_for(text)}
                for index, text in enumerate(request["input"])
            ]
            wire = {
                "object": "list",
                "model": "fixture-encoder",
                "data": list(reversed(data)),
                "usage": {"prompt_tokens": 4, "total_tokens": 4},
            }
            failure = state["failure"]
            if failure == "wrong_model":
                wire["model"] = "another-model"
            elif failure == "missing_usage":
                wire.pop("usage")
            elif failure == "bad_usage":
                wire["usage"]["total_tokens"] = 5
            elif failure == "missing_vector":
                wire["data"].pop()
            elif failure == "duplicate_index":
                wire["data"][-1]["index"] = wire["data"][0]["index"]
            elif failure == "wrong_dimension":
                wire["data"][0]["embedding"] = [1.0, 0.0, 0.0]
            elif failure == "zero":
                wire["data"][0]["embedding"] = [0.0, 0.0]
            elif failure == "nonfinite":
                wire["data"][0]["embedding"] = [float("nan"), 0.0]
            elif failure == "oversized":
                wire["padding"] = "x" * 40000
            response = b"{" if failure == "truncated" else json.dumps(wire).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            if state["status"] == 302:
                self.send_header("Location", "/other-encoder")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, seen, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_encoder_orders_by_input_index_and_binds_native_inputs(endpoint):
    encoder = SelfHostedEncoder(
        service(endpoint[0], text_prefix="passage: ", request_dimensions=True)
    )
    native = ("港口", "unrelated")
    result = asyncio.run(encoder.encode_batch(native))
    assert result.vectors == ((1.0, 0.0), (0.0, 1.0))
    request = endpoint[1][0]
    assert request[0] == "/v1/embeddings" and request[2] is None
    assert request[1] == {
        "model": "fixture-encoder",
        "input": ["passage: 港口", "passage: unrelated"],
        "encoding_format": "float",
        "dimensions": 2,
    }
    assert result.call.request_sha256 == hashlib.sha256(request[3]).hexdigest()
    assert result.call.input_sha256 == tuple(
        hashlib.sha256(("passage: " + text).encode()).hexdigest() for text in native
    )
    assert result.call.usage.total_tokens == 4
    assert result.call.service == encoder.config


@pytest.mark.parametrize(
    "failure",
    [
        "wrong_model",
        "missing_usage",
        "bad_usage",
        "missing_vector",
        "duplicate_index",
        "wrong_dimension",
        "zero",
        "nonfinite",
        "truncated",
        "oversized",
        "redirect",
    ],
)
def test_encoder_refusals_preserve_bounded_call_evidence(endpoint, failure):
    if failure == "redirect":
        endpoint[2]["status"] = 302
    else:
        endpoint[2]["failure"] = failure
    with pytest.raises(EncodingFailure) as refused:
        asyncio.run(SelfHostedEncoder(service(endpoint[0])).encode_batch(("港口", "other")))
    call = refused.value.call
    assert call.outcome == "refused" and call.telemetry == "observed"
    assert call.request_sha256 == hashlib.sha256(endpoint[1][0][3]).hexdigest()
    assert call.response_bytes <= 20000 and len(endpoint[1]) == 1


@pytest.mark.parametrize(
    "changes,texts",
    [
        ({"max_batch_texts": 1}, ("ports", "other")),
        ({"max_text_chars": 3}, ("ports",)),
        ({"max_input_chars": 5}, ("ports", "other")),
        ({"max_request_bytes": 2}, ("ports",)),
    ],
)
def test_encoder_limits_refuse_before_io(endpoint, changes, texts):
    with pytest.raises(EncodingFailure, match="budget_exhausted") as refused:
        asyncio.run(SelfHostedEncoder(service(endpoint[0], **changes)).encode_batch(texts))
    assert not endpoint[1] and refused.value.call.response_bytes == 0


def test_encoder_policy_and_rebinding_are_fail_closed(endpoint):
    for changes in (
        {"endpoint": "https://public.example/v1/embeddings", "approved_addresses": ["8.8.8.8"]},
        {"approved_addresses": ["169.254.169.254"]},
        {"endpoint": "http://127.0.0.1/v1/chat/completions"},
        {"endpoint": "http://127.0.0.1/v1/embeddings?secret=x"},
        {"approved_addresses": ["10.0.0.1"]},
    ):
        with pytest.raises(ValidationError):
            service(endpoint[0], **changes)
    with pytest.raises(ValueError, match="credential requires HTTPS"):
        SelfHostedEncoder(
            service(endpoint[0], authorization="bearer"), credential=SecretStr("fixture")
        )
    cfg = service(endpoint[0], authorization="bearer", allow_plaintext_credentials=True)
    result = asyncio.run(
        SelfHostedEncoder(cfg, credential=SecretStr("fixture-only")).encode_batch(("港口",))
    )
    assert endpoint[1][0][2] == "Bearer fixture-only"
    assert "fixture-only" not in result.model_dump_json()


def test_semantic_scorer_binds_shelf_native_offsets_and_shared_spend(endpoint):
    service_cfg = service(endpoint[0])
    refs = references(service_cfg)
    scoring = policy(service_cfg, refs, max_windows=2)
    budget, ledger = RunBudget(run_config(scoring), lambda: 0.0), Ledger()
    document = Extracted(
        title="native",
        text="noise " * 50 + "港口 ports evidence",
        language="zh",
        links=(
            LinkCandidate(url="https://example.org/a", anchor="other"),
            LinkCandidate(url="https://example.org/b", anchor="港口"),
            LinkCandidate(url="https://example.org/c", anchor="ports"),
            LinkCandidate(url="https://example.org/d", anchor="opposite"),
        ),
    )
    before = document.model_dump_json()
    ranked = asyncio.run(
        EmbeddingScorer(scoring, SelfHostedEncoder(service_cfg), refs).score(
            Goal(text="ports"), document, budget, ledger
        )
    )
    assert document.model_dump_json() == before
    assert [item.url for item in ranked] == [
        "https://example.org/c",
        "https://example.org/b",
        "https://example.org/a",
        "https://example.org/d",
    ]
    assert [item.score for item in ranked] == [1.0, 0.8, 0.0, 0.0]
    observed = ledger.snapshot()
    calls = [row.encoding_call for row in observed if row.encoding_call is not None]
    assert budget.encoding_calls == len(calls) == len(endpoint[1]) == 3
    assert budget.encoding_chars == sum(call.input_chars for call in calls)
    evidence = observed[-1].similarity
    assert evidence.document_cosine == 1.0 and evidence.omitted_chars > 0
    assert evidence.references_sha256 == refs.sha256
    for window in evidence.windows:
        assert (
            window.text_sha256
            == hashlib.sha256(document.text[window.start : window.end].encode()).hexdigest()
        )


def test_semantic_call_budget_reserves_before_spend_and_keeps_partial_ledger(endpoint):
    cfg = service(endpoint[0], max_batch_texts=1)
    refs = references(cfg)
    scoring = policy(cfg, refs, encoding_call_budget=1, max_windows=1)
    budget, ledger = RunBudget(run_config(scoring), lambda: 0.0), Ledger()
    document = Extracted(
        title="native",
        text="ports",
        language="en",
        links=(LinkCandidate(url="https://example.org/ports"),),
    )
    with pytest.raises(GhimeraRefused, match="budget_exhausted"):
        asyncio.run(
            EmbeddingScorer(scoring, SelfHostedEncoder(cfg), refs).score(
                Goal(text="ports"), document, budget, ledger
            )
        )
    assert budget.encoding_calls == len(endpoint[1]) == len(ledger.snapshot()) == 1


def test_semantic_failure_is_accounted_and_does_not_fall_back_to_keywords(endpoint):
    cfg = service(endpoint[0])
    refs, scoring = references(cfg), policy(cfg, references(cfg))
    config = run_config(scoring)
    endpoint[2]["failure"] = "wrong_model"
    loop = GoalLoop(
        config=config,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=EmbeddingScorer(scoring, SelfHostedEncoder(cfg), refs),
        judge=FakeJudge(),
    )
    result = asyncio.run(
        loop.run(
            Goal(text="ports", seeds=("https://example.org",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
    )
    assert result.receipt.stop_reason == "failed"
    assert result.receipt.judge_calls == 0 and result.receipt.encoding_calls == 1
    assert not result.documents
    assert next(row for row in result.ledger if row.encoding_call).refusal is not None
    assert Harvest.model_validate_json(result.model_dump_json()) == result


def test_semantic_full_loop_keeps_judge_and_validates_receipt_readback(endpoint):
    cfg = service(endpoint[0])
    refs, scoring = references(cfg), policy(cfg, references(cfg))
    config = run_config(scoring)
    with pytest.raises(ValueError, match="keyword-only"):
        GoalLoop(
            config=config,
            fetcher=FetchLadder((FakeRoute(),)),
            extractor=FakeExtractor(),
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        )
    loop = GoalLoop(
        config=config,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=EmbeddingScorer(scoring, SelfHostedEncoder(cfg), refs),
        judge=FakeJudge(),
    )
    result = asyncio.run(
        loop.run(
            Goal(text="ports", seeds=("https://example.org",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
    )
    assert result.documents and result.receipt.encoding_calls > 0 and result.receipt.judge_calls > 0
    assert Harvest.model_validate_json(result.model_dump_json()) == result
    raw = result.model_dump()
    raw["receipt"]["encoding_chars"] += 1
    with pytest.raises(ValidationError, match="encoding spend"):
        Harvest.model_validate(raw)
    raw = result.model_dump()
    raw["receipt"]["effective_config"]["scoring"]["references_sha256"] = "b" * 64
    with pytest.raises(ValidationError, match="reference vectors"):
        Harvest.model_validate(raw)
    raw = result.model_dump()
    observed = next(row for row in raw["ledger"] if row["similarity"])
    observed["similarity"]["links"][0]["score"] = 0.123
    with pytest.raises(ValidationError, match="frontier scores"):
        Harvest.model_validate(raw)
    raw = result.model_dump()
    next(row for row in raw["ledger"] if row["similarity"])["similarity"]["windows"][0][
        "text_sha256"
    ] = "c" * 64
    with pytest.raises(ValidationError, match="native spans"):
        Harvest.model_validate(raw)


def test_shelf_identity_dimensions_prefix_and_digest_cannot_be_rebound(endpoint):
    cfg = service(endpoint[0])
    refs = references(cfg)
    scorer = EmbeddingScorer(policy(cfg, refs), SelfHostedEncoder(cfg), refs)
    with pytest.raises(ValueError, match="effective policy"):
        scorer.validate_config(run_config(policy(cfg, refs, keyword_weight=0.9)))
    with pytest.raises(ValueError, match="shelf vectors"):
        EmbeddingScorer(
            policy(cfg, refs), SelfHostedEncoder(cfg), references(cfg, revision="other")
        )
    with pytest.raises(ValueError, match="shelf vectors"):
        EmbeddingScorer(
            policy(cfg, refs), SelfHostedEncoder(cfg), references(cfg, text_prefix="different")
        )
    with pytest.raises(ValidationError):
        references(cfg, dimensions=3)
    assert unit_vector((1e308, 1e308)) == pytest.approx((2**-0.5, 2**-0.5))
    with pytest.raises(ValueError):
        unit_vector((0.0, 0.0))


def test_encoding_cancellation_preserves_reserved_spend_and_partial_evidence():
    cfg = service(1)
    refs, scoring = references(cfg), policy(cfg, references(cfg), max_windows=1)

    class CancelledWire:
        config = cfg

        async def post(self, body):
            from ghimera.model_http import ModelWireCancelled

            raise ModelWireCancelled(ModelHttpResponse(200, b"partial-vector", "application/json"))

    budget, ledger = RunBudget(run_config(scoring), lambda: 0.0), Ledger()
    with pytest.raises(EncodingCancelled):
        asyncio.run(
            EmbeddingScorer(scoring, SelfHostedEncoder(cfg, http=CancelledWire()), refs).score(
                Goal(text="ports"),
                Extracted(title="x", text="ports", language="en"),
                budget,
                ledger,
            )
        )
    assert budget.encoding_calls == len(ledger.snapshot()) == 1
    assert ledger.snapshot()[0].encoding_call.outcome == "cancelled"
    assert ledger.snapshot()[0].encoding_call.response_bytes == len(b"partial-vector")


def test_injected_encoder_cannot_bypass_batch_validation(endpoint):
    cfg = service(endpoint[0])
    refs, scoring = references(cfg), policy(cfg, references(cfg), max_windows=1)

    class PoisonedEncoder(SelfHostedEncoder):
        async def encode_batch(self, texts):
            result = await super().encode_batch(texts)
            return result.model_copy(update={"vectors": ((0.0, 0.0),)})

    budget, ledger = RunBudget(run_config(scoring), lambda: 0.0), Ledger()
    with pytest.raises(GhimeraRefused, match="adapter_contract"):
        asyncio.run(
            EmbeddingScorer(scoring, PoisonedEncoder(cfg), refs).score(
                Goal(text="ports"),
                Extracted(title="x", text="ports", language="en"),
                budget,
                ledger,
            )
        )
    assert budget.encoding_calls == len(ledger.snapshot()) == 1
    assert ledger.snapshot()[0].encoding_call.outcome == "refused"


def test_scoring_example_is_explicit_and_matches_its_fixture_reference():
    import tomllib

    with Path("examples/scoring.toml").open("rb") as stream:
        parsed = ScoringConfig.model_validate(tomllib.load(stream))
    refs = EmbeddingReferences.model_validate_json(
        Path("examples/embedding-references.fixture.json").read_bytes()
    )
    assert parsed.references_sha256 == refs.sha256
    assert parsed.encoder.model_id == refs.model_id
    assert parsed.encoder.revision == refs.revision


def intent_policy(cfg, **changes):
    return policy(
        cfg, references(cfg), reference_source="intent", references_sha256=None, **changes
    )


def test_intent_mode_is_explicit_and_preserves_pinned_serialization(endpoint):
    cfg = service(endpoint[0])
    refs = references(cfg)
    pinned = policy(cfg, refs)
    assert "reference_source" not in pinned.model_dump()
    assert pinned.model_dump()["references_sha256"] == refs.sha256
    intent = intent_policy(cfg)
    assert intent.model_dump()["reference_source"] == "intent"
    assert "references_sha256" not in intent.model_dump()
    assert ScoringConfig.model_validate_json(intent.model_dump_json()) == intent
    with pytest.raises(ValidationError, match="pinned scoring"):
        policy(cfg, refs, references_sha256=None)
    with pytest.raises(ValidationError, match="intent scoring"):
        policy(cfg, refs, reference_source="intent")
    with pytest.raises(ValueError, match="prepares its own"):
        EmbeddingScorer(intent, SelfHostedEncoder(cfg), refs)
    with pytest.raises(ValueError, match="requires shelf vectors"):
        EmbeddingScorer(pinned, SelfHostedEncoder(cfg))
    assert not endpoint[1]


def test_intent_is_encoded_once_with_original_unicode_and_prefix(endpoint):
    cfg = service(endpoint[0], text_prefix="query: ")
    scoring = intent_policy(cfg, max_windows=1)
    budget, ledger = RunBudget(run_config(scoring), lambda: 0.0), Ledger()
    scorer = EmbeddingScorer(scoring, SelfHostedEncoder(cfg))
    goal = Goal(text="港口 ports")
    doc = Extracted(title="native", text="港口 evidence", language="zh")

    async def run():
        await scorer.score(goal, doc, budget, ledger)
        await scorer.score(goal, doc, budget, ledger)

    asyncio.run(run())
    assert [item[1]["input"] for item in endpoint[1]] == [
        ["query: 港口 ports"],
        ["query: 港口 evidence"],
        ["query: 港口 evidence"],
    ]
    prepared = [row for row in ledger.snapshot() if row.intent_reference]
    assert len(prepared) == 1
    observation = prepared[0].intent_reference
    assert observation.encoding_sequence == 0
    assert (
        observation.references.chunks[0].text_sha256
        == hashlib.sha256(goal.text.encode()).hexdigest()
    )
    assert observation.references.chunks[0].vector == (1.0, 0.0)
    assert budget.encoding_calls == len(endpoint[1]) == 3
    assert budget.encoding_chars == sum(len(item[1]["input"][0]) for item in endpoint[1])
    assert ledger.snapshot()[-1].similarity.document_cosine == 1.0
    assert ledger.snapshot()[-1].reason == "intent_cosine_not_probability"


def test_restored_intent_vectors_do_not_trigger_another_preparation_call(endpoint):
    cfg = service(endpoint[0])
    scoring = intent_policy(cfg, max_windows=1)
    original_config = run_config(scoring)
    budget, ledger = RunBudget(original_config, lambda: 0.0), Ledger()
    goal = Goal(text="港口 ports")
    doc = Extracted(title="native", text="港口 evidence", language="zh")
    first = EmbeddingScorer(scoring, SelfHostedEncoder(cfg))
    asyncio.run(first.score(goal, doc, budget, ledger))
    before = ledger.snapshot()
    restored = Ledger(restored_rows=before)
    resumed_budget = RunBudget(original_config, lambda: 0.0)
    resumed_budget.encoding_calls = budget.encoding_calls
    resumed_budget.encoding_chars = budget.encoding_chars
    second = EmbeddingScorer(scoring, SelfHostedEncoder(cfg))
    asyncio.run(second.score(goal, doc, resumed_budget, restored))
    assert len(endpoint[1]) == 3  # original intent, original document, new document
    assert endpoint[1][-1][1]["input"] == ["港口 evidence"]
    assert sum(row.intent_reference is not None for row in restored.snapshot()) == 1
    assert restored.snapshot()[: len(before)] == before
    assert resumed_budget.encoding_calls == 3


def test_concurrent_scores_share_preparation_but_runs_never_share_intents(endpoint):
    cfg = service(endpoint[0])
    scoring = intent_policy(cfg, max_windows=1)
    scorer = EmbeddingScorer(scoring, SelfHostedEncoder(cfg))
    document = Extracted(title="native", text="ports", language="en")
    first = RunBudget(run_config(scoring), lambda: 0.0), Ledger()
    second = RunBudget(run_config(scoring), lambda: 0.0), Ledger()

    async def run():
        await asyncio.gather(
            scorer.score(Goal(text="ports"), document, *first),
            scorer.score(Goal(text="ports"), document, *first),
            scorer.score(Goal(text="opposite"), document, *second),
        )

    asyncio.run(run())
    for budget, ledger in (first, second):
        assert len([row for row in ledger.snapshot() if row.intent_reference]) == 1
        assert budget.encoding_calls == len([row for row in ledger.snapshot() if row.encoding_call])
    assert first[1].snapshot()[-1].similarity.document_cosine == 1.0
    assert second[1].snapshot()[-1].similarity.document_cosine == -1.0
    assert len(endpoint[1]) == 5
    for goal, ledger in ((Goal(text="changed intent"), first[1]), (Goal(text="ports"), Ledger())):
        with pytest.raises(GhimeraRefused, match="adapter_contract"):
            asyncio.run(scorer.score(goal, document, first[0], ledger))
    assert len(endpoint[1]) == 5


def test_intent_full_loop_roundtrip_and_tampered_preparation_refuse(endpoint):
    cfg = service(endpoint[0])
    scoring = intent_policy(cfg, max_windows=1)
    config = run_config(scoring)
    loop = GoalLoop(
        config=config,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=EmbeddingScorer(scoring, SelfHostedEncoder(cfg)),
        judge=FakeJudge(),
    )
    result = asyncio.run(
        loop.run(
            Goal(text="ports", seeds=("https://example.org",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
    )
    assert result.documents and Harvest.model_validate_json(result.model_dump_json()) == result
    observed = next(row for row in result.ledger if row.intent_reference)
    assert result.ledger[observed.intent_reference.encoding_sequence].encoding_call.input_chars == 5
    for mutation in ("goal", "call", "input", "prefix", "missing", "late", "window", "pinned"):
        raw = result.model_dump(mode="json")
        prepared = next(row for row in raw["ledger"] if row["event"] == "intent_reference")
        reference = prepared["intent_reference"]
        if mutation == "goal":
            reference["goal_sha256"] = "d" * 64
        elif mutation == "call":
            reference["encoding_sequence"] = prepared["sequence"]
        elif mutation == "input":
            raw["ledger"][reference["encoding_sequence"]]["encoding_call"]["input_sha256"] = (
                "e" * 64,
            )
        elif mutation == "prefix":
            reference["references"]["text_prefix"] = "another: "
        elif mutation == "missing":
            raw["ledger"].remove(prepared)
        elif mutation == "late":
            raw["ledger"].remove(prepared)
            raw["ledger"].insert(-1, prepared)
        elif mutation == "window":
            next(row for row in raw["ledger"] if row["similarity"])["similarity"]["windows"][0][
                "reference_source_id"
            ] = "other"
        else:
            raw["receipt"]["effective_config"]["scoring"]["reference_source"] = "pinned"
            raw["receipt"]["effective_config"]["scoring"]["references_sha256"] = reference[
                "references"
            ]["chunks"][0]["text_sha256"]
        for index, row in enumerate(raw["ledger"]):
            row["sequence"] = index
        with pytest.raises(ValidationError):
            Harvest.model_validate(raw)


def test_intent_reference_failure_keeps_spend_and_never_invents_vectors(endpoint):
    cfg = service(endpoint[0])
    scoring = intent_policy(cfg)
    endpoint[2]["failure"] = "wrong_model"
    loop = GoalLoop(
        config=run_config(scoring),
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=EmbeddingScorer(scoring, SelfHostedEncoder(cfg)),
        judge=FakeJudge(),
    )
    result = asyncio.run(
        loop.run(
            Goal(text="ports", seeds=("https://example.org",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
    )
    assert result.receipt.stop_reason == "failed" and result.receipt.encoding_calls == 1
    assert not result.documents and not any(row.intent_reference for row in result.ledger)
    assert result.receipt.judge_calls == 0 and len(endpoint[1]) == 1
    assert Harvest.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("mode", ["calls", "chars", "oversized"])
def test_intent_preparation_obeys_shared_budget_without_partial_truncation(endpoint, mode):
    cfg = service(endpoint[0])
    changes = {"max_windows": 1}
    goal = "ports"
    if mode == "calls":
        changes["encoding_call_budget"] = 1
    elif mode == "chars":
        changes["encoding_char_budget"] = 4
    else:
        goal = "ports" * 100
    scoring = intent_policy(cfg, **changes)
    budget, ledger = RunBudget(run_config(scoring), lambda: 0.0), Ledger()
    with pytest.raises(GhimeraRefused, match="budget_exhausted"):
        asyncio.run(
            EmbeddingScorer(scoring, SelfHostedEncoder(cfg)).score(
                Goal(text=goal),
                Extracted(title="native", text="ports", language="en"),
                budget,
                ledger,
            )
        )
    assert len(endpoint[1]) == (1 if mode == "calls" else 0)
    assert budget.encoding_calls == len(endpoint[1])
    assert not any(row.similarity for row in ledger.snapshot())
    assert len([row for row in ledger.snapshot() if row.intent_reference]) == (
        1 if mode == "calls" else 0
    )


def test_intent_research_and_durable_inspection_share_the_prepared_reference(endpoint, tmp_path):
    from ghimera.journal import read_journal
    from ghimera.journal_types import JournalReport
    from ghimera.research import ResearchLoop
    from ghimera.research_types import ResearchRequest, ResearchResult
    from tests.test_intent_research import (
        AnalystFixture,
        PlannerFixture,
        ReviewerFixture,
        SearchFixture,
    )
    from tests.test_intent_research import (
        policy as research_policy,
    )
    from tests.test_run_journal import configured

    cfg = service(endpoint[0])
    scoring = intent_policy(cfg, max_windows=1)
    config = GhimeraConfig.model_validate(
        dict(
            run_config(scoring).model_dump(),
            research=research_policy(),
            journal=configured(tmp_path).journal,
        )
    )
    collector = GoalLoop(
        config=config,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=EmbeddingScorer(scoring, SelfHostedEncoder(cfg)),
        judge=FakeJudge(satisfied=True),
    )
    loop = ResearchLoop(
        config=config,
        collector=collector,
        search=SearchFixture(),
        planner=PlannerFixture(),
        analyst=AnalystFixture(),
        reviewer=ReviewerFixture(),
    )
    result = asyncio.run(loop.run(ResearchRequest(intent="find ports"), run_id="intent-research"))
    assert result.status == "answered" and result.answer.claims[0].citations
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result
    report = read_journal(config.journal, "intent-research")
    assert report.state == "complete" and report.rows == result.harvest.ledger
    prepared = next(row for row in report.rows if row.intent_reference)
    assert prepared.intent_reference.goal_sha256 == hashlib.sha256(b"find ports").hexdigest()
    assert result.harvest.receipt.encoding_calls == len(endpoint[1])
    raw = report.model_dump(mode="json")
    next(row for row in raw["rows"] if row["event"] == "intent_reference")["intent_reference"][
        "goal_sha256"
    ] = "b" * 64
    with pytest.raises(ValidationError, match="original goal"):
        JournalReport.model_validate(raw)


def test_cancelled_intent_encoding_never_becomes_a_prepared_reference():
    cfg = service(1)
    scoring = intent_policy(cfg, max_windows=1)

    class CancelledWire:
        config = cfg

        async def post(self, body):
            from ghimera.model_http import ModelWireCancelled

            raise ModelWireCancelled(ModelHttpResponse(200, b"partial-vector", "application/json"))

    budget, ledger = RunBudget(run_config(scoring), lambda: 0.0), Ledger()
    scorer = EmbeddingScorer(scoring, SelfHostedEncoder(cfg, http=CancelledWire()))
    with pytest.raises(EncodingCancelled):
        asyncio.run(
            scorer.score(
                Goal(text="ports"),
                Extracted(title="native", text="ports", language="en"),
                budget,
                ledger,
            )
        )
    assert budget.encoding_calls == len(ledger.snapshot()) == 1
    assert ledger.snapshot()[0].encoding_call.outcome == "cancelled"
    assert not any(row.intent_reference for row in ledger.snapshot())


def test_intent_scoring_example_has_no_fake_pinned_vector_digest():
    import tomllib

    with Path("examples/intent-scoring.toml").open("rb") as stream:
        parsed = ScoringConfig.model_validate(tomllib.load(stream))
    assert parsed.reference_source == "intent" and parsed.references_sha256 is None
