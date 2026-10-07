"""Passive variant selection and real OCR/corpus handoff, not chart accuracy."""

import asyncio
import hashlib
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.image_candidates import admitted, image_candidates
from ghimera.image_ocr import TesseractOcr
from ghimera.loop import GoalLoop
from ghimera.models import Goal, Harvest, Page, Scope
from ghimera.responsive_config import ResponsiveImageConfig
from ghimera.responsive_images import parse_srcset, safe_image_url
from ghimera.visual_config import VisualConfig
from ghimera.visual_stage import VisualStage
from tests.test_embedding_scoring import endpoint
from tests.test_evidence_corpus import config as corpus_config
from tests.test_evidence_corpus import corpus
from tests.test_visuals import page, raster, recipe, run_config

__all__ = ["endpoint"]


def responsive(**changes):
    raw = tomllib.loads(Path("examples/responsive-images.toml").read_text())
    raw.update(changes)
    return ResponsiveImageConfig.model_validate(raw)


def visual(tmp_path, **changes):
    raw = recipe(tmp_path).model_dump()
    raw["responsive"] = responsive(**changes)
    return VisualConfig.model_validate(raw)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/small.png, /large.png 2x", [("/small.png", None, 1.0), ("/large.png", None, 2.0)]),
        ("/part,a.png 1.5x", [("/part,a.png", None, 1.5)]),
        ("/a.png 900w 400h", [("/a.png", 900, None)]),
        ("/bad.png 1x 2x, /valid.png 2x", [("/valid.png", None, 2.0)]),
        ("/bad.png 400h, /valid.png 800w", [("/valid.png", 800, None)]),
        ("/bad.png custom(1, 2), /valid.png 1e0x", [("/valid.png", None, 1.0)]),
        ("/bad.png -1x, /valid.png .5x", [("/valid.png", None, 0.5)]),
        ("/bad.png 1e999x, /valid.png 1x", [("/valid.png", None, 1.0)]),
    ],
)
def test_srcset_tokenization_preserves_url_commas_and_rejects_invalid_descriptors(value, expected):
    actual = parse_srcset(value, responsive())
    assert [(row.url, row.width, row.density) for row in actual] == expected


def test_explicit_width_density_and_lazy_attribute_precedence_preserve_native_metadata(tmp_path):
    cfg = visual(tmp_path)
    parent = page(
        '<figure><img src="/fallback.png" srcset="/other.png 3000w" '
        'data-srcset="/small.png 800w, /chart,detail.png 1200w, /huge.png 8000w" '
        'sizes="(max-width: 50em) 100vw, 50vw">'
        "<figcaption>組織 chart 港口</figcaption></figure>"
        '<img alt="port diagram" srcset="/low.png .5x, /high.png 2x, /huge.png 5x">'
    )
    found = image_candidates(parent, cfg)
    assert [item.url for item in found] == [
        "https://example.org/chart,detail.png",
        "https://example.org/high.png",
    ]
    first, second = found
    assert admitted(first, cfg) and admitted(second, cfg)
    assert "組織 chart 港口" in first.caption
    assert first.responsive.attribute == "data-srcset"
    assert first.responsive.width == 1200 and second.responsive.density == 2.0
    assert first.responsive.markup_sha256 == hashlib.sha256(parent.body).hexdigest()
    assert (
        first.responsive.attribute_sha256
        == hashlib.sha256(b"/small.png 800w, /chart,detail.png 1200w, /huge.png 8000w").hexdigest()
    )
    assert first.responsive.sizes == "(max-width: 50em) 100vw, 50vw"


def test_picture_alternatives_preserve_media_instead_of_inventing_a_viewport(tmp_path):
    parent = page(
        '<source srcset="/outside.png 1x"><picture>'
        '<source type="image/avif" srcset="/unsupported.avif 2x">'
        '<source media="(min-width: 800px)" type="image/png" '
        'srcset="/wide.png 800w, /wide-large.png 1600w">'
        '<source media="(max-width: 799px)" srcset="/narrow.png 2x">'
        '<img src="/fallback.png" alt="organization diagram"></picture>'
    )
    found = image_candidates(parent, visual(tmp_path))
    assert [item.url.rsplit("/", 1)[-1] for item in found] == [
        "wide-large.png",
        "narrow.png",
        "fallback.png",
    ]
    assert [item.responsive.picture_source_index for item in found] == [1, 2, None]
    assert found[0].responsive.media == "(min-width: 800px)"
    assert found[1].responsive.media == "(max-width: 799px)"
    assert "picture_type_or_media" in found[0].responsive.omissions
    unconditional = image_candidates(parent, visual(tmp_path, picture_media="unconditional_only"))
    assert len(unconditional) == 1 and unconditional[0].url.endswith("/fallback.png")


@pytest.mark.parametrize(
    ("attributes", "updates", "reason"),
    [
        ('srcset="/a.png 100w, /b.png 200w"', {"max_variants_per_attribute": 1}, "variant_count"),
        ('srcset="/a.png 100w"', {"max_attribute_chars": 5}, "attribute_chars"),
        ('srcset="/a.png 100w, /b.png 2x"', {}, "mixed_descriptor_space"),
        ('srcset="data:image/png;base64,YQ== 1x, /a.png 9x"', {}, "no_bounded_variant"),
    ],
)
def test_set_refusals_keep_an_explicit_fallback_and_omission(tmp_path, attributes, updates, reason):
    parent = page(f'<img src="/chart.png" alt="port chart" {attributes}>')
    found = image_candidates(parent, visual(tmp_path, **updates))
    if reason == "attribute_chars":
        # This operator's five-character cap excludes the fallback too.
        assert not found
    else:
        assert len(found) == 1 and found[0].url.endswith("/chart.png")
        assert reason in found[0].responsive.omissions


def test_picture_and_page_caps_do_not_turn_a_prefix_into_a_complete_source_set(tmp_path):
    parent = page(
        '<picture><source srcset="/a.png 2x"><source srcset="/b.png 2x">'
        '<img src="/chart.png" alt="port chart"></picture>'
    )
    limited = image_candidates(parent, visual(tmp_path, max_picture_sources=1))
    assert len(limited) == 1 and limited[0].url.endswith("/chart.png")
    assert limited[0].responsive.omissions == ("picture_source_count",)
    raw = visual(tmp_path).model_dump()
    raw.update(max_candidates_per_page=1, max_images_per_page=1)
    cfg = VisualConfig.model_validate(raw)
    assert len(image_candidates(parent, cfg)) == 1


def test_legacy_visual_recipe_and_candidate_wire_shapes_do_not_gain_fields(tmp_path):
    cfg = recipe(tmp_path)
    assert "responsive" not in cfg.model_dump()
    found = image_candidates(page('<img src="/chart.png" alt="port chart">'), cfg)
    assert len(found) == 1 and "responsive" not in found[0].model_dump()
    assert "responsive" not in found[0].model_dump_json()


@pytest.mark.parametrize(
    "observed",
    ["https://[broken/chart.png", "https://example.org:bad/chart.png", "/chart\nname.png"],
)
def test_invalid_urls_refuse_without_normalizing_control_characters(observed):
    assert safe_image_url("https://example.org/start", observed) is None


def test_pacific_recipe_routes_each_required_language_without_merging_chinese_scripts():
    cfg = VisualConfig.model_validate(
        tomllib.loads(Path("examples/visuals-pacific.toml").read_text())
    )
    expected = {
        "en": ("eng",),
        "zh-Hans": ("chi_sim", "eng"),
        "zh-Hant": ("chi_tra", "eng"),
        "ja": ("jpn", "jpn_vert", "eng"),
        "ko": ("kor", "eng"),
        "tl": ("fil", "eng"),
        "fil": ("fil", "eng"),
        "id": ("ind", "eng"),
        "ms": ("msa", "eng"),
        "vi": ("vie", "eng"),
        "th": ("tha", "eng"),
    }
    for language, packs in expected.items():
        assert cfg.language_routes[language] == packs
        assert set(packs) <= set(cfg.languages)


def test_declared_source_selection_runs_real_ocr_and_indexes_only_retained_non_logo_evidence(
    tmp_path, endpoint
):
    cfg = visual(tmp_path)
    policy = run_config(cfg)

    class ResponsiveRoute(FakeRoute):
        def __init__(self):
            super().__init__()
            self.image = raster()

        async def attempt(self, request):
            self.requests.append(request)
            image = request.url.endswith(".png")
            return Page(
                url=request.url,
                final_url=request.url,
                status=200,
                content_type="image/png" if image else "text/html",
                body=self.image
                if image
                else (
                    b'<article>ports</article><picture><source srcset="/logo-large.png 2x">'
                    b'<img src="/logo.png" class="logo" alt="port chart"></picture>'
                    b'<figure><picture><source type="image/png" srcset="/chart-large.png 2x">'
                    b'<img src="/chart-small.png" alt="port chart"></picture>'
                    b"<figcaption>Pacific port chart</figcaption></figure>"
                ),
            )

    async def operation():
        route, judge = ResponsiveRoute(), FakeJudge()
        loop = GoalLoop(
            config=policy,
            fetcher=FetchLadder((route,)),
            extractor=FakeExtractor(),
            scorer=KeywordScorer(),
            judge=judge,
            visual_stage=VisualStage(cfg, ocr=TesseractOcr(cfg), judge=judge),
        )
        material = await loop.run(
            Goal(text="ports", seeds=("https://example.org/start",)),
            Scope(allowed_hosts=("example.org",), max_depth=0, content_types=("text/html",)),
        )
        assert len(material.documents[0].images) == 1
        image = material.documents[0].images[0]
        assert image.candidate.url.endswith("/chart-large.png")
        assert "PACIFIC" in image.ocr.text and "PORTS" in image.ocr.text
        assert image.candidate.responsive.picture_source_index == 0
        assert all("logo" not in request.url for request in route.requests)
        assert Harvest.model_validate_json(material.model_dump_json()) == material
        poisoned = material.model_dump()
        poisoned["documents"][0]["images"][0]["candidate"]["responsive"]["density"] = 3.0
        with pytest.raises(ValidationError, match="replay from the retained markup"):
            Harvest.model_validate(poisoned)
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        await store.append(material)
        found = await store.search("unrelated", top_k=10)
        image_hits = [hit for hit in found.hits if hit.passage.kind == "image_ocr"]
        assert len(image_hits) == 1
        original = store.document(image_hits[0].passage.document_id)
        assert len(original.images) == 1 and original.images[0].raw == route.image
        assert all("logo" not in retained.candidate.url for retained in original.images)
        store.close()

    asyncio.run(operation())
