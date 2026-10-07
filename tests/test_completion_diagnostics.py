"""Non-secret protocol diagnostics; never promote reasoning into final evidence."""

import asyncio
import json

import pytest

from ghimera.model_client import SelfHostedModel, model_schema
from ghimera.model_http import ModelHttpResponse
from ghimera.model_types import ModelCallEvidence
from ghimera.models import Extracted, Goal, LedgerRow, Verdict
from ghimera.refusals import ModelFailure
from tests.test_c0 import config
from tests.test_served_models import service

PRIVATE = "private-reasoning-fixture-must-not-be-retained"
FINAL = json.dumps(
    {
        "decision": "accept",
        "kind": "report",
        "publisher": "fixture",
        "language": "en",
        "reason": "protocol fixture",
    }
)


class Wire:
    config = service(8769)

    def __init__(
        self,
        *,
        content=FINAL,
        finish="stop",
        model="fixture-model",
        choices=1,
        status=200,
        invalid=False,
        reasoning=PRIVATE,
    ):
        self.calls = 0
        self.status = status
        self.body = (
            b"invalid-envelope"
            if invalid
            else json.dumps(
                {
                    "id": "fixture",
                    "model": model,
                    "choices": [
                        {
                            "index": index,
                            "finish_reason": finish,
                            "message": {
                                "role": "assistant",
                                "content": content,
                                "reasoning_content": reasoning,
                                "refusal": None,
                            },
                        }
                        for index in range(choices)
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
                }
            ).encode()
        )

    async def post(self, body):
        self.calls += 1
        return ModelHttpResponse(self.status, self.body, "application/json")


def invoke(wire):
    model = SelfHostedModel(config(), wire.config, http=wire)
    return asyncio.run(
        model.document(
            Goal(text="ports"),
            Extracted(title="Report", text="Port A.", language="en"),
            second_look=False,
        )
    )


def test_success_records_shape_without_reasoning_or_final_text():
    result = invoke(Wire())
    diagnostic = result.model_call.completion
    assert diagnostic.schema_version == "ghimera.completion-shape/1"
    assert diagnostic.choices == 1 and diagnostic.model_matches
    assert diagnostic.finish_reason == "stop" and diagnostic.final_content == "present"
    assert diagnostic.reasoning_present and not diagnostic.refusal_present
    text = result.model_call.model_dump_json(by_alias=True)
    assert PRIVATE not in text and "protocol fixture" not in text
    assert ModelCallEvidence.model_validate_json(text) == result.model_call
    row = LedgerRow(
        sequence=0, event="verdict", reason="protocol fixture", model_call=result.model_call
    )
    assert LedgerRow.model_validate_json(row.model_dump_json()) == row
    assert "CompletionShape" not in json.dumps(model_schema(Verdict))


def test_reasoning_only_response_is_refused_and_diagnosed_without_fallback():
    wire = Wire(content=None, reasoning=FINAL)
    with pytest.raises(ModelFailure, match="model_unavailable") as refused:
        invoke(wire)
    call = refused.value.model_call
    assert call.status == 200 and call.outcome == "refused"
    assert call.completion.final_content == "missing"
    assert call.completion.reasoning_present and call.completion.finish_reason == "stop"
    assert call.total_tokens == 20 and wire.calls == 1
    assert FINAL not in call.model_dump_json()


@pytest.mark.parametrize("content,state", [(None, "missing"), ("", "empty")])
def test_empty_final_state_is_explicit(content, state):
    with pytest.raises(ModelFailure) as refused:
        invoke(Wire(content=content))
    assert refused.value.model_call.completion.final_content == state


@pytest.mark.parametrize("reasoning", [None, "", {"trace": PRIVATE}])
def test_reasoning_presence_does_not_strengthen_the_original_json_wire(reasoning):
    call = invoke(Wire(reasoning=reasoning)).model_call
    assert call.completion.reasoning_present == bool(reasoning)
    assert PRIVATE not in call.model_dump_json()


@pytest.mark.parametrize("finish,expected", [("length", "length"), (PRIVATE, "other")])
def test_finish_reason_is_bounded_vocabulary_not_untrusted_provider_prose(finish, expected):
    with pytest.raises(ModelFailure) as refused:
        invoke(Wire(finish=finish))
    call = refused.value.model_call
    assert call.completion.finish_reason == expected
    assert PRIVATE not in call.model_dump_json()


@pytest.mark.parametrize("choices", [0, 2])
def test_ambiguous_choice_sets_do_not_claim_one_final_state(choices):
    with pytest.raises(ModelFailure) as refused:
        invoke(Wire(choices=choices))
    diagnostic = refused.value.model_call.completion
    assert diagnostic.choices == choices
    assert diagnostic.finish_reason is None and diagnostic.final_content is None
    assert diagnostic.refusal_present is None and diagnostic.reasoning_present is None


def test_model_mismatch_is_observed_without_retaining_the_returned_identity():
    with pytest.raises(ModelFailure) as refused:
        invoke(Wire(model=PRIVATE))
    call = refused.value.model_call
    assert not call.completion.model_matches
    assert PRIVATE not in call.model_dump_json()


@pytest.mark.parametrize("updates", [{"invalid": True}, {"status": 500}])
def test_unparsed_or_error_envelopes_do_not_fabricate_completion_shape(updates):
    with pytest.raises(ModelFailure) as refused:
        invoke(Wire(**updates))
    call = refused.value.model_call
    assert call.completion is None and "completion" not in call.model_dump()


def test_legacy_call_serialization_is_unchanged_when_diagnostics_are_absent():
    original = invoke(Wire()).model_call.model_dump(mode="json", by_alias=True)
    original.pop("completion")
    restored = ModelCallEvidence.model_validate(original)
    assert restored.completion is None
    assert restored.model_dump(mode="json", by_alias=True) == original
    schema = ModelCallEvidence.model_json_schema()
    assert "completion" not in schema["properties"]
    assert "CompletionShape" not in json.dumps(schema)
