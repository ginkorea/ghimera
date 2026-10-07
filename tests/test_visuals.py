"""Visual contracts, real raster/OCR execution, and scoped collector wiring."""

import asyncio
import hashlib
import io
import json
import os
import sys
import time
import tomllib
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.image_candidates import admitted, image_candidates
from ghimera.image_ocr import TesseractOcr
from ghimera.image_worker import DecodeRequest, decode
from ghimera.ledger import Ledger
from ghimera.loop import GoalLoop
from ghimera.model_http import ModelHttpResponse
from ghimera.models import Goal, Page, Scope, Verdict
from ghimera.visual_config import VisualConfig
from ghimera.visual_model import LocalVisionReader
from ghimera.visual_stage import VisualStage
from tests.test_served_models import service


def recipe(tmp_path):
    raw = tomllib.loads(Path("examples/visuals-pacific.toml").read_text())
    raw.update(
        tesseract=Path(os.environ["GHIMERA_TEST_TESSERACT"]),
        tessdata_directory=Path(os.environ["GHIMERA_TEST_TESSDATA"]),
        worker_python=Path(sys.executable),
        work_directory=tmp_path / "ocr",
        languages=("eng",),
        fallback_languages=("eng",),
        language_routes={"en": ("eng",)},
        min_word_confidence=0,
    )
    return VisualConfig.model_validate(raw)


def raster(text="PACIFIC PORTS 2026"):
    image = Image.new("RGB", (1000, 250), "white")
    font = ImageFont.truetype(os.environ["GHIMERA_TEST_FONT"], 50)
    ImageDraw.Draw(image).text((30, 60), text, font=font, fill="black")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def page(html):
    return Page(
        url="https://example.org/start",
        final_url="https://example.org/start",
        status=200,
        content_type="text/html",
        body=html.encode(),
    )


def test_candidates_reject_logos_before_download_and_use_native_caption(tmp_path):
    config = recipe(tmp_path)
    parent = page(
        '<header><img src="/chart-nav.png"></header><img src="/logo.png" alt="chart">'
        '<figure><img data-src="/evidence.png" width="900" height="600">'
        "<figcaption>Pacific port chart</figcaption></figure>"
        '<img src="/chart-pixel.png" width="1" height="1">'
    )
    candidates = image_candidates(parent, config)
    kept = [item for item in candidates if admitted(item, config)]
    assert [item.url for item in kept] == ["https://example.org/evidence.png"]
    assert "Pacific port chart" in kept[0].caption
    assert kept[0].parent_sha256 == hashlib.sha256(parent.body).hexdigest()


def test_real_ocr_retains_original_regions_and_pack_identity(tmp_path):
    config = recipe(tmp_path)
    raw = raster()
    result = asyncio.run(TesseractOcr(config).read(raw, language_hint="en"))
    assert "PACIFIC" in result.text and "PORTS" in result.text and "2026" in result.text
    assert result.width == 1000 and result.height == 250
    assert result.image_sha256 == hashlib.sha256(raw).hexdigest()
    assert set(result.language_pack_sha256) == {"eng"}
    assert all(0 <= item.region.left < item.region.right <= 1 for item in result.spans)
    assert not list(config.work_directory.iterdir())


def test_missing_pacific_language_pack_fails_admission(tmp_path):
    config = recipe(tmp_path)
    raw = config.model_dump(by_alias=True)
    raw.update(languages=("eng", "missing_pacific_language"))
    with pytest.raises(ValueError, match="language is unavailable"):
        TesseractOcr(VisualConfig.model_validate(raw))


def test_pacific_profile_distinguishes_scripts_and_declares_japanese_dependencies():
    config = VisualConfig.model_validate(
        tomllib.loads(Path("examples/visuals-pacific.toml").read_text())
    )
    assert config.language_routes["zh-CN"] == config.language_routes["zh-Hans"]
    assert config.language_routes["zh-TW"] == config.language_routes["zh-Hant"]
    assert config.language_routes["zh-Hans"] != config.language_routes["zh-Hant"]
    assert config.language_routes["tl"] == config.language_routes["fil"] == ("fil", "eng")
    assert config.language_routes["ja"] == ("jpn", "jpn_vert", "eng")
    assert config.language_routes["ko"] == ("kor", "eng")


@pytest.mark.parametrize("kind", ["tiny", "oversized", "invalid", "animated"])
def test_decode_refuses_small_bomb_malformed_and_animated_images(tmp_path, kind):
    config = recipe(tmp_path)
    if kind == "invalid":
        raw = b"not an image"
    else:
        image = Image.new("RGB", (10, 10) if kind == "tiny" else (1000, 250), "white")
        output = io.BytesIO()
        if kind == "animated":
            image.save(
                output, "PNG", save_all=True, append_images=[Image.new("RGB", image.size, "black")]
            )
        else:
            image.save(output, "PNG")
        raw = output.getvalue()
        if kind == "oversized":
            values = config.model_dump(by_alias=True)
            values["max_pixels"] = 50000
            config = VisualConfig.model_validate(values)
    with pytest.raises((ValueError, OSError)):
        decode(DecodeRequest(config=config, raw=raw))


class VisualRoute(FakeRoute):
    def __init__(self):
        super().__init__()
        self.raw = raster()

    async def attempt(self, request):
        self.requests.append(request)
        body = (
            self.raw
            if request.url.endswith(".png")
            else (
                b'<article>ports</article><img src="/logo.png" alt="chart">'
                b'<img src="/chart.png" alt="port chart">'
            )
        )
        return Page(
            url=request.url,
            final_url=request.url,
            status=200,
            content_type="image/png" if request.url.endswith(".png") else "text/html",
            body=body,
        )


def run_config(visual):
    raw = GhimeraConfig.from_toml(Path("examples/chimera.toml")).model_dump(by_alias=True)
    raw.update(
        visuals=visual,
        page_budget=8,
        judge_budget=8,
        byte_budget=1000000,
        http={
            "schema": "chimera.http/1",
            "max_response_bytes": 100000,
            "max_header_bytes": 16384,
            "max_redirects": 1,
            "retry_backoff_seconds": 0.01,
            "retry_jitter_seconds": 0,
            "robots_cache_seconds": 60,
            "conditional_cache_entries": 4,
            "network": {"schema": "chimera.network/1", "mode": "public"},
            "robots": {"schema": "chimera.robots/1", "mode": "honor", "product_token": "Chimera"},
        },
    )
    return GhimeraConfig.model_validate(raw)


def test_accepted_visual_is_collected_and_archive_roundtrips_without_logo(tmp_path):
    visual = recipe(tmp_path)
    config = run_config(visual)
    route = VisualRoute()
    judge = FakeJudge()
    stage = VisualStage(visual, ocr=TesseractOcr(visual), judge=judge)
    loop = GoalLoop(
        config=config,
        fetcher=FetchLadder((route,)),
        extractor=FakeExtractor(),
        scorer=KeywordScorer(),
        judge=judge,
        visual_stage=stage,
    )
    result = asyncio.run(
        loop.run(
            Goal(text="ports", seeds=("https://example.org/start",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
    )
    assert len(result.documents) == 1 and len(result.documents[0].images) == 1
    assert [request.url for request in route.requests] == [
        "https://example.org/start",
        "https://example.org/chart.png",
    ]
    image = result.documents[0].images[0]
    assert image.raw == route.raw and image.interpretation is None
    assert result.receipt.judge_calls == 2
    assert result.model_validate_json(result.model_dump_json()) == result
    result.documents[0].validate_policy(config)
    with pytest.raises(ValidationError):
        image.model_validate(dict(image.model_dump(), raw=b"changed"))


def test_rejected_visual_leaves_no_image_or_vector_artifact(tmp_path):
    class Reject(FakeJudge):
        async def document(self, goal, document, *, second_look):
            return Verdict(
                decision="reject",
                kind="other",
                publisher="unknown",
                language="en",
                reason="irrelevant",
            )

    visual = recipe(tmp_path)
    config = run_config(visual)
    fetcher = FetchLadder((VisualRoute(),))
    stage = VisualStage(visual, ocr=TesseractOcr(visual), judge=Reject())
    ledger = Ledger()
    retained = asyncio.run(
        stage.collect(
            goal=Goal(text="other"),
            parent=page('<img src="/chart.png" alt="chart">'),
            scope=Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
            fetcher=fetcher,
            budget=RunBudget(config, time.monotonic),
            ledger=ledger,
            language_hint="en",
        )
    )
    assert retained == () and not fetcher._cache
    assert not list(visual.work_directory.iterdir())
    assert "visual_rejected" in ledger.snapshot()[-1].reason


class VisionWire:
    def __init__(self, config, *, unsupported=False, changed=False, truncated=False):
        self.config = config
        self.unsupported, self.changed, self.truncated = unsupported, changed, truncated

    async def post(self, body):
        request = json.loads(body)
        messages = request["messages"][0]["content"]
        assert messages[1]["image_url"]["url"].startswith("data:image/png;base64,")
        inputs = json.loads(messages[0]["text"].rsplit("\n", 1)[1])
        digest = inputs["ocr"]["image_sha256"]
        if "proposal" in inputs:
            result = {
                "image_sha256": digest,
                "proposal_sha256": inputs["proposal_sha256"],
                "relevant": True,
                "supported": not self.unsupported,
                "reason": "fixture only",
            }
        else:
            result = {
                "image_sha256": "0" * 64 if self.changed else digest,
                "relevant": True,
                "claims": [
                    {
                        "text": "fixture observation",
                        "regions": [{"left": 0.0, "top": 0.0, "right": 1.0, "bottom": 1.0}],
                    }
                ],
            }
        return ModelHttpResponse(
            200,
            json.dumps(
                {
                    "model": self.config.served_model,
                    "choices": [
                        {
                            "finish_reason": "length" if self.truncated else "stop",
                            "message": {"content": json.dumps(result)},
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                }
            ).encode(),
            "application/json",
        )


@pytest.mark.parametrize("mode", ["supported", "unsupported", "changed", "truncated"])
def test_visual_model_protocol_reviews_actual_image_and_refuses_unbound_claims(tmp_path, mode):
    from ghimera.refusals import GhimeraRefused

    values = recipe(tmp_path).model_dump(by_alias=True)
    values.update(
        vision=service(9999, max_request_bytes=1000000, max_input_chars=100000),
        reviewer=service(9999, max_request_bytes=1000000, max_input_chars=100000),
    )
    visual = VisualConfig.model_validate(values)
    config = run_config(visual)
    raw = raster()
    ocr = asyncio.run(TesseractOcr(visual).read(raw, language_hint="en"))
    reader = LocalVisionReader(
        visual,
        vision_http=VisionWire(
            visual.vision, changed=mode == "changed", truncated=mode == "truncated"
        ),
        review_http=VisionWire(visual.reviewer, unsupported=mode == "unsupported"),
    )
    budget, ledger = RunBudget(config, time.monotonic), Ledger()
    call = reader.interpret(
        intent="ports",
        raw=raw,
        ocr=ocr,
        budget=budget,
        ledger=ledger,
        url="https://example.org/chart.png",
    )
    if mode in {"changed", "truncated"}:
        with pytest.raises(GhimeraRefused):
            asyncio.run(call)
    else:
        result = asyncio.run(call)
        assert (result is not None) == (mode == "supported")
        assert budget.judge_calls == 2
    assert budget.judge_calls == sum(row.event == "visual_model" for row in ledger.snapshot())
