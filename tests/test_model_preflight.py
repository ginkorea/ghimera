"""Actual serializer inspection; fixture answers do not establish model quality."""

import asyncio
import hashlib

import pytest

from ghimera.config import GhimeraConfig
from ghimera.model_client import SelfHostedModel
from ghimera.model_http import PinnedModelHttp
from ghimera.model_preflight import preflight_semantic_review
from ghimera.refusals import GhimeraRefused, ModelFailure
from ghimera.semantic_batching import review_selections
from tests.test_semantic_graph import SemanticWire
from tests.test_semantic_quote_selection import QuoteWire, native_document, quote_config
from tests.test_semantic_verification import reviewed_config, run_stage


class RecordingQuoteWire(QuoteWire):
    def __init__(self, service):
        super().__init__(service)
        self.bodies = []

    async def post(self, body):
        self.bodies.append(body)
        return await super().post(body)


def original_proposal(config, doc):
    return asyncio.run(
        SelfHostedModel(
            config, config.models.analyst, http=SemanticWire(config.models.analyst)
        ).semantic_extract("organization", doc, 0, len(doc.extracted.text), config.semantics)
    )


def inspect(config, doc, proposal):
    return asyncio.run(
        preflight_semantic_review(
            config, "organization", doc, 0, len(doc.extracted.text), config.semantics, proposal
        )
    )


def test_preflight_matches_every_actual_request_without_network_or_credentials(
    tmp_path, monkeypatch
):
    cfg, doc = quote_config(tmp_path), native_document()
    proposal = original_proposal(cfg, doc)
    original = proposal.model_dump_json()

    async def forbidden(*args, **kwargs):
        raise AssertionError("preflight must not use a network transport")

    monkeypatch.setattr(PinnedModelHttp, "post", forbidden)
    footprints = inspect(cfg, doc, proposal)
    wire = RecordingQuoteWire(cfg.models.reviewer)
    model = SelfHostedModel(cfg, cfg.models.reviewer, http=wire)
    for selection in review_selections(cfg.semantics.verification, proposal):
        asyncio.run(
            model.semantic_review_part(
                "organization", doc, 0, len(doc.extracted.text), cfg.semantics, proposal, selection
            )
        )
    assert len(footprints) == len(wire.bodies) == 4
    for footprint, body in zip(footprints, wire.bodies, strict=True):
        assert footprint.request_bytes == len(body)
        assert footprint.request_sha256 == hashlib.sha256(body).hexdigest()
        assert footprint.input_chars <= cfg.models.reviewer.max_input_chars
        with pytest.raises(AttributeError):
            footprint.input_chars = 0
    assert proposal.model_dump_json() == original


@pytest.mark.parametrize("bound", ["max_input_chars", "max_request_bytes"])
def test_oversized_request_refuses_before_any_network(tmp_path, monkeypatch, bound):
    cfg, doc = quote_config(tmp_path), native_document()
    proposal = original_proposal(cfg, doc)
    raw = cfg.model_dump()
    raw["models"]["reviewer"][bound] = 1
    cfg = GhimeraConfig.model_validate(raw)

    async def forbidden(*args, **kwargs):
        raise AssertionError("oversized preflight must not use network")

    monkeypatch.setattr(PinnedModelHttp, "post", forbidden)
    with pytest.raises(ModelFailure, match="budget_exhausted") as refused:
        inspect(cfg, doc, proposal)
    assert refused.value.model_call.status is None
    assert refused.value.model_call.response_bytes == 0


def test_preflight_includes_single_call_legacy_profile(tmp_path):
    cfg, doc = reviewed_config(tmp_path), native_document()
    proposal = original_proposal(cfg, doc)
    assert len(inspect(cfg, doc, proposal)) == 1


def test_preflight_preserves_proposal_grounding_refusal(tmp_path):
    cfg, doc = quote_config(tmp_path), native_document()
    proposal = original_proposal(cfg, doc)
    damaged = proposal.model_copy(update={"model_call": None})
    with pytest.raises(GhimeraRefused):
        inspect(cfg, doc, damaged)


def test_preparation_cannot_substitute_the_objects_bound_model(tmp_path):
    cfg, doc = quote_config(tmp_path), native_document()
    proposal = original_proposal(cfg, doc)
    model = SelfHostedModel(cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst))
    with pytest.raises(GhimeraRefused):
        asyncio.run(
            model.prepare_semantic_review(
                "organization", doc, 0, len(doc.extracted.text), cfg.semantics, proposal
            )
        )


def test_later_oversized_partition_refuses_complete_preflight(tmp_path):
    cfg, doc = quote_config(tmp_path), native_document()
    proposal = original_proposal(cfg, doc)
    footprints = inspect(cfg, doc, proposal)
    # The date-bound relation grammar makes a later request larger than the
    # first item request. Earlier fit must not be presented as whole-batch fit.
    assert max(f.request_bytes for f in footprints[1:]) > footprints[0].request_bytes
    raw = cfg.model_dump()
    raw["models"]["reviewer"]["max_request_bytes"] = footprints[0].request_bytes
    cfg = GhimeraConfig.model_validate(raw)
    with pytest.raises(ModelFailure, match="budget_exhausted"):
        inspect(cfg, doc, proposal)


def test_real_model_stage_prepares_all_parts_before_reserving_or_calling_review(
    tmp_path, monkeypatch
):
    from ghimera.budget import RunBudget

    cfg, doc = quote_config(tmp_path), native_document()
    proposal = original_proposal(cfg, doc)
    footprint = inspect(cfg, doc, proposal)[0]
    raw = cfg.model_dump()
    raw["models"]["reviewer"]["max_request_bytes"] = footprint.request_bytes
    cfg = GhimeraConfig.model_validate(raw)
    wire = RecordingQuoteWire(cfg.models.reviewer)

    def forbidden(*args, **kwargs):
        raise AssertionError("no review budget may be reserved for an oversized batch")

    monkeypatch.setattr(RunBudget, "reserve_semantic_review", forbidden)
    with pytest.raises(GhimeraRefused, match="budget_exhausted"):
        run_stage(cfg, SemanticWire(cfg.models.analyst), wire, (doc,))
    assert wire.bodies == []
