"""Native collector/model wiring; controlled replies are not model-quality evidence."""

import asyncio
import json

from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeExtractor, FakeJudge, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.image_ocr import TesseractOcr
from ghimera.loop import GoalLoop
from ghimera.model_client import VISUAL_PROMPT_REVISION, SelfHostedModel
from ghimera.model_http import ModelHttpResponse
from ghimera.models import Goal, Scope
from ghimera.research_types import EvidenceRequest, Question
from ghimera.visual_evidence import graph_visual_readings
from ghimera.visual_stage import VisualStage
from tests.test_served_models import config, service
from tests.test_visual_evidence_graph import graph_policy, image_document
from tests.test_visuals import VisualRoute, recipe, run_config


def test_collector_projects_one_enriched_parent_and_replays_its_actual_ocr(tmp_path):
    visual = recipe(tmp_path)
    raw = run_config(visual).model_dump(by_alias=True)
    raw["graph"] = graph_policy(tmp_path).model_dump(by_alias=True)
    cfg = GhimeraConfig.model_validate(raw)
    route, judge = VisualRoute(), FakeJudge()
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((route,)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=judge,
        visual_stage=VisualStage(visual, ocr=TesseractOcr(visual), judge=judge),
    )
    harvest = asyncio.run(
        loop.run(
            Goal(text="ports", seeds=("https://example.org/start",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
            run_id="visual-parent-native",
        )
    )
    assert harvest.graph is not None
    parents = [node for node in harvest.graph.nodes if node.role == "document"]
    assert len(parents) == 1
    assert parents[0].visual_readings == graph_visual_readings(harvest.documents[0].images)
    assert parents[0].visual_readings
    assert any(edge.rule == "visual_evidence" for edge in harvest.graph.edges)
    assert harvest.model_validate_json(harvest.model_dump_json()) == harvest


def test_model_answer_context_names_derived_visual_basis_not_native_text():
    bound = service(8769, citation_format="template_ids")

    class Wire:
        config = bound

        def __init__(self):
            self.request = None

        async def post(self, body):
            self.request = json.loads(body)
            packet = json.loads(self.request["messages"][1]["content"])
            window = next(
                item
                for item in packet["evidence"]["windows"]
                if item["citation"]["basis"] == "image_ocr"
            )
            output = {
                "coverage": [
                    {
                        "question_id": "q1",
                        "status": "answered",
                        "reason": "controlled reply",
                        "citations": [{"citation_id": window["citation_id"]}],
                    }
                ]
            }
            reply = {
                "id": "fixture",
                "model": bound.served_model,
                "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(output),
                        },
                    }
                ],
            }
            return ModelHttpResponse(200, json.dumps(reply).encode(), "application/json")

    wire = Wire()
    result = asyncio.run(
        SelfHostedModel(config(), bound, http=wire).assess(
            EvidenceRequest(
                intent="Maritime Bureau",
                questions=(Question(id="q1", text="Which bureau?"),),
                documents=(image_document(),),
            )
        )
    )
    assert result.coverage[0].citations[0].basis == "image_ocr"
    assert result.coverage[0].citations[0].visual_anchor is not None
    assert result.model_call.prompt_revision == VISUAL_PROMPT_REVISION
    assert result.model_call.selected_visual_citation_ids
    assert "do not establish" in wire.request["messages"][0]["content"]
