"""Real model-protocol HTTP fixtures, never claimed as real LLM accuracy."""

import asyncio
import hashlib
import json
import threading
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from chimera.doubles import FakeExtractor, FakeRoute, KeywordScorer
from chimera.fetch import FetchLadder
from chimera.loop import GoalLoop
from chimera.model_client import SelfHostedModel
from chimera.model_config import ModelServiceConfig
from chimera.models import Document, Extracted, Goal, Verdict
from chimera.refusals import ChimeraRefused
from chimera.research import ResearchLoop
from chimera.research_types import ResearchRequest
from tests.test_c0 import config
from tests.test_intent_research import SearchFixture, policy


def service(port, **changes):
    raw = {
        "schema": "chimera.model-service/1",
        "endpoint": f"http://127.0.0.1:{port}/v1/chat/completions",
        "approved_addresses": ["127.0.0.1"],
        "allow_plaintext": True,
        "allow_plaintext_credentials": False,
        "authorization": "none",
        "model_id": "protocol-fixture",
        "revision": "fixture-1",
        "served_model": "fixture-model",
        "timeout_seconds": 2.0,
        "max_request_bytes": 100000,
        "max_response_bytes": 100000,
        "max_header_bytes": 8192,
        "max_output_tokens": 2048,
        "temperature": 0.0,
        "top_p": 1.0,
        "response_format": "json_object",
        "require_usage": True,
        "max_input_chars": 20000,
        "context": {
            "schema": "chimera.evidence-context/1",
            "max_documents": 8,
            "max_chars": 3000,
            "window_chars": 1000,
            "max_windows_per_document": 2,
            "overlap_chars": 50,
        },
    }
    raw.update(changes)
    return ModelServiceConfig.model_validate(raw)


@pytest.fixture
def endpoint():
    seen = []
    state = {"status": 200, "failure": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, request, self.headers.get("Authorization")))
            payload = json.loads(request["messages"][1]["content"])
            task = payload["task"]
            if task == "plan":
                result = {
                    "questions": [{"id": "q1", "text": "Which port?"}],
                    "queries": [{"text": "port report", "question_ids": ["q1"]}],
                }
            elif task == "verdict":
                result = {
                    "decision": "accept",
                    "kind": "report",
                    "publisher": "fixture",
                    "language": "en",
                    "reason": "protocol fixture only",
                }
            elif task == "assessment":
                citation = payload["evidence"]["windows"][0]["citation"]
                result = {
                    "coverage": [
                        {
                            "question_id": "q1",
                            "status": "answered",
                            "reason": "fixture",
                            "citations": [citation],
                        }
                    ]
                }
            elif task == "answer":
                citation = payload["assessment"]["coverage"][0]["citations"][0]
                result = {
                    "claims": [
                        {"text": "Port evidence.", "question_ids": ["q1"], "citations": [citation]}
                    ],
                    "confidence": 0.95,
                }
            elif task == "review":
                result = {
                    "answer_digest": payload["answer_digest"],
                    "intent_covered": True,
                    "reason": "fixture",
                    "claims": [{"index": 0, "verdict": "supported", "reason": "fixture"}],
                }
            else:
                result = {"satisfied": True, "confidence": 0.95, "reason": "fixture"}
            wire = {
                "id": "fixture-call",
                "model": "fixture-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(result),
                            "refusal": None,
                        },
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
            }
            failure = state["failure"]
            if failure == "truncated":
                wire["choices"][0]["finish_reason"] = "length"
            elif failure == "wrong_model":
                wire["model"] = "different"
            elif failure == "missing_usage":
                wire.pop("usage")
            elif failure == "invalid_json":
                wire["choices"][0]["message"]["content"] = "not json"
            elif failure == "oversized":
                wire["padding"] = "x" * 200000
            raw = json.dumps(wire).encode()
            self.send_response(state["status"])
            if state["status"] == 302:
                self.send_header("Location", "/elsewhere")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, seen, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_served_model_protocol_completes_intent_with_usage_and_native_citations(endpoint):
    cfg = config(research=policy(max_model_input_chars=20000), page_budget=30)
    model = SelfHostedModel(cfg, service(endpoint[0]))
    reviewer = SelfHostedModel(cfg, service(endpoint[0], model_id="protocol-reviewer"))
    collector = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=model,
    )
    loop = ResearchLoop(
        config=cfg,
        collector=collector,
        search=SearchFixture(),
        planner=model,
        analyst=model,
        reviewer=reviewer,
    )
    result = asyncio.run(loop.run(ResearchRequest(intent="Which port?")))
    assert result.status == "answered"
    assert result.harvest.receipt.judge_calls == len(endpoint[1]) == 5
    evidence = [row.model_call for row in result.harvest.ledger if row.model_call]
    assert len(evidence) == 5 and sum(item.total_tokens for item in evidence) == 100
    assert all(item.request_sha256 and item.response_sha256 for item in evidence)
    assert [json.loads(row[1]["messages"][1]["content"])["task"] for row in endpoint[1]] == [
        "plan",
        "verdict",
        "assessment",
        "answer",
        "review",
    ]
    assert all(row[0] == "/v1/chat/completions" and row[2] is None for row in endpoint[1])


@pytest.mark.parametrize(
    "failure", ["truncated", "wrong_model", "missing_usage", "invalid_json", "redirect"]
)
def test_bad_model_responses_refuse_without_fallback_and_preserve_evidence(endpoint, failure):
    if failure == "redirect":
        endpoint[2]["status"] = 302
    else:
        endpoint[2]["failure"] = failure
    cfg = config(research=policy())
    model = SelfHostedModel(cfg, service(endpoint[0]))
    with pytest.raises(ChimeraRefused, match="model_unavailable") as refused:
        asyncio.run(
            model.document(
                Goal(text="ports"),
                Extracted(title="Report", text="Port A.", language="en"),
                second_look=False,
            )
        )
    assert len(endpoint[1]) == 1
    assert refused.value.model_call.response_sha256


def test_large_documents_are_explicit_native_windows_not_raw_payloads(endpoint):
    cfg = config(research=policy())
    model = SelfHostedModel(cfg, service(endpoint[0]))
    text = "begin " + "irrelevant " * 2000 + "Port A opened yesterday."
    raw = b"do-not-send-raw-file"
    document = Document(
        url="https://example.org/report",
        sha256=hashlib.sha256(raw).hexdigest(),
        raw=raw,
        extracted=Extracted(title="Report", text=text, language="en"),
        verdict=Verdict(
            decision="accept", kind="report", publisher="fixture", language="en", reason="fixture"
        ),
    )
    asyncio.run(model.grade(Goal(text="Port A"), (document,)))
    body = endpoint[1][0][1]["messages"][1]["content"]
    assert "do-not-send-raw-file" not in body
    payload = json.loads(body)
    windows = payload["evidence"]["windows"]
    assert sum(len(item["citation"]["quote"]) for item in windows) <= 3000
    assert any("Port A opened" in item["citation"]["quote"] for item in windows)
    assert payload["evidence"]["documents"][0]["omitted_chars"] > 0
    for window in windows:
        citation = window["citation"]
        assert citation["quote"] == text[citation["start"] : citation["end"]]


def test_private_model_policy_and_credential_boundary_fail_before_io():
    with pytest.raises(ValidationError):
        service(
            8000,
            endpoint="https://api.openai.com/v1/chat/completions",
            approved_addresses=["8.8.8.8"],
        )
    with pytest.raises(ValidationError):
        service(8000, approved_addresses=["169.254.169.254"])
    with pytest.raises(ValidationError):
        service(8000, allow_plaintext=False)
    with pytest.raises(ValueError, match="credential"):
        SelfHostedModel(
            config(), service(8000, authorization="bearer"), credential=SecretStr("fixture-only")
        )


def test_model_dns_must_match_every_approved_address_before_post(endpoint):
    class ReboundResolver:
        async def resolve(self, host, port):
            return ("127.0.0.1", "8.8.8.8")

    configured = service(
        endpoint[0], endpoint=f"http://model.fixture:{endpoint[0]}/v1/chat/completions"
    )
    model = SelfHostedModel(config(), configured, resolver=ReboundResolver())
    with pytest.raises(ChimeraRefused, match="model_unavailable"):
        asyncio.run(
            model.document(
                Goal(text="ports"),
                Extracted(title="Report", text="Port A.", language="en"),
                second_look=False,
            )
        )
    assert not endpoint[1]


def test_model_response_callback_is_bounded_and_failure_is_recorded(endpoint):
    endpoint[2]["failure"] = "oversized"
    model = SelfHostedModel(config(), service(endpoint[0], max_response_bytes=1000))
    with pytest.raises(ChimeraRefused, match="model_unavailable") as refused:
        asyncio.run(
            model.document(
                Goal(text="ports"),
                Extracted(title="Report", text="Port A.", language="en"),
                second_look=False,
            )
        )
    assert len(endpoint[1]) == 1
    assert refused.value.model_call.response_bytes == 1000
    assert refused.value.model_call.usage is None


def test_model_bearer_is_sent_only_to_configured_service_not_call_evidence(endpoint):
    configured = service(endpoint[0], authorization="bearer", allow_plaintext_credentials=True)
    model = SelfHostedModel(config(), configured, credential=SecretStr("synthetic-fixture-bearer"))
    verdict = asyncio.run(
        model.document(
            Goal(text="ports"),
            Extracted(title="Report", text="Port A.", language="en"),
            second_look=False,
        )
    )
    assert endpoint[1][0][2] == "Bearer synthetic-fixture-bearer"
    assert "synthetic-fixture-bearer" not in verdict.model_dump_json()


def test_required_citations_are_preserved_before_discretionary_context():
    from chimera.evidence_context import ContextSelector, native_citation

    documents = []
    for index in range(3):
        raw = f"raw-{index}".encode()
        documents.append(
            Document(
                url=f"https://example.org/{index}",
                sha256=hashlib.sha256(raw).hexdigest(),
                raw=raw,
                extracted=Extracted(
                    title="Report", text=f"Native evidence {index}.", language="en"
                ),
                verdict=Verdict(
                    decision="accept",
                    kind="report",
                    publisher="fixture",
                    language="en",
                    reason="fixture",
                ),
            )
        )
    policy = service(8000).context.model_copy(update={"max_documents": 1})
    citation = native_citation(documents[-1], 0, len(documents[-1].extracted.text))
    context = ContextSelector(policy).build("evidence", tuple(documents), required=(citation,))
    assert context.windows[0].citation == citation
    assert len(context.documents) == 1 and len(context.omitted_documents) == 2


def test_model_metadata_cannot_exceed_prompt_budget_before_wire(endpoint):
    model = SelfHostedModel(config(), service(endpoint[0], max_input_chars=50))
    with pytest.raises(ChimeraRefused, match="budget_exhausted") as refused:
        asyncio.run(
            model.document(
                Goal(text="ports"),
                Extracted(title="Report", text="Port A.", language="en"),
                second_look=False,
            )
        )
    assert not endpoint[1] and refused.value.model_call.response_bytes == 0


def test_model_prompt_limit_includes_system_schema_not_only_user_context(endpoint):
    model = SelfHostedModel(config(), service(endpoint[0], max_input_chars=900))
    with pytest.raises(ChimeraRefused, match="budget_exhausted") as refused:
        asyncio.run(
            model.document(
                Goal(text="ports"),
                Extracted(title="Report", text="Port A.", language="en"),
                second_look=False,
            )
        )
    assert not endpoint[1]
    assert refused.value.model_call.input_chars > 900


def test_typed_role_bindings_and_example_need_no_python_allocation_edits():
    from chimera.model_client import SelfHostedModels

    with Path("examples/model-service.toml").open("rb") as stream:
        example = ModelServiceConfig.model_validate(tomllib.load(stream))
    assert example.authorization == "none" and not example.allow_plaintext
    primary = service(8000).model_dump(mode="json", by_alias=True)
    review = service(8001, model_id="reviewer").model_dump(mode="json", by_alias=True)
    bindings = {
        "schema": "chimera.model-bindings/1",
        "planner": primary,
        "analyst": primary,
        "judge": primary,
        "reviewer": review,
    }
    cfg = config(research=policy(), models=bindings)
    roles = SelfHostedModels.from_config(cfg)
    assert roles.reviewer.model.model_id == "reviewer"
    assert roles.analyst.model.location == "self_hosted"
    with pytest.raises(ValidationError, match="distinct"):
        config(research=policy(), models={**bindings, "reviewer": primary})
