"""0.4.12: transient model/encoder refusals retry with backoff; persistent ones skip a page."""

import asyncio
import json

from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute
from ghimera.embedding import SelfHostedEncoder
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.model_http import ModelHttpResponse
from ghimera.model_retry import ModelRetryPolicy, retry_transient
from ghimera.models import Goal, Scope
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.semantic_scoring import EmbeddingScorer
from tests.test_embedding_scoring import policy, references, run_config, service, vector_for

QUICK = ModelRetryPolicy(
    max_wait_seconds=0.05, initial_backoff_seconds=0.01, max_backoff_seconds=0.02
)
SEEDS = ("https://example.org/one", "https://example.org/two")


class ReloadingEncoder:
    """A shared encoder that answers 409 model_not_ready while it reloads."""

    def __init__(self, cfg, *, refusals: int, refuse_when: str = "") -> None:
        self.config, self.refusals, self.refuse_when, self.calls = cfg, refusals, refuse_when, 0

    async def post(self, body: bytes) -> ModelHttpResponse:
        self.calls += 1
        texts = json.loads(body)["input"]
        if self.refusals > 0 and any(self.refuse_when in text for text in texts):
            self.refusals -= 1
            return ModelHttpResponse(409, b'{"error":"model_not_ready"}', "application/json")
        wire = {
            "object": "list",
            "model": "fixture-encoder",
            "data": [
                {"object": "embedding", "index": index, "embedding": vector_for(text)}
                for index, text in enumerate(texts)
            ],
            "usage": {"prompt_tokens": 4, "total_tokens": 4},
        }
        return ModelHttpResponse(200, json.dumps(wire).encode(), "application/json")


def collect(wire_refusals: int, refuse_when: str = ""):
    cfg = service(1)
    refs = references(cfg)
    scoring = policy(cfg, refs, max_windows=1)
    config = run_config(scoring).model_copy(update={"model_retry": QUICK})
    wire = ReloadingEncoder(cfg, refusals=wire_refusals, refuse_when=refuse_when)
    loop = GoalLoop(
        config=config,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=EmbeddingScorer(scoring, SelfHostedEncoder(cfg, http=wire), refs),
        judge=FakeJudge(),
    )
    scope = Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",))
    return asyncio.run(loop.run(Goal(text="ports", seeds=SEEDS), scope)), wire


def test_encoder_refusing_once_is_retried_and_the_page_is_scored():
    result, wire = collect(wire_refusals=1)
    assert result.receipt.stop_reason == "frontier_empty"
    assert {doc.url for doc in result.documents} == set(SEEDS)
    encodings = [row for row in result.ledger if row.event == "encoding"]
    refused = [row for row in encodings if row.refusal is not None]
    assert len(refused) == 1 and refused[0].detail == "encoder answered HTTP 409"
    # The refused try is its own accounted call.
    assert result.receipt.encoding_calls == len(encodings) == wire.calls
    assert not any(row.refusal == RefusalCode.MODEL_UNAVAILABLE for row in result.ledger[-3:])
    assert all(
        row.refusal == RefusalCode.OUT_OF_SCOPE for row in result.ledger if row.event == "refusal"
    )


def test_encoder_that_keeps_refusing_skips_that_page_and_the_round_continues():
    result, _ = collect(wire_refusals=10_000, refuse_when="/one")
    assert result.receipt.stop_reason == "frontier_empty"
    assert {doc.url for doc in result.documents} == {"https://example.org/two"}
    skipped = [
        row
        for row in result.ledger
        if row.event == "refusal" and row.refusal != RefusalCode.OUT_OF_SCOPE
    ]
    assert [(row.url, row.refusal, row.detail) for row in skipped] == [
        ("https://example.org/one", RefusalCode.MODEL_UNAVAILABLE, "encoder answered HTTP 409")
    ]
    assert not any(row.reason == "source_processing_cancelled" for row in result.ledger)


def test_non_transient_refusals_are_never_retried():
    attempts = []

    async def wrong_model():
        attempts.append(1)
        raise GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE, "wrong served model")

    try:
        asyncio.run(retry_transient(QUICK, wrong_model))
    except GhimeraRefused as exc:
        assert exc.detail == "wrong served model"
    assert attempts == [1]


def test_backoff_is_bounded_by_max_wait():
    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)

    async def reloading():
        refused = GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE, "encoder answered HTTP 409")
        refused.transient = True
        raise refused

    policy_ = ModelRetryPolicy(
        max_wait_seconds=10, initial_backoff_seconds=1, max_backoff_seconds=4
    )
    try:
        asyncio.run(retry_transient(policy_, reloading, sleep=sleep))
    except GhimeraRefused:
        pass
    assert waits == [1, 2, 4] and sum(waits) <= 10
    assert ModelRetryPolicy().max_wait_seconds == 180.0


def test_transient_judge_refusal_is_retried_on_the_legacy_verdict_path():
    class ReloadingJudge(FakeJudge):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def document(self, goal, document, *, second_look):
            self.calls += 1
            if self.calls == 1:
                refused = GhimeraRefused(RefusalCode.MODEL_UNAVAILABLE, "judge answered HTTP 503")
                refused.transient = True
                raise refused
            return await super().document(goal, document, second_look=second_look)

    from ghimera.doubles import KeywordScorer
    from tests.test_c0 import config

    judge = ReloadingJudge()
    loop = GoalLoop(
        config=config(model_retry=QUICK.model_dump(by_alias=True)),
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=judge,
    )
    scope = Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",))
    result = asyncio.run(loop.run(Goal(text="ports", seeds=SEEDS[:1]), scope))
    assert [doc.url for doc in result.documents] == [SEEDS[0]] and judge.calls == 2
    verdicts = [row for row in result.ledger if row.event == "verdict"]
    assert [row.refusal for row in verdicts] == [RefusalCode.MODEL_UNAVAILABLE, None]
    assert verdicts[0].detail == "judge answered HTTP 503"
    assert result.receipt.judge_calls == 2
