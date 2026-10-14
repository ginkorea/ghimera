"""0.4.12: no run ceilings; spend is counted; input bounds cut or refuse one input."""

import asyncio
import hashlib
import io
import os
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.discovery_config import DiscoveryProvider
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import Extracted, Goal, LinkCandidate, Page, Scope
from ghimera.reference_config import ReferenceConfig
from ghimera.refusals import REFUSALS, BoundExceeded, GhimeraRefused, RefusalCode
from ghimera.research_config import DEFAULT_SATURATION_ROUNDS, ResearchConfig
from ghimera.scoring_config import ScoringConfig
from ghimera.spend import RunSpend
from tests.test_c0 import config


def scope(max_depth: int = 2) -> Scope:
    return Scope(allowed_hosts=("example.org",), max_depth=max_depth, content_types=("text/html",))


def refusal_text(excinfo: pytest.ExceptionInfo[ValidationError]) -> str:
    return " ".join(error["msg"] for error in excinfo.value.errors())


@pytest.mark.parametrize("key", ["page_budget", "byte_budget", "judge_budget", "wall_seconds"])
def test_a_recipe_with_a_run_ceiling_is_refused_by_name(key):
    raw = config().model_dump(by_alias=True)
    raw[key] = 400
    with pytest.raises(ValidationError) as excinfo:
        GhimeraConfig.model_validate(raw)
    assert (
        f"recipe key '{key}' is refused: the core no longer has run ceilings "
        "(removed in 0.4.12); delete the key"
    ) in refusal_text(excinfo)


def test_the_page_budget_recipe_is_refused_from_toml(tmp_path):
    text = Path("examples/chimera.toml").read_text()
    path = tmp_path / "stale.toml"
    path.write_text("page_budget = 400\n" + text)
    with pytest.raises(ValidationError) as excinfo:
        GhimeraConfig.from_toml(path)
    assert "recipe key 'page_budget' is refused" in refusal_text(excinfo)
    assert "no longer has run ceilings" in refusal_text(excinfo)


def test_nested_ceilings_are_refused_by_their_section_name():
    from tests.test_intent_research import policy

    for key in ("max_rounds", "query_budget"):
        with pytest.raises(ValidationError) as excinfo:
            ResearchConfig.model_validate(policy(**{key: 8}))
        assert f"'research.{key}' is refused" in refusal_text(excinfo)
    scoring = tomllib.loads(Path("examples/scoring.toml").read_text())
    for key in ("encoding_call_budget", "encoding_char_budget"):
        with pytest.raises(ValidationError) as excinfo:
            ScoringConfig.model_validate(scoring | {key: 100})
        assert f"'scoring.{key}' is refused" in refusal_text(excinfo)
    discovery = tomllib.loads(Path("examples/discovery.toml").read_text())
    provider = discovery["discovery"]["providers"][0]
    for key in ("max_calls", "byte_budget", "call_seconds_budget"):
        with pytest.raises(ValidationError) as excinfo:
            DiscoveryProvider.model_validate(provider | {key: 12})
        assert f"'discovery provider {key}' is refused" in refusal_text(excinfo)
    references = tomllib.loads(Path("examples/intent-research.toml").read_text())["references"]
    with pytest.raises(ValidationError) as excinfo:
        ReferenceConfig.model_validate(references | {"cited_by_query_budget": 8})
    assert "'references.cited_by_query_budget' is refused" in refusal_text(excinfo)


def test_budget_exhausted_no_longer_exists():
    assert not hasattr(RefusalCode, "BUDGET_EXHAUSTED")
    assert "budget_exhausted" not in {code.value for code in RefusalCode}
    assert RefusalCode.BOUND_EXCEEDED in REFUSALS


def test_spend_counts_without_any_limit():
    spend = RunSpend(config(), lambda: 0.0)
    for _ in range(10_000):
        spend.reserve_fetch()
        spend.reserve_judge()
    spend.record_bytes(10**12)
    assert (spend.fetches, spend.judge_calls, spend.bytes_read) == (10_000, 10_000, 10**12)
    for attribute in ("remaining_seconds", "remaining_bytes", "exhausted_ceiling", "check_time"):
        assert not hasattr(spend, attribute)


def test_research_ends_on_saturation_with_no_round_ceiling():
    from tests.test_intent_research import policy

    research = ResearchConfig.model_validate(policy())
    assert research.effective_saturation_rounds == DEFAULT_SATURATION_ROUNDS
    assert not {"max_rounds", "query_budget"} & set(research.model_dump(by_alias=True))
    explicit = ResearchConfig.model_validate(policy(saturation_rounds=4))
    assert explicit.effective_saturation_rounds == 4


class Chain(FakeExtractor):
    """Page n links to page n+1 until a fixed end; no stopping rule is met before it."""

    def __init__(self, pages: int) -> None:
        self.pages = pages

    async def extract(self, page: Page) -> Extracted:
        number = int(page.final_url.rsplit("/p", 1)[1])
        links = (
            (LinkCandidate(url=f"https://example.org/p{number + 1}", anchor="ports next"),)
            if number + 1 < self.pages
            else ()
        )
        return Extracted(title="chain", text=page.body.decode(), language="en", links=links)


def test_a_run_with_no_stopping_rule_met_continues_past_the_old_page_budget():
    pages = 450  # examples/chimera.toml carried page_budget = 400 before 0.4.12
    loop = GoalLoop(
        config=config(),
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=Chain(pages),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    goal = Goal(text="ports", seeds=("https://example.org/p0",))
    result = asyncio.run(loop.run(goal, scope(max_depth=pages)))
    assert result.receipt.stop_reason == "frontier_empty"
    assert result.receipt.fetches == pages > 400
    assert len(result.documents) == pages
    assert result.receipt.judge_calls >= pages and result.receipt.bytes_read > 0
    assert not any(row.refusal == RefusalCode.BOUND_EXCEEDED for row in result.ledger)


def twelve_megabyte_page() -> Page:
    paragraph = "<p>The port authority report covers maritime terminal construction.</p>"
    html = (
        "<!doctype html><html lang='en'><head><title>Ports</title></head><body><article>"
        + paragraph * (12 * 1024 * 1024 // len(paragraph) + 1)
        + "</article></body></html>"
    )
    body = html.encode()
    assert len(body) > 12 * 1024 * 1024
    return Page(
        url="https://example.org/reports/ports",
        final_url="https://example.org/reports/ports",
        status=200,
        content_type="text/html",
        body=body,
    )


def test_a_twelve_megabyte_page_is_read_as_its_first_eight_megabytes(tmp_path):
    from tests.test_fast_reader import FAST
    from tests.test_html_extraction import extractor

    bound = 8 * 1024 * 1024
    reader = extractor(tmp_path, fast_reader=FAST, max_input_bytes=bound)
    page = twelve_megabyte_page()
    parsed = asyncio.run(reader.extract(page))
    evidence = parsed.extraction
    assert evidence is not None
    assert evidence.omitted_bytes == len(page.body) - bound
    # The evidence still names the whole page it was cut from.
    assert evidence.source_sha256 == hashlib.sha256(page.body).hexdigest()
    assert "maritime terminal construction" in parsed.text
    assert type(evidence).model_validate(evidence.model_dump(by_alias=True)) == evidence


def test_a_page_within_the_bound_records_no_omission(tmp_path):
    from tests.test_fast_reader import FAST
    from tests.test_html_extraction import extractor, page

    parsed = asyncio.run(extractor(tmp_path, fast_reader=FAST).extract(page()))
    assert parsed.extraction is not None and parsed.extraction.omitted_bytes == 0
    assert "omitted_bytes" not in parsed.extraction.model_dump(by_alias=True)


def test_an_input_bound_refusal_names_the_bound_and_is_not_a_spend_limit():
    refused = BoundExceeded("visuals.max_pixels", 400_000_000, 40_000_000)
    assert refused.code == RefusalCode.BOUND_EXCEEDED
    assert refused.detail == (
        "input bound visuals.max_pixels exceeded: 4e+08 over limit 4e+07; "
        "an input bound, not a spend limit"
    )


class BombOnOnePage(FakeExtractor):
    """One page carries an image that cannot be decoded within its input bound."""

    async def extract(self, page: Page) -> Extracted:
        if page.final_url.endswith("/bomb"):
            raise BoundExceeded("visuals.max_pixels", 400_000_000, 40_000_000, "image bomb")
        if page.final_url.endswith("/a"):
            return Extracted(
                title="hub",
                text="ports hub",
                language="en",
                links=(
                    LinkCandidate(url="https://example.org/bomb", anchor="ports bomb"),
                    LinkCandidate(url="https://example.org/fast", anchor="ports fast"),
                ),
            )
        return Extracted(title="leaf", text=page.body.decode(), language="en", links=())


@pytest.mark.parametrize("parallel", [False, True])
def test_an_image_bomb_refuses_that_page_and_the_round_continues(parallel):
    execution = (
        dict(
            schema="ghimera.execution/1",
            active_sources=4,
            extraction_workers=4,
            scoring_workers=4,
            judge_workers=4,
            semantic_workers=1,
            visual_workers=1,
        )
        if parallel
        else None
    )
    loop = GoalLoop(
        config=config(saturation_window=50, execution=execution),
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=BombOnOnePage(),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
    )
    result = asyncio.run(loop.run(Goal(text="ports", seeds=("https://example.org/a",)), scope()))
    assert result.receipt.stop_reason == "frontier_empty"
    assert {doc.url for doc in result.documents} == {
        "https://example.org/a",
        "https://example.org/fast",
    }
    bomb = [row for row in result.ledger if row.url == "https://example.org/bomb" and row.refusal]
    assert bomb[-1].refusal == RefusalCode.BOUND_EXCEEDED
    assert "visuals.max_pixels" in (bomb[-1].detail or "")
    assert "not a spend limit" in (bomb[-1].detail or "")


@pytest.mark.skipif(
    not os.environ.get("GHIMERA_TEST_TESSERACT"), reason="needs the real OCR decoder worker"
)
def test_a_real_decompression_bomb_is_refused_as_an_input_bound(tmp_path):
    from PIL import Image

    from ghimera.image_ocr import TesseractOcr
    from tests.test_visuals import recipe

    visual = recipe(tmp_path)
    image = Image.new("1", (15_000, 15_000))  # 225 megapixels compress to a tiny PNG
    output = io.BytesIO()
    image.save(output, "PNG")
    raw = output.getvalue()
    assert len(raw) < visual.max_image_bytes
    with pytest.raises(GhimeraRefused) as refused:
        asyncio.run(TesseractOcr(visual).read(raw))
    assert refused.value.code == RefusalCode.BOUND_EXCEEDED
    assert "input bound" in refused.value.detail and "not a spend limit" in refused.value.detail


def test_per_run_feature_caps_are_refused_by_section_name():
    from ghimera.identity_automation_types import IdentityAutomationConfig
    from ghimera.local_input_types import LocalInputConfig
    from ghimera.model_work_types import ModelResultsConfig, ModelWorkConfig
    from ghimera.research_reranking_types import ResearchRerankingConfig
    from ghimera.run_encoding_types import RunEncodingRecoveryConfig
    from ghimera.semantic_types import SemanticConfig, SemanticVerificationConfig

    def toml(name: str) -> dict:
        return tomllib.loads(Path("examples", name).read_text())

    cases = [
        (ModelWorkConfig, toml("model-work.toml")["model_work"], "model_work.", "max_input_bytes"),
        (
            ModelWorkConfig,
            toml("model-work.toml")["model_work"],
            "model_work.",
            "max_unanswered_calls",
        ),
        (
            ModelResultsConfig,
            toml("model-results.toml"),
            "model_work.results.",
            "max_total_result_bytes",
        ),
        (SemanticConfig, toml("semantics.toml"), "semantics.", "max_calls_per_run"),
        (
            SemanticVerificationConfig,
            toml("review-proposal-dates.toml"),
            "semantics.verification.",
            "max_calls_per_run",
        ),
        (
            IdentityAutomationConfig,
            toml("identity-automation.toml"),
            "identity_automation.",
            "max_proposal_calls",
        ),
        (
            IdentityAutomationConfig,
            toml("identity-automation.toml"),
            "identity_automation.",
            "max_review_calls",
        ),
        (
            ResearchRerankingConfig,
            toml("research-reranking.toml"),
            "research.reranking.",
            "max_calls",
        ),
    ]
    for model, raw, prefix, key in cases:
        with pytest.raises(ValidationError) as excinfo:
            model.model_validate(raw | {key: 4})
        assert f"recipe key '{prefix}{key}' is refused" in refusal_text(excinfo), key
    local = {
        "schema": "ghimera.local-inputs/1",
        "allowed_roots": ["/srv/in"],
        "max_input_bytes": 10,
    }
    for key in ("max_files_per_run", "max_total_bytes"):
        with pytest.raises(ValidationError) as excinfo:
            LocalInputConfig.model_validate(local | {key: 4})
        assert f"'local_inputs.{key}' is refused" in refusal_text(excinfo)
    recovery = {
        "schema": "ghimera.run-encoding-recovery/1",
        "max_result_bytes": 10,
        "unknown_policy": "hold",
    }
    RunEncodingRecoveryConfig.model_validate(recovery)
    with pytest.raises(ValidationError) as excinfo:
        RunEncodingRecoveryConfig.model_validate(recovery | {"max_total_result_bytes": 20})
    assert "'run_encoding_recovery.max_total_result_bytes' is refused" in refusal_text(excinfo)
