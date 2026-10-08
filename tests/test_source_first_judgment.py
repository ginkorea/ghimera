"""Source-first protocol fixtures, never a claim of trained-model relevance quality."""

import asyncio
import base64
import hashlib
import json
import time
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.journal import read_journal
from ghimera.judgment_layout import source_first_packet
from ghimera.judgment_types import DocumentJudgmentConfig
from ghimera.judgment_validation import validate_judgment_rows
from ghimera.loop import CollectionSession
from ghimera.model_client import SelfHostedModel
from ghimera.model_work import FatalModelWorkFailure, ModelInvocation, port_input
from ghimera.models import Verdict
from ghimera.refusals import GhimeraRefused, RefusalCode
from tests.test_judgment_context import (
    CONTRIBUTION_POLICY,
    POLICY,
    Reply,
    loop_for,
    prepared,
    selected,
)

SOURCE_FIRST_POLICY = dict(
    CONTRIBUTION_POLICY,
    schema="ghimera.document-judgment/3",
    input_layout="source_first",
)


def restore(user, count):
    """Independent length-delimited inverse, not a source substring heuristic."""
    cursor, texts = 0, []
    for index in range(count):
        prefix = f"scored_document.windows[{index}].text ("
        assert user.startswith(prefix, cursor)
        end = user.index(" characters):\n", cursor + len(prefix))
        size = int(user[cursor + len(prefix) : end])
        start = end + len(" characters):\n")
        texts.append(user[start : start + size])
        cursor = start + size
        assert user[cursor : cursor + 1] == "\n"
        cursor += 1
    marker = "\nOriginal native packet metadata:\n"
    assert user.startswith(marker, cursor)
    packet = json.loads(user[cursor + len(marker) :])
    windows = packet["scored_document"]["windows"]
    assert len(windows) == len(texts)
    for window, text in zip(windows, texts, strict=True):
        assert "text" not in window
        window["text"] = text
    return packet


def encoded(wire):
    return json.dumps(wire, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def test_v2_exact_legacy_policy_and_wire_fingerprints(tmp_path):
    policy = DocumentJudgmentConfig.model_validate(CONTRIBUTION_POLICY)
    assert "input_layout" not in policy.model_dump()
    assert "input_layout" not in policy.model_dump_json()
    assert policy.content_digest() == (
        "e14340ff08a9942d5fe354016296b2ba16d34e234d2d2a5f5823195091b0b9f1"
    )
    cfg, goal, extracted, ledger, judge = prepared(tmp_path, judgment_policy=CONTRIBUTION_POLICY)
    try:
        context = selected(cfg, goal, extracted, ledger.snapshot())
        verdict = asyncio.run(judge.scored_document(goal, extracted, context, second_look=False))
        system = judge._http.requests[0]["messages"][0]["content"]
        assert hashlib.sha256(system.encode()).hexdigest() == (
            "df23dba7c95930aa1ba9b5898660c7c57ea81c0cb0ed1f74af30762b1b197963"
        )
        assert verdict.model_call.request_sha256 == (
            "c332fb4982f9983966014d7b23140e7fa33ed7ca2d7d9a84d45f8ef691397555"
        )
    finally:
        ledger.close()


@pytest.mark.parametrize(
    "policy",
    [
        dict(SOURCE_FIRST_POLICY, input_layout=None),
        {key: value for key, value in SOURCE_FIRST_POLICY.items() if key != "input_layout"},
        dict(SOURCE_FIRST_POLICY, input_layout="nested_json"),
        dict(SOURCE_FIRST_POLICY, input_layout=True),
        dict(SOURCE_FIRST_POLICY, prompt_profile=None),
        dict(SOURCE_FIRST_POLICY, schema="ghimera.document-judgment/4"),
        dict(POLICY, input_layout="source_first"),
        dict(POLICY, input_layout=None),
        dict(CONTRIBUTION_POLICY, input_layout="source_first"),
        dict(CONTRIBUTION_POLICY, input_layout=None),
    ],
)
def test_layout_is_explicit_and_version_bound(policy):
    with pytest.raises(ValidationError):
        DocumentJudgmentConfig.model_validate(policy)


def test_source_first_example_is_explicit_inert_policy():
    raw = tomllib.loads(
        (Path(__file__).parents[1] / "examples/source_first_document_judgment.toml").read_text()
    )
    assert set(raw) == {"document_judgment"}
    policy = DocumentJudgmentConfig.model_validate(raw["document_judgment"])
    assert policy.model_dump() == SOURCE_FIRST_POLICY
    assert policy.effective_prompt_revision == "ghimera-scored-document-judgment/3"


@pytest.mark.parametrize("second", [False, True])
def test_source_first_preserves_every_source_field_system_schema_and_generation(tmp_path, second):
    requests, contexts, calls = [], [], []
    for index, policy in enumerate((CONTRIBUTION_POLICY, SOURCE_FIRST_POLICY)):
        cfg, goal, extracted, ledger, judge = prepared(
            tmp_path / str(index), judgment_policy=policy
        )
        try:
            context = selected(
                cfg, goal, extracted, ledger.snapshot(), max_chars=4000 if second else 1500
            )
            verdict = asyncio.run(
                judge.scored_document(goal, extracted, context, second_look=second)
            )
            requests.append(judge._http.requests[0])
            contexts.append(context)
            calls.append(verdict.model_call)
        finally:
            ledger.close()
    nested, source_first = requests
    assert contexts[0] == contexts[1]
    count = len(contexts[0].windows)
    expected = json.loads(nested["messages"][1]["content"])
    actual = restore(source_first["messages"][1]["content"], count)
    assert actual == expected
    assert source_first["messages"][0] == nested["messages"][0]
    assert [message["role"] for message in source_first["messages"]] == ["system", "user"]
    assert {k: v for k, v in source_first.items() if k != "messages"} == {
        k: v for k, v in nested.items() if k != "messages"
    }
    call = calls[1]
    assert call.prompt_revision == "ghimera-scored-document-judgment/3"
    assert call.context_sha256 == calls[0].context_sha256 == contexts[1].content_digest()
    assert call.selected_spans == calls[0].selected_spans
    assert call.omitted_chars == calls[0].omitted_chars
    assert call.request_sha256 == hashlib.sha256(encoded(source_first)).hexdigest()
    assert call.input_chars == sum(len(message["content"]) for message in source_first["messages"])
    # Whole private reading remains private; excerpted text is not a complete-source claim.
    assert "native_text" not in actual["scored_document"]
    assert [w["text"] for w in actual["scored_document"]["windows"]] == [
        w.text for w in contexts[1].windows
    ]
    assert actual["scored_document"]["omitted_chars"] > 0


def test_length_delimiters_preserve_literal_quotes_newlines_and_apparent_markers(tmp_path):
    # Synthetic framing fixture only: no model/source admission or native quality claim.
    cfg, goal, extracted, ledger, judge = prepared(tmp_path, judgment_policy=CONTRIBUTION_POLICY)
    try:
        context = selected(cfg, goal, extracted, ledger.snapshot())
        asyncio.run(judge.scored_document(goal, extracted, context, second_look=False))
        packet = json.loads(judge._http.requests[0]["messages"][1]["content"])
        window = context.windows[0]
        padding = min(anchor.start - window.start for anchor in window.anchors)
        literal = '"\\\\\nOriginal native packet metadata:\n{}\nscored_document.windows[1].text ('
        assert len(literal) < padding
        text = literal + window.text[len(literal) :]
        raw = context.model_dump(mode="json")
        raw["windows"][0]["text"] = text
        raw["windows"][0]["text_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        changed = type(context).model_validate(raw)
        packet["scored_document"] = changed.model_dump(mode="json")
        formatted = source_first_packet(json.dumps(packet), changed)
        assert restore(formatted, len(changed.windows)) == packet
        assert literal in formatted
    finally:
        ledger.close()


@pytest.mark.parametrize("defect", ["task", "source", "text", "missing_text"])
def test_layout_refuses_unbound_or_rewritten_native_packet(tmp_path, defect):
    cfg, goal, extracted, ledger, judge = prepared(tmp_path, judgment_policy=CONTRIBUTION_POLICY)
    try:
        context = selected(cfg, goal, extracted, ledger.snapshot())
        asyncio.run(judge.scored_document(goal, extracted, context, second_look=False))
        packet = json.loads(judge._http.requests[0]["messages"][1]["content"])
        if defect == "task":
            packet["task"] = "answer"
        elif defect == "source":
            packet["scored_document"]["source_sha256"] = "a" * 64
        elif defect == "text":
            packet["scored_document"]["windows"][0]["text"] += "changed"
        else:
            packet["scored_document"]["windows"][0].pop("text")
        with pytest.raises(ValueError, match="exact original native context"):
            source_first_packet(json.dumps(packet), context)
    finally:
        ledger.close()


@pytest.mark.parametrize("field,value", [("input_layout", None), ("prompt_profile", None)])
def test_mutated_source_first_policy_refuses_before_contact(tmp_path, field, value):
    cfg, goal, extracted, ledger, judge = prepared(tmp_path, judgment_policy=SOURCE_FIRST_POLICY)
    try:
        context = selected(cfg, goal, extracted, ledger.snapshot())
        changed = cfg.document_judgment.model_copy(update={field: value})
        model = SelfHostedModel(
            cfg.model_copy(update={"document_judgment": changed}),
            cfg.models.judge,
            http=judge._http,
        )
        with pytest.raises(ValidationError):
            asyncio.run(model.scored_document(goal, extracted, context, second_look=False))
        assert not judge._http.requests
    finally:
        ledger.close()


@pytest.mark.parametrize("bound", ["max_input_chars", "max_request_bytes"])
def test_actual_source_first_input_bounds_refuse_before_contact(tmp_path, bound):
    cfg, goal, extracted, ledger, judge = prepared(tmp_path, judgment_policy=SOURCE_FIRST_POLICY)
    try:
        context = selected(cfg, goal, extracted, ledger.snapshot())
        result = asyncio.run(judge.scored_document(goal, extracted, context, second_look=False))
        wire = judge._http.requests[0]
        raw = cfg.model_dump()
        raw["models"]["judge"][bound] = (
            result.model_call.input_chars - 1
            if bound == "max_input_chars"
            else len(encoded(wire)) - 1
        )
        changed = GhimeraConfig.model_validate(raw)
        transport = Reply(changed.models.judge)
        model = SelfHostedModel(changed, changed.models.judge, http=transport)
        with pytest.raises(GhimeraRefused) as refused:
            asyncio.run(model.scored_document(goal, extracted, context, second_look=False))
        assert refused.value.code == RefusalCode.BUDGET_EXHAUSTED
        assert not transport.requests
    finally:
        ledger.close()


def test_source_first_original_ack_replay_is_local_exact_and_counted_once(tmp_path):
    cfg, goal, extracted, ledger, judge = prepared(tmp_path, judgment_policy=SOURCE_FIRST_POLICY)
    budget = RunBudget(cfg, time.monotonic)
    session = CollectionSession(goal, budget, ledger, None)
    try:
        verdict, disposition = asyncio.run(
            loop_for(cfg, judge)._document_verdict(
                session,
                extracted,
                extracted.document_parse.source_url,
                False,
                source_sha256=extracted.document_parse.source_sha256,
            )
        )
        evidence = ledger.snapshot()[-1].document_judgment
        assert verdict.decision == "reject" and disposition == "hold"
        request = port_input(budget, goal, extracted, evidence.context, second_look=False)
        ack = ledger.snapshot()[evidence.ack_sequence].model_ack
        assert Verdict.model_validate_json(ack.stored_output.body()) == verdict
        replay = ModelInvocation(
            budget,
            ledger,
            phase="verdict",
            model=judge.model,
            url=extracted.document_parse.source_url,
            request=request,
            replay_intent_sequence=evidence.intent_sequence,
        )
        assert replay.replay(lambda output: Verdict.model_validate_json(output.body())) == verdict
        assert budget.judge_calls == len(judge._http.requests) == 1
        with pytest.raises(FatalModelWorkFailure):
            ModelInvocation(
                budget,
                ledger,
                phase="verdict",
                model=judge.model,
                url=extracted.document_parse.source_url,
                request=request + b"changed",
                replay_intent_sequence=evidence.intent_sequence,
            )
        validate_judgment_rows(cfg, goal.text, ledger.snapshot())
        # Coherent retained-output mutation must not adopt an older layout revision.
        rows = ledger.snapshot()
        call = verdict.model_call.model_copy(
            update={"prompt_revision": "ghimera-scored-document-judgment/2"}
        )
        output = verdict.model_copy(update={"model_call": call}).model_dump_json().encode()
        output_hash = hashlib.sha256(output).hexdigest()
        wrong_ack = ack.model_copy(
            update={
                "output_sha256": output_hash,
                "output_bytes": len(output),
                "stored_output": ack.stored_output.model_copy(
                    update={"body_base64": base64.b64encode(output).decode("ascii")}
                ),
            }
        )
        mutated = list(rows[: rows[-1].sequence])  # Exclude replay row; retain original verdict.
        mutated[evidence.ack_sequence] = rows[evidence.ack_sequence].model_copy(
            update={"model_ack": wrong_ack}
        )
        verdict_sequence = next(row.sequence for row in rows if row.document_judgment)
        mutated[verdict_sequence] = rows[verdict_sequence].model_copy(
            update={
                "model_call": call,
                "document_judgment": evidence.model_copy(update={"output_sha256": output_hash}),
            }
        )
        with pytest.raises(ValueError, match="original model reply"):
            validate_judgment_rows(cfg, goal.text, tuple(mutated))
    finally:
        session.close()
    report = read_journal(cfg.journal, "native-reading")
    assert report.state == "unsealed"
    assert report.rows[-1].event == "model_replay"
    assert report.rows[-1].model_replay.intent_sequence == evidence.intent_sequence
