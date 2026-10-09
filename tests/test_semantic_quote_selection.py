"""Source-owned quote choices, never answer repair or model accuracy evidence."""

import asyncio
import json
import tomllib
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.model_client import SelfHostedModel
from ghimera.refusals import GhimeraRefused, ModelFailure
from ghimera.semantic_batching import review_selections
from ghimera.semantic_graph import validate_rows
from ghimera.semantic_quotes import native_quote_templates
from ghimera.semantic_types import SemanticVerificationConfig
from tests.test_semantic_graph import SemanticWire, document
from tests.test_semantic_review_dimensions import DimensionWire, dimension_config
from tests.test_semantic_verification import run_stage


def quote_config(tmp_path):
    raw = dimension_config(tmp_path).model_dump()
    raw["semantics"]["verification"]["prompt_profile"] = "native_quote_checks"
    return GhimeraConfig.model_validate(raw)


class QuoteWire(DimensionWire):
    def __init__(self, service, *, defect=None, **kwargs):
        super().__init__(service, **kwargs)
        self.quote_defect = defect

    async def post(self, body):
        response = await super().post(body)
        wire = json.loads(response.body)
        payload = json.loads(wire["choices"][0]["message"]["content"])
        packet = self.requests[-1][1]
        payload["schema"] = "ghimera.semantic-review/6"
        if packet["semantic_review_selection"]["coverage"]:
            templates = packet["native_quote_templates"]
            citation = packet["evidence"]["windows"][0]["citation_id"]

            def witness(surface, occurrence=0):
                return dict(surface=surface, citation_id=citation, occurrence=occurrence)

            evidence = templates[-1]  # Exact native clause, not the whole-window fallback.
            payload.update(
                coverage="incomplete",
                coverage_reason="The source asserts another organizational relationship.",
                coverage_findings=[
                    dict(
                        kind="relation",
                        rule="reports_to",
                        source=witness("丙委員會"),
                        target=witness("乙委員會", 1),
                        evidence=dict(quote_id=evidence["quote_id"]),
                        reason="The native clause contains both endpoints and the predicate.",
                    )
                ],
            )
            if self.quote_defect == "unknown":
                payload["coverage_findings"][0]["evidence"]["quote_id"] = "0" * 64
            elif self.quote_defect == "wrong_clause":
                payload["coverage_findings"][0]["evidence"]["quote_id"] = templates[1]["quote_id"]
            elif self.quote_defect == "duplicate_proposal":
                payload["coverage_findings"][0]["source"] = witness("甲委員會")
                payload["coverage_findings"][0]["target"] = witness("乙委員會")
                payload["coverage_findings"][0]["evidence"]["quote_id"] = templates[0]["quote_id"]
            elif self.quote_defect == "coverage_shape":
                payload["coverage"] = "adequate"
        wire["choices"][0]["message"]["content"] = json.dumps(payload)
        return type(response)(response.status, json.dumps(wire).encode(), response.content_type)


def native_document():
    return document("甲委員會隸屬乙委員會。\n丙委員會隸屬乙委員會。")


def test_selected_native_quote_preserves_coverage_and_review_replay(tmp_path):
    cfg, doc = quote_config(tmp_path), native_document()
    wire = QuoteWire(cfg.models.reviewer)
    graph, rows, _ = run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (doc,))
    window = rows[-1].semantic_window
    review = window.review.parts[-1].review
    assert review.coverage_findings[0].evidence.surface == "丙委員會隸屬乙委員會。"
    assert review.quote_response.response.schema_version == "ghimera.semantic-review/6"
    assert review.dimension_response is None
    assert not any(node.label == "丙委員會" for node in graph.snapshot().nodes)
    assert validate_rows(cfg, rows) == (rows[-1],)
    assert type(window).model_validate_json(window.model_dump_json()) == window
    for request, packet in wire.requests:
        assert packet["semantic_proposal"] == wire.requests[0][1]["semantic_proposal"]
        schema = request["response_format"]["json_schema"]["schema"]
        Draft202012Validator.check_schema(schema)
        assert "quote_id" in schema["$defs"]["NativeQuoteReference"]["properties"]
        assert "surface" not in schema["$defs"]["NativeQuoteReference"]["properties"]
        assert "native_quote_templates" in packet
        assert "Return ghimera.semantic-review/6" in request["messages"][0]["content"]
        choice = schema["$defs"]["NativeQuoteReference"]["properties"]["quote_id"]
        assert choice["enum"] == [t["quote_id"] for t in packet["native_quote_templates"]]
        assert review.model_call.prompt_revision == "ghimera-semantic-verification/8"
        assert review.quote_response.response.model_call is None


@pytest.mark.parametrize(
    "defect,reason",
    [
        ("unknown", "semantic_review_normalization_failed"),
        ("coverage_shape", "semantic_review_normalization_failed"),
        ("wrong_clause", "semantic_review_source_binding_failed"),
        ("duplicate_proposal", "semantic_review_source_binding_failed"),
    ],
)
def test_quote_selector_cannot_evade_original_grounding(tmp_path, defect, reason):
    cfg = quote_config(tmp_path)
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg,
            SemanticWire(cfg.models.analyst),
            QuoteWire(cfg.models.reviewer, defect=defect),
            (native_document(),),
        )

    # The stage deliberately exposes its stable public refusal, while the
    # native client and retained ledger own the detailed observed call.
    async def client_review():
        doc = native_document()
        extractor = SelfHostedModel(cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst))
        original = await extractor.semantic_extract(
            "map the organization", doc, 0, len(doc.extracted.text), cfg.semantics
        )
        reviewer = SelfHostedModel(
            cfg, cfg.models.reviewer, http=QuoteWire(cfg.models.reviewer, defect=defect)
        )
        await reviewer.semantic_review_part(
            "map the organization",
            doc,
            0,
            len(doc.extracted.text),
            cfg.semantics,
            original,
            review_selections(cfg.semantics.verification, original)[-1],
        )

    with pytest.raises(ModelFailure) as refused:
        asyncio.run(client_review())
    call = refused.value.model_call
    assert call.output_contract_failure.reason == reason
    assert call.outcome == "refused" and call.status == 200
    assert "丙委員會" not in call.model_dump_json()


def test_template_table_covers_native_window_and_preserves_repeated_occurrences():
    reference = "cite:" + "1" * 64
    text = "甲。\n甲。\n乙。"
    templates = native_quote_templates(text, reference)
    assert templates[0].quote == text
    assert tuple(text[t.start : t.end] for t in templates) == tuple(t.quote for t in templates)
    repeated = [t for t in templates[1:] if t.quote == "甲。\n"]
    assert len(repeated) == 2 and repeated[0].quote_id != repeated[1].quote_id
    assert [t.occurrence for t in repeated] == [0, 1]
    for t in templates:
        assert type(t).model_validate_json(t.model_dump_json()) == t


def test_retained_selected_quote_cannot_be_rewritten_or_erased(tmp_path):
    cfg = quote_config(tmp_path)
    _, rows, _ = run_stage(
        cfg, SemanticWire(cfg.models.analyst), QuoteWire(cfg.models.reviewer), (native_document(),)
    )
    review = rows[-1].semantic_window.review.parts[-1].review
    altered = deepcopy(review.model_dump())
    altered["coverage_findings"][0]["evidence"]["surface"] = "甲委員會"
    with pytest.raises(ValidationError):
        type(review).model_validate(altered)
    altered = deepcopy(rows[-1].model_dump())
    altered["semantic_window"]["review"]["parts"][-1]["review"].pop("quote_response")
    with pytest.raises((ValueError, GhimeraRefused)):
        changed = type(rows[-1]).model_validate(altered)
        validate_rows(cfg, rows[:-1] + (changed,))


def test_old_profile_does_not_gain_quote_prompt_fields(tmp_path):
    cfg = dimension_config(tmp_path)
    wire = DimensionWire(cfg.models.reviewer)
    run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (document(),))
    assert all("native_quote_templates" not in packet for _, packet in wire.requests)


def test_quote_profile_requires_explicit_batched_schema_provider(tmp_path):
    raw = quote_config(tmp_path).model_dump()
    raw["models"]["reviewer"]["response_format"] = "json_object"
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)


def test_native_quote_template_identity_cannot_hide_a_changed_source():
    template = native_quote_templates("甲。", "cite:" + "1" * 64)[0]
    for field, value in (("quote", "乙。"), ("occurrence", -1), ("end", 1), ("quote_id", "0" * 64)):
        with pytest.raises(ValidationError):
            type(template).model_validate(dict(template.model_dump(), **{field: value}))


def test_entire_template_table_must_match_original_context_on_replay(tmp_path):
    from ghimera.evidence_context import native_citation
    from ghimera.model_citations import citation_id
    from ghimera.semantic_grounding import validate_grounded_review

    cfg, doc = quote_config(tmp_path), native_document()
    _, rows, _ = run_stage(
        cfg, SemanticWire(cfg.models.analyst), QuoteWire(cfg.models.reviewer), (doc,)
    )
    window = rows[-1].semantic_window
    review = window.review.parts[-1].review
    raw = review.model_dump(mode="json")
    # The chosen clause is unchanged, but an unchosen alternative is altered.
    raw["quote_response"]["templates"][1] = native_quote_templates(
        "另一个原文。", review.quote_response.templates[0].citation_id
    )[0].model_dump()
    changed = type(review).model_validate(raw)
    with pytest.raises(GhimeraRefused):
        validate_grounded_review(
            cfg.semantics,
            window.proposal,
            changed,
            doc.extracted.text,
            citation_id(native_citation(doc, 0, len(doc.extracted.text))),
        )


def test_safe_example_selects_only_a_wire_profile_not_a_runtime():
    raw = tomllib.loads(Path("examples/review-native-quotes.toml").read_text())
    config = SemanticVerificationConfig.model_validate(raw)
    assert config.prompt_profile == "native_quote_checks"
    assert config.effective_prompt_revision == "ghimera-semantic-verification/8"
    assert not {"endpoint", "node", "gpu", "model_id", "credential"} & raw.keys()
