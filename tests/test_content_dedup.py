"""Content identity must never destroy source occurrences or invent aliases."""

import asyncio
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.content_dedup import ContentIndex, canonical_url, fingerprint
from ghimera.dedup_config import DedupConfig
from ghimera.doubles import FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import Document, Extracted, Goal, Harvest, Page, Scope, Verdict


def policy(**updates):
    raw = dict(
        schema="chimera.dedup/1",
        tracking_parameters=["fbclid"],
        tracking_prefixes=["utm_"],
        shingle_chars=5,
        max_hamming_distance=3,
        min_near_chars=100,
        max_index_documents=1000,
        max_text_chars=500000,
        min_length_ratio=0.9,
    )
    raw.update(updates)
    return DedupConfig.model_validate(raw)


def document(text, *, url="https://example.org/report", raw=None, language="en"):
    body = text.encode() if raw is None else raw
    return Document(
        url=url,
        sha256=hashlib.sha256(body).hexdigest(),
        raw=body,
        extracted=Extracted(title="report", text=text, language=language),
        verdict=Verdict(
            decision="accept",
            kind="report",
            publisher="fixture",
            language=language,
            reason="controlled fixture",
        ),
    )


TEXT = (
    "The port authority published a report about maritime infrastructure. "
    "It describes terminal construction, shipping capacity and logistics investment. "
) * 8


def test_canonical_url_is_configured_and_preserves_meaningful_query():
    assert (
        canonical_url("https://EXAMPLE.org:443/r?utm_source=x&id=1&fbclid=y#part", policy())
        == "https://example.org/r?id=1"
    )
    assert canonical_url("https://example.org/r?id=2", policy()) != canonical_url(
        "https://example.org/r?id=1", policy()
    )
    with pytest.raises(ValueError):
        canonical_url("https://user:secret@example.org/r", policy())


def test_exact_bytes_and_normalized_native_text_are_separate_evidence():
    index = ContentIndex(policy())
    first = document(TEXT)
    index.add(first)
    raw_match = index.match(
        document("different extraction", url="https://example.org/other", raw=first.raw)
    )
    assert raw_match.reason == "content_sha256" and raw_match.representative_sha256 == first.sha256
    text_match = index.match(document("  " + TEXT.upper() + "\n", url="https://example.org/mirror"))
    assert text_match.reason == "normalized_text" and text_match.hamming_distance == 0


def test_simhash_links_same_language_edits_not_translations_or_short_text():
    index = ContentIndex(policy())
    first = document(TEXT)
    index.add(first)
    changed = document(TEXT.replace("authority", "agency", 1), url="https://example.org/mirror")
    match = index.match(changed)
    assert match is not None and match.reason == "simhash"
    assert match.hamming_distance <= policy().max_hamming_distance
    assert index.match(document(changed.extracted.text, language="zh")) is None
    short = ContentIndex(policy())
    short.add(document("tiny text"))
    assert short.match(document("tiny next")) is None


def test_canonical_same_url_is_not_enough_to_merge_changed_content():
    index = ContentIndex(policy())
    first = document(TEXT)
    index.add(first)
    changed = document(
        "A completely different report about agricultural yields and crop disease. " * 20
    )
    assert index.match(changed) is None
    drift = index.drift(changed)
    assert drift.previous_sha256 == first.sha256 and drift.current_sha256 == changed.sha256


def test_chinese_character_shingles_and_deterministic_reader():
    first = document(
        "港口管理機關發布海運與港口建設報告，說明貨運能力與物流投資。" * 20, language="zh"
    )
    index = ContentIndex(policy())
    index.add(first)
    changed = document(
        first.extracted.text.replace("港口管理機關", "港口主管機關", 1),
        url="https://example.org/zh",
        language="zh",
    )
    assert index.match(changed).reason == "simhash"
    assert fingerprint(first, policy()) == fingerprint(first, policy())


def test_policy_refuses_unbounded_or_ambiguous_settings():
    for change in (
        {"max_index_documents": 0},
        {"max_hamming_distance": 64},
        {"tracking_prefixes": [""]},
        {"tracking_parameters": ["ID"]},
    ):
        with pytest.raises(ValidationError):
            policy(**change)


def test_index_limit_refuses_without_silently_eviction():
    from ghimera.refusals import GhimeraRefused, RefusalCode

    index = ContentIndex(policy(max_index_documents=1))
    index.add(document(TEXT))
    with pytest.raises(GhimeraRefused) as exc:
        index.add(document("Different source", url="https://example.org/other"))
    assert exc.value.code == RefusalCode.BUDGET_EXHAUSTED
    assert index.match(document(TEXT)).reason == "content_sha256"


def test_changed_numbers_remain_independent_documents():
    index = ContentIndex(policy())
    index.add(document(TEXT + " Shipping capacity: 42."))
    assert index.match(document(TEXT + " Shipping capacity: 43.")) is None


def test_canonical_hint_cannot_alias_to_a_different_host():
    item = document(TEXT)
    hinted = item.model_copy(
        update={
            "extracted": item.extracted.model_copy(
                update={"canonical_url": "https://other.example/report"}
            )
        }
    )
    assert fingerprint(hinted, policy()).canonical_url == item.url


def test_identical_byte_aliases_do_not_shadow_each_others_citations():
    from ghimera.evidence_context import ContextSelector
    from ghimera.model_config import EvidenceContextConfig
    from ghimera.research import CitationValidator, citation_for

    first = document(TEXT)
    mirror = document(TEXT, url="https://example.org/mirror")
    sources = (first, mirror)
    citations = tuple(citation_for(item, 0, 40) for item in sources)
    CitationValidator(sources).validate(citations)
    context_policy = EvidenceContextConfig.model_validate(
        dict(
            schema="chimera.evidence-context/1",
            max_documents=2,
            max_chars=300,
            window_chars=100,
            max_windows_per_document=2,
            overlap_chars=10,
        )
    )
    context = ContextSelector(context_policy).build("port", sources, required=citations)
    assert {item.citation.source_url for item in context.windows} == {item.url for item in sources}
    assert {item.url for item in context.documents} == {item.url for item in sources}


def test_drift_is_bound_to_effective_policy_and_both_retained_revisions():
    index = ContentIndex(policy())
    first = document(TEXT)
    second = document(TEXT + " A new appendix was added.")
    index.add(first)
    drift = index.drift(second)
    assert drift.config_digest == policy().content_digest()
    index.observe(second)
    assert index.drift(second) is None


def test_fingerprint_limit_refuses_rather_than_merging_truncated_text():
    from ghimera.refusals import GhimeraRefused, RefusalCode

    with pytest.raises(GhimeraRefused) as exc:
        fingerprint(document(TEXT), policy(max_text_chars=10))
    assert exc.value.code == RefusalCode.BUDGET_EXHAUSTED


@pytest.mark.parametrize(
    "urls",
    [
        ("https://example.org/a", "https://example.org/b"),
        ("https://example.org/report?utm_source=a", "https://example.org/report?utm_source=b"),
    ],
)
def test_loop_retains_duplicate_raw_extraction_verdict_and_bound_evidence(urls):
    cfg = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    cfg.update(dedup=policy().model_dump(by_alias=True), grade_interval=100, saturation_window=100)

    class Route(FakeRoute):
        async def attempt(self, request):
            raw = TEXT if request.url == urls[0] else TEXT.replace("authority", "agency", 1)
            return Page(
                url=request.url,
                final_url=request.url,
                status=200,
                content_type="text/html",
                body=raw.encode(),
            )

    class Extractor:
        revision = "controlled-native@1"

        def validate_config(self, config):
            pass

        async def extract(self, page):
            return Extracted(title="port report", text=page.body.decode(), language="en")

    result = asyncio.run(
        GoalLoop(
            config=GhimeraConfig.model_validate(cfg),
            fetcher=FetchLadder((Route(),)),
            extractor=Extractor(),
            scorer=KeywordScorer(),
            judge=FakeJudge(),
        ).run(
            Goal(text="ports", seeds=urls),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
    )
    assert len(result.documents) == 1
    kept = result.documents[0]
    assert kept.duplicate_urls == (urls[1],) and len(kept.occurrences) == 1
    occurrence = kept.occurrences[0]
    assert occurrence.raw != kept.raw and occurrence.verdict.decision == "accept"
    assert occurrence.dedup.representative_sha256 == kept.sha256
    assert occurrence.dedup.config_digest == policy().content_digest()
    assert Harvest.model_validate_json(result.model_dump_json()) == result
    assert result.source_documents[1].sha256 == occurrence.sha256
    from ghimera.research import citation_for

    citation = citation_for(result.source_documents[1], 0, 40)
    assert citation.matches(result.source_documents[1])
    assert not citation.matches(kept)
    if "utm_source" in urls[0]:
        drift = next(row.content_drift for row in result.ledger if row.event == "content_drift")
        assert drift.previous_sha256 == kept.sha256 and drift.current_sha256 == occurrence.sha256
    wire = result.model_dump(by_alias=True)
    wire["documents"][0]["occurrences"][0]["dedup"]["representative_sha256"] = "0" * 64
    with pytest.raises(ValidationError):
        Harvest.model_validate(wire)
    wire = result.model_dump(by_alias=True)
    duplicate = next(row for row in wire["ledger"] if row["event"] == "duplicate")
    duplicate["dedup"]["config_digest"] = "0" * 64
    with pytest.raises(ValidationError):
        Harvest.model_validate(wire)
