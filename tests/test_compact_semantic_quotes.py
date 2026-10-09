"""Compact request conformance, not model-quality or token-throughput evidence."""

import asyncio
import hashlib
import json
import tomllib
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.model_client import SelfHostedModel
from ghimera.refusals import GhimeraRefused
from ghimera.semantic_batching import review_selections
from ghimera.semantic_graph import validate_rows
from ghimera.semantic_types import SemanticProposal, SemanticVerificationConfig
from tests.test_semantic_graph import SemanticWire
from tests.test_semantic_quote_selection import QuoteWire, native_document, quote_config
from tests.test_semantic_verification import run_stage


def compact_config(tmp_path):
    raw = quote_config(tmp_path).model_dump()
    raw["semantics"]["verification"]["prompt_profile"] = "compact_native_quote_checks"
    return GhimeraConfig.model_validate(raw)


def test_original_native_quote_requests_keep_the_frozen_full_wire():
    cfg = quote_config(Path("/tmp/ghimera-native-quote-legacy-fixture"))
    wire = QuoteWire(cfg.models.reviewer)

    async def original_requests():
        doc = native_document()
        extractor = SelfHostedModel(cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst))
        observation = await extractor.semantic_extract(
            "map the organization", doc, 0, len(doc.extracted.text), cfg.semantics
        )
        assert observation.model_call is not None
        # This is a synthetic wire fixture with a fixed clock, not a retained
        # real result. Preserve all mandatory source/service/input bindings.
        proposal = SemanticProposal.model_validate(
            dict(
                observation.model_dump(),
                model_call=dict(observation.model_call.model_dump(), latency_seconds=0.0),
            )
        )
        reviewer = SelfHostedModel(cfg, cfg.models.reviewer, http=wire)
        for selection in review_selections(cfg.semantics.verification, proposal):
            await reviewer.semantic_review_part(
                "map the organization",
                doc,
                0,
                len(doc.extracted.text),
                cfg.semantics,
                proposal,
                selection,
            )

    asyncio.run(original_requests())
    hashes = tuple(
        hashlib.sha256(
            json.dumps(request, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        ).hexdigest()
        for request, _ in wire.requests
    )
    # Measured independently on untouched 2451edcb under C0 Python 3.11.16.
    # The fixture excludes timing variance, not any original wire field.
    assert hashes == (
        "093ab95d3a5390c6deb81c565c94884860b207cf81f13ed4b3ae86c240fc4e22",
        "4681b55bfc1cc52fb4998e3b624ece1e4dd0772bd224436ca53d079099f4c882",
        "fdff4192b15dff6561b69baa5d56ae9033de012b31db3fa0400c83104e460603",
        "2f3ab545a9df9ca9d218ff26cd9d7f80b497e7cc5cce878822fc93011b351345",
    )


def test_compact_profile_preserves_source_proposal_and_full_native_replay(tmp_path):
    cfg, doc = compact_config(tmp_path), native_document()
    wire = QuoteWire(cfg.models.reviewer)
    _, rows, _ = run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (doc,))
    window = rows[-1].semantic_window
    for (request, packet), part in zip(wire.requests, window.review.parts, strict=True):
        assert "native_quote_templates" not in packet
        assert packet["evidence"]["windows"][0]["citation"]["quote"] == doc.extracted.text
        assert packet["semantic_proposal"] == wire.requests[0][1]["semantic_proposal"]
        assert packet["proposal_digest"] == window.proposal.content_digest()
        assert part.review.model_call.prompt_revision == "ghimera-semantic-verification/9"
        templates = part.review.quote_response.templates
        assert templates[0].quote == doc.extracted.text
        assert all(doc.extracted.text[t.start : t.end] == t.quote for t in templates)
        schema = request["response_format"]["json_schema"]["schema"]
        Draft202012Validator.check_schema(schema)
        if part.selection.coverage:
            assert packet["native_quote_choices"] == [
                {"quote_id": t.quote_id, "quote": t.quote} for t in templates
            ]
            assert schema["$defs"]["NativeQuoteReference"]["properties"]["quote_id"]["enum"] == [
                t.quote_id for t in templates
            ]
        else:
            assert "native_quote_choices" not in packet
            assert schema["properties"]["coverage_findings"]["maxItems"] == 0
            assert schema["properties"]["coverage"]["enum"] == ["uncertain"]
    assert validate_rows(cfg, rows) == (rows[-1],)
    assert type(window).model_validate_json(window.model_dump_json()) == window
    assert window.review.coverage_findings[0].evidence.surface == "丙委員會隸屬乙委員會。"
    with pytest.raises(ValueError, match="independent configured service"):
        validate_rows(quote_config(tmp_path), rows)


def test_compaction_reduces_request_overhead_without_dropping_coverage_choices(tmp_path):
    old, compact = quote_config(tmp_path), compact_config(tmp_path)
    before, after = QuoteWire(old.models.reviewer), QuoteWire(compact.models.reviewer)
    for cfg, wire in ((old, before), (compact, after)):
        run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (native_document(),))
    assert len(before.requests) == len(after.requests)
    for (old_request, _), (new_request, _) in zip(before.requests, after.requests, strict=True):
        old_size = sum(len(message["content"]) for message in old_request["messages"])
        new_size = sum(len(message["content"]) for message in new_request["messages"])
        assert new_size < old_size


@pytest.mark.parametrize(
    "defect", ["unknown", "wrong_clause", "duplicate_proposal", "coverage_shape"]
)
def test_compact_projection_does_not_relax_original_quote_grounding(tmp_path, defect):
    cfg = compact_config(tmp_path)
    with pytest.raises(GhimeraRefused):
        run_stage(
            cfg,
            SemanticWire(cfg.models.analyst),
            QuoteWire(cfg.models.reviewer, defect=defect),
            (native_document(),),
        )


def test_compact_profile_requires_explicit_batched_schema_provider(tmp_path):
    cfg = compact_config(tmp_path)
    raw = cfg.model_dump()
    raw["models"]["reviewer"]["response_format"] = "json_object"
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)
    raw = cfg.semantics.verification.model_dump()
    raw["schema"] = "ghimera.semantic-verification/3"
    raw.pop("max_mentions_per_call")
    raw.pop("max_relations_per_call")
    with pytest.raises(ValidationError):
        SemanticVerificationConfig.model_validate(raw)


def test_compact_example_selects_wire_policy_not_an_operational_runtime():
    raw = tomllib.loads(Path("examples/review-compact-native-quotes.toml").read_text())
    policy = SemanticVerificationConfig.model_validate(raw)
    assert policy.effective_prompt_revision == "ghimera-semantic-verification/9"
    assert not {"endpoint", "node", "gpu", "model_id", "credential"} & raw.keys()
