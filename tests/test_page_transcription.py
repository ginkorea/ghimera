"""Actual offline page pixels and model-protocol contracts, not model accuracy."""

import asyncio
import base64
import hashlib
import json
import sys
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.ledger import Ledger
from ghimera.model_http import ModelHttpResponse
from ghimera.page_renderer import PdfPageRenderer
from ghimera.page_transcriber import LocalPageTranscriber
from ghimera.page_transcription_config import PageRenderConfig, PageTranscriptionConfig
from ghimera.page_transcription_types import ReviewedPageTranscription
from ghimera.refusals import GhimeraRefused, RefusalCode
from tests.test_document_extraction import native_pdf
from tests.test_served_models import service


def render_config(tmp_path, **updates):
    values = dict(
        schema="ghimera.page-render/1",
        worker_python=sys.executable,
        work_directory=tmp_path / "render",
        renderer_package_version="5.14.0",
        image_package_version="12.3.0",
        scale=1.0,
        max_input_bytes=2_000_000,
        max_pages=10,
        max_pixels_per_page=2_000_000,
        max_image_bytes=500_000,
        max_total_image_bytes=1_000_000,
        max_workers=2,
        timeout_seconds=20.0,
        cleanup_timeout_seconds=5.0,
        max_output_bytes=2_000_000,
        max_diagnostic_bytes=8192,
    )
    values.update(updates)
    return PageRenderConfig.model_validate(values)


def policy(tmp_path):
    return PageTranscriptionConfig(
        schema="ghimera.page-transcription/1",
        renderer=render_config(tmp_path),
        transcriber=service(9991, model_id="transcription-fixture"),
        reviewer=service(9992, model_id="review-fixture"),
        languages=("zh-Hans", "zh-Hant", "en"),
        max_lines=100,
        max_text_chars=20_000,
        max_uncertain_regions=10,
        max_concurrent_pages=2,
    )


class ModelPort:
    def __init__(self, config, *, review=False, mutation=None, waiting=None):
        self.config, self.review, self.mutation, self.waiting = config, review, mutation, waiting
        self.requests = []

    async def post(self, body):
        self.requests.append(json.loads(body))
        if self.waiting is not None:
            await self.waiting.wait()
        request = self.requests[-1]
        context = json.loads(request["messages"][0]["content"][0]["text"].split("\n", 1)[1])
        if self.review:
            content = dict(
                image_sha256=context["image_sha256"],
                proposal_sha256=context["proposal_sha256"],
                accepted_line_indices=[0],
                uncertain_line_indices=[],
                omitted_regions=[],
            )
        else:
            content = dict(
                image_sha256=context["image_sha256"], lines=["臺灣港務公司"], uncertain_regions=[]
            )
        finish = "stop"
        if self.mutation == "wrong_hash":
            content["image_sha256"] = "0" * 64
        elif self.mutation == "missing_review":
            content["accepted_line_indices"] = []
        elif self.mutation == "uncertain_review":
            content["accepted_line_indices"], content["uncertain_line_indices"] = [], [0]
        elif self.mutation == "truncated":
            finish = "length"
        elif self.mutation == "duplicate_review":
            content["accepted_line_indices"] = [0, 0]
        return ModelHttpResponse(
            200,
            json.dumps(
                {
                    "model": self.config.served_model,
                    "choices": [
                        {
                            "message": {"content": json.dumps(content)},
                            "finish_reason": finish,
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
                }
            ).encode(),
            "application/json",
        )


def run_context():
    return RunBudget(
        GhimeraConfig.from_toml(Path("examples/chimera.toml")), time.monotonic
    ), Ledger()


def test_actual_render_worker_preserves_original_page_identity_and_png(tmp_path):
    cfg = render_config(tmp_path)
    source = native_pdf()
    result = asyncio.run(PdfPageRenderer(cfg).render(source))
    assert result.source_sha256 == hashlib.sha256(source).hexdigest()
    assert result.policy_sha256 == cfg.content_digest()
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.png.startswith(b"\x89PNG") and page.width * page.height <= cfg.max_pixels_per_page
    assert not list(cfg.work_directory.iterdir())
    assert "pypdfium2" not in sys.modules
    changed = page.model_dump()
    changed["png"] += b"changed"
    with pytest.raises(ValidationError, match="exact retained pixels"):
        type(page).model_validate(changed)


@pytest.mark.parametrize(
    "updates",
    [
        {"max_pixels_per_page": 100},
        {"renderer_package_version": "missing"},
        {"max_image_bytes": 10},
        {"max_output_bytes": 10},
    ],
)
def test_actual_worker_refuses_limits_and_wrong_native_identity(tmp_path, updates):
    with pytest.raises(GhimeraRefused):
        asyncio.run(PdfPageRenderer(render_config(tmp_path, **updates)).render(native_pdf()))


def test_generated_native_script_text_has_separate_review_and_no_ocr_confidence(tmp_path):
    cfg = policy(tmp_path)
    page = asyncio.run(PdfPageRenderer(cfg.renderer).render(native_pdf())).pages[0]
    first, second = ModelPort(cfg.transcriber), ModelPort(cfg.reviewer, review=True)
    budget, ledger = run_context()
    result = asyncio.run(
        LocalPageTranscriber(cfg, transcription_http=first, review_http=second).transcribe(
            page,
            language_hint="zh-Hant",
            source_url="https://example.org/x.pdf",
            budget=budget,
            ledger=ledger,
        )
    )
    assert result.accepted and result.text == "臺灣港務公司"
    assert budget.judge_calls == 2
    assert [row.event for row in ledger.snapshot()] == [
        "transcription_model",
        "transcription",
        "transcription_model",
        "transcription",
    ]
    assert all(call.usage.total_tokens == 10 for call in result.calls)
    assert ReviewedPageTranscription.model_validate_json(result.model_dump_json()) == result
    for port in (first, second):
        image = port.requests[0]["messages"][0]["content"][1]["image_url"]["url"]
        assert base64.b64decode(image.split(",", 1)[1]) == page.png
        assert port.requests[0]["max_tokens"] == port.config.max_output_tokens
    assert "confidence" not in result.proposal.model_dump()


@pytest.mark.parametrize(
    "mutation,role",
    [
        ("wrong_hash", "transcription"),
        ("wrong_hash", "review"),
        ("missing_review", "review"),
        ("duplicate_review", "review"),
        ("truncated", "transcription"),
        ("truncated", "review"),
    ],
)
def test_wrong_source_truncation_and_incomplete_review_never_become_evidence(
    tmp_path, mutation, role
):
    cfg = policy(tmp_path)
    page = asyncio.run(PdfPageRenderer(cfg.renderer).render(native_pdf())).pages[0]
    first = ModelPort(cfg.transcriber, mutation=mutation if role == "transcription" else None)
    second = ModelPort(cfg.reviewer, review=True, mutation=mutation if role == "review" else None)
    budget, ledger = run_context()
    with pytest.raises(GhimeraRefused):
        asyncio.run(
            LocalPageTranscriber(cfg, transcription_http=first, review_http=second).transcribe(
                page,
                language_hint="zh-Hans",
                source_url="https://example.org/x.pdf",
                budget=budget,
                ledger=ledger,
            )
        )
    calls = [row.transcription_call for row in ledger.snapshot() if row.event == "transcription"]
    assert len(calls) == budget.judge_calls
    assert all(call.response_sha256 for call in calls)
    if mutation == "truncated":
        assert calls[-1].finish_reason == "length" and calls[-1].outcome == "refused"
        assert calls[-1].usage.total_tokens == 10


def test_review_uncertainty_retains_record_but_exposes_no_accepted_text(tmp_path):
    cfg = policy(tmp_path)
    page = asyncio.run(PdfPageRenderer(cfg.renderer).render(native_pdf())).pages[0]
    budget, ledger = run_context()
    result = asyncio.run(
        LocalPageTranscriber(
            cfg,
            transcription_http=ModelPort(cfg.transcriber),
            review_http=ModelPort(cfg.reviewer, review=True, mutation="uncertain_review"),
        ).transcribe(
            page,
            language_hint="zh-Hant",
            source_url="https://example.org/x.pdf",
            budget=budget,
            ledger=ledger,
        )
    )
    assert not result.accepted and result.text is None
    assert result.proposal.text == "臺灣港務公司"


def test_unknown_script_and_wrong_render_policy_refuse_before_model_spend(tmp_path):
    cfg = policy(tmp_path)
    page = asyncio.run(PdfPageRenderer(cfg.renderer).render(native_pdf())).pages[0]
    first, second = ModelPort(cfg.transcriber), ModelPort(cfg.reviewer, review=True)
    budget, ledger = run_context()
    with pytest.raises(GhimeraRefused) as refused:
        asyncio.run(
            LocalPageTranscriber(cfg, transcription_http=first, review_http=second).transcribe(
                page,
                language_hint="unknown",
                source_url="https://example.org/x.pdf",
                budget=budget,
                ledger=ledger,
            )
        )
    assert refused.value.code == RefusalCode.ADAPTER_CONTRACT
    assert not first.requests and not second.requests and budget.judge_calls == 0


def test_cancelled_call_keeps_actual_attempt_and_releases_page_slot(tmp_path):
    cfg = policy(tmp_path)
    values = cfg.model_dump(by_alias=True)
    values["max_concurrent_pages"] = 1
    cfg = PageTranscriptionConfig.model_validate(values)
    page = asyncio.run(PdfPageRenderer(cfg.renderer).render(native_pdf())).pages[0]

    async def scenario():
        waiting = asyncio.Event()
        first = ModelPort(cfg.transcriber, waiting=waiting)
        reader = LocalPageTranscriber(
            cfg, transcription_http=first, review_http=ModelPort(cfg.reviewer, review=True)
        )
        budget, ledger = run_context()
        task = asyncio.create_task(
            reader.transcribe(
                page,
                language_hint="zh-Hant",
                source_url="https://example.org/x.pdf",
                budget=budget,
                ledger=ledger,
            )
        )
        async with asyncio.timeout(1):
            while not first.requests:
                await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        call = ledger.snapshot()[-1].transcription_call
        assert call.outcome == "cancelled" and call.response_sha256 is None
        assert budget.judge_calls == 1
        waiting.set()
        result = await reader.transcribe(
            page,
            language_hint="zh-Hant",
            source_url="https://example.org/x.pdf",
            budget=budget,
            ledger=ledger,
        )
        assert result.accepted and budget.judge_calls == 3
        assert sum(row.event == "transcription_model" for row in ledger.snapshot()) == 3

    asyncio.run(scenario())


def test_wrong_injected_endpoint_and_same_review_model_are_not_silent_fallback(tmp_path):
    cfg = policy(tmp_path)
    with pytest.raises(GhimeraRefused):
        LocalPageTranscriber(
            cfg,
            transcription_http=ModelPort(cfg.reviewer),
            review_http=ModelPort(cfg.reviewer, review=True),
        )
    values = cfg.model_dump(by_alias=True)
    values["reviewer"] = values["transcriber"]
    with pytest.raises(ValidationError, match="separately identified"):
        PageTranscriptionConfig.model_validate(values)


def test_model_request_limit_refuses_without_spending_the_run(tmp_path):
    cfg = policy(tmp_path)
    values = cfg.model_dump(by_alias=True)
    values["transcriber"]["max_request_bytes"] = 10
    cfg = PageTranscriptionConfig.model_validate(values)
    page = asyncio.run(PdfPageRenderer(cfg.renderer).render(native_pdf())).pages[0]
    first = ModelPort(cfg.transcriber)
    budget, ledger = run_context()
    with pytest.raises(GhimeraRefused) as refused:
        asyncio.run(
            LocalPageTranscriber(
                cfg, transcription_http=first, review_http=ModelPort(cfg.reviewer, review=True)
            ).transcribe(
                page,
                language_hint="en",
                source_url="https://example.org/x.pdf",
                budget=budget,
                ledger=ledger,
            )
        )
    assert refused.value.code == RefusalCode.BUDGET_EXHAUSTED
    assert budget.judge_calls == 0 and not first.requests and not ledger.snapshot()
