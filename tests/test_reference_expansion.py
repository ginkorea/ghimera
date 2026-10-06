"""Sources-of-sources are observed, scored and bounded by configured depth."""

import asyncio
import hashlib

import pytest
from pydantic import ValidationError

from chimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from chimera.fetch import FetchLadder
from chimera.loop import GoalLoop
from chimera.models import Extracted, Goal, Harvest, LinkCandidate, Scope
from chimera.reference_types import DocumentReference, ReferenceSpan
from tests.test_c0 import config


def references(**updates):
    raw = dict(
        schema="chimera.references/1",
        follow_document_references=True,
        discover_cited_by=False,
        outside_scope="refuse",
        denied_hosts=(),
        max_parents=8,
        max_candidates_per_parent=8,
        max_queued_per_run=8,
        max_extra_hosts=2,
        max_hops=1,
        cited_by_query_budget=0,
        cited_by_query_template='"{title}" "{url}" cited by',
        max_query_title_chars=120,
    )
    raw.update(updates)
    return raw


def reference(url, text):
    start = text.index(url)
    return DocumentReference(
        schema="chimera.document-reference/1",
        target_url=url,
        anchor=text,
        kind="native_url",
        span=ReferenceSpan(start=start, end=start + len(url), quote=url),
    )


class ReferenceExtractor(FakeExtractor):
    async def extract(self, page):
        target = (
            "https://references.example/ports"
            if page.final_url.endswith("/root")
            else "https://third.example/ports"
        )
        text = "Port source: " + target
        return Extracted(
            title="Port report",
            text=text,
            language="en",
            links=(LinkCandidate(url=target, anchor=text),),
            references=(reference(target, text),),
        )


def run(*, cfg=None, judge=None):
    cfg = cfg or config(references=references(outside_scope="observed_public"))
    route = FakeRoute()
    collector = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route,)),
        extractor=ReferenceExtractor(),
        scorer=KeywordScorer(),
        judge=judge or FakeJudge(),
    )
    result = asyncio.run(
        collector.run(
            Goal(text="ports", seeds=("https://example.org/root",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
    )
    return result, route


def test_observed_reference_gets_one_scored_hop_not_a_recursive_crawl():
    result, route = run()
    assert [item.url for item in route.requests] == [
        "https://example.org/root",
        "https://references.example/ports",
    ]
    observations = [row.reference for row in result.ledger if row.reference is not None]
    assert [item.outcome for item in observations] == ["queued", "hop_limit"]
    assert observations[0].source.sha256 == result.documents[0].sha256
    assert observations[0].parent_hops == 0 and observations[1].parent_hops == 1
    assert result.model_validate_json(result.model_dump_json()) == result


def test_depth_is_configuration_not_a_one_hop_implementation_ceiling():
    result, route = run(
        cfg=config(references=references(outside_scope="observed_public", max_hops=2))
    )
    assert [item.url for item in route.requests] == [
        "https://example.org/root",
        "https://references.example/ports",
        "https://third.example/ports",
    ]
    assert result.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("budget", ["max_parents", "max_queued_per_run"])
def test_document_reference_limits_share_one_run_owned_budget(budget):
    result, route = run(
        cfg=config(
            references=references(outside_scope="observed_public", max_hops=3, **{budget: 1})
        )
    )
    assert len(route.requests) == 2
    assert [row.reference.outcome for row in result.ledger if row.reference] == [
        "queued",
        "budget_limit",
    ]


def test_extra_host_limit_applies_across_reference_descendants():
    result, route = run(
        cfg=config(
            references=references(outside_scope="observed_public", max_hops=3, max_extra_hosts=1)
        )
    )
    assert len(route.requests) == 2
    assert [row.reference.outcome for row in result.ledger if row.reference] == [
        "queued",
        "out_of_scope",
    ]


def test_disabled_outscope_and_denied_references_never_reach_fetcher():
    for policy, expected in [
        (references(), "out_of_scope"),
        (references(follow_document_references=False), "disabled"),
        (
            references(outside_scope="observed_public", denied_hosts=("references.example",)),
            "out_of_scope",
        ),
        (None, None),
    ]:
        result, route = run(cfg=config(references=policy))
        assert len(route.requests) == 1
        if expected is not None:
            assert next(row.reference for row in result.ledger if row.reference).outcome == expected


def test_rejected_document_never_seeds_references():
    class Reject(FakeJudge):
        async def document(self, goal, document, *, second_look):
            return (await super().document(goal, document, second_look=second_look)).model_copy(
                update={"decision": "reject"}
            )

    result, route = run(judge=Reject())
    assert len(route.requests) == 1 and not result.documents
    assert not any(row.reference for row in result.ledger)


def test_native_reference_and_ledger_provenance_cannot_be_rebound():
    text = "Port source: https://references.example/ports"
    proof = reference("https://references.example/ports", text)
    raw = dict(
        title="Report",
        text=text,
        language="en",
        links=(LinkCandidate(url=proof.target_url, anchor=text),),
        references=(proof,),
    )
    assert Extracted.model_validate(raw).references == (proof,)
    with pytest.raises(ValidationError):
        Extracted.model_validate(dict(raw, text=text.replace("references", "different")))
    result, _ = run()
    wire = result.model_dump(by_alias=True)
    row = next(item for item in wire["ledger"] if item["reference"] is not None)
    row["reference"]["source"]["sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="reference"):
        Harvest.model_validate(wire)


def test_saved_child_cannot_reset_depth_using_an_invented_frontier_origin():
    result, _ = run()
    wire = result.model_dump(by_alias=True)
    row = [item for item in wire["ledger"] if item["reference"] is not None][-1]
    row["reference"]["parent_hops"] = 0
    row["reference"]["origin_url"] = "https://invented.example/ports"
    with pytest.raises(ValidationError, match="ancestry"):
        Harvest.model_validate(wire)


def test_reference_policy_is_explicit_versioned_and_validates_query_templates():
    assert config(references=references()).references.schema_version == "chimera.references/1"
    for update in [
        dict(schema="chimera.references/2"),
        dict(max_queued_per_run=0),
        dict(cited_by_query_template="{untrusted.attribute}"),
        dict(discover_cited_by=True, cited_by_query_budget=1),
        dict(denied_hosts=("127.0.0.1",)),
    ]:
        with pytest.raises(ValidationError):
            config(references=references(**update))


def test_real_docling_native_links_carry_replayable_text_locators(tmp_path):
    from chimera.documents import DocumentExtractor
    from tests.test_document_extraction import config as document_config
    from tests.test_document_extraction import page

    source = page()
    cfg = document_config(tmp_path, timeout_seconds=90.0)
    result = asyncio.run(DocumentExtractor(cfg).extract(source))
    proof = result.references[0]
    assert proof.target_url == "https://example.org/appendix.pdf"
    assert proof.matches(result.text, result.document_layout)
    assert result.document_parse.source_sha256 == hashlib.sha256(source.body).hexdigest()
    assert Extracted.model_validate_json(result.model_dump_json()) == result
