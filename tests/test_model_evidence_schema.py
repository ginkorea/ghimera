"""Client-owned gateway evidence must not revise historical model-facing shapes."""

import pytest
from pydantic import BaseModel, ValidationError

from ghimera.model_config import ModelBindingsConfig, ModelServiceConfig
from ghimera.model_types import ModelCallEvidence
from tests.test_model_gateway import configured


class EvidenceFirst(BaseModel):
    call: ModelCallEvidence
    models: ModelBindingsConfig
    service: ModelServiceConfig


class OperationalFirst(BaseModel):
    service: ModelServiceConfig
    models: ModelBindingsConfig
    call: ModelCallEvidence


def resolved(root, shape):
    return root["$defs"][shape["$ref"].removeprefix("#/$defs/")] if "$ref" in shape else shape


@pytest.mark.parametrize("shape", [EvidenceFirst, OperationalFirst])
@pytest.mark.parametrize("mode", ["validation", "serialization"])
@pytest.mark.parametrize("by_alias", [True, False])
def test_evidence_projection_is_isolated_from_operational_bindings(shape, mode, by_alias):
    schema_key = "schema" if by_alias else "schema_version"
    schema = shape.model_json_schema(mode=mode, by_alias=by_alias)
    call = resolved(schema, schema["properties"]["call"])
    projected = resolved(schema, call["properties"]["service"])
    operational = resolved(schema, schema["properties"]["service"])
    bindings = resolved(schema, schema["properties"]["models"])
    assert "gateway" not in projected["properties"]
    assert projected["properties"][schema_key]["enum"] == [
        "chimera.model-service/1",
        "chimera.model-service/2",
    ]
    assert "gateway" in operational["properties"]
    assert operational["properties"][schema_key]["enum"] == [
        "chimera.model-service/1",
        "chimera.model-service/2",
        "chimera.model-service/3",
    ]
    for role in ("planner", "analyst", "reviewer", "judge"):
        assert resolved(schema, bindings["properties"][role]) == operational


@pytest.mark.parametrize("mode", ["validation", "serialization"])
def test_legacy_evidence_schema_prunes_gateway_only_and_operational_schema_stays_truthful(mode):
    evidence = ModelCallEvidence.model_json_schema(mode=mode)
    assert "SelfHostedGatewayConfig" not in evidence["$defs"]
    projection = evidence["$defs"]["ModelServiceConfig"]
    assert "gateway" not in projection["properties"]
    assert "LocalGenerationConfig" in evidence["$defs"]
    operational = ModelServiceConfig.model_json_schema(mode=mode)
    assert "gateway" in operational["properties"]
    assert operational["$defs"]["SelfHostedGatewayConfig"]["properties"]["address_scope"][
        "enum"
    ] == ["global", "global_or_shared"]


def test_full_gateway_evidence_keeps_native_validation_serialization_and_identity():
    service = configured(
        approved_addresses=("100.64.0.2",),
        gateway=dict(
            schema="ghimera.self-hosted-gateway/1",
            origin="https://inference.example.invalid",
            address_scope="global_or_shared",
        ),
    )
    evidence = ModelCallEvidence(
        schema="chimera.model-call/1",
        service=service,
        task="semantic_review",
        prompt_revision="fixture-revision",
        request_sha256="a" * 64,
        response_sha256="b" * 64,
        response_bytes=2,
        status=200,
        latency_seconds=0.0,
        usage=None,
        input_chars=0,
        context_sha256="c" * 64,
        selected_spans=(),
        omitted_document_ids=(),
        omitted_chars=0,
        outcome="success",
    )
    assert type(evidence.service) is ModelServiceConfig and evidence.service is service
    assert evidence.model_dump()["service"] == service.model_dump()
    restored = ModelCallEvidence.model_validate_json(evidence.model_dump_json())
    assert restored == evidence and restored.service == service
    assert restored.service.gateway.address_scope == "global_or_shared"
    raw = evidence.model_dump()
    raw["service"]["gateway"]["address_scope"] = "private"
    with pytest.raises(ValidationError):
        ModelCallEvidence.model_validate(raw)
