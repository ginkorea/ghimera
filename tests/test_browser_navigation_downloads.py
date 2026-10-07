"""Unknown scoped attachment URLs use the existing native, scored frontier."""

import asyncio
import hashlib
import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghimera.config import GhimeraConfig
from ghimera.document_media import DOCX_TYPE
from ghimera.documents import DocumentExtractionSuite, DocumentExtractor
from ghimera.doubles import FakeJudge, KeywordScorer
from ghimera.extraction import HtmlExtractor
from ghimera.fetch import FetchLadder
from ghimera.http import CurlRoute
from ghimera.human_browser import BoundPageHumanSession
from ghimera.human_browser_route import HumanBrowserRoute
from ghimera.human_browser_types import HumanBrowserConfig
from ghimera.loop import GoalLoop
from ghimera.models import Goal, Harvest, Scope
from ghimera.refusals import GhimeraRefused
from tests.test_browser_downloads import download_policy, download_site, scope_for_source
from tests.test_document_extraction import config as document_config
from tests.test_document_extraction import docx, native_pdf
from tests.test_html_extraction import ARTICLE
from tests.test_html_extraction import policy as extraction_policy
from tests.test_http_fetch import ResolverFixture, state
from tests.test_human_browser import policy, with_browser


def navigation_policy(selected, origin, formats=("application/pdf", DOCX_TYPE)):
    data = download_policy(selected, origin).model_dump()
    data["downloads"].pop("actions")
    data["downloads"]["navigation_content_types"] = formats
    return HumanBrowserConfig.model_validate(data)


def test_navigation_downloads_require_explicit_unique_admitted_formats():
    explicit = download_policy(policy(), "https://publisher.example")
    assert "navigation_content_types" not in explicit.model_dump()["downloads"]
    selected = navigation_policy(policy(), "https://publisher.example")
    assert not selected.downloads.actions
    assert selected.downloads.navigation_content_types == ("application/pdf", DOCX_TYPE)
    fragment = tomllib.loads(Path("examples/browser-navigation-downloads.toml").read_text())
    candidate = selected.model_dump()
    candidate["downloads"] = fragment["human_browser"]["downloads"]
    assert HumanBrowserConfig.model_validate(candidate).downloads.navigation_content_types == (
        "application/pdf",
        DOCX_TYPE,
    )
    for formats in ((), ("application/pdf", "application/pdf"), ("text/html",)):
        with pytest.raises(ValidationError):
            navigation_policy(policy(), "https://publisher.example", formats)


@pytest.mark.parametrize("mime", ["application/pdf", DOCX_TYPE])
def test_unknown_browser_attachment_is_admitted_from_actual_file_bytes(tmp_path, mime):
    raw = native_pdf() if mime == "application/pdf" else docx()
    with download_site(raw) as site:
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(selected, page, unrelated):
            selected = navigation_policy(selected, origin)
            session = BoundPageHumanSession(selected, page=page, assistant=None)
            capture = await session.capture(
                origin + "/research/report", scope=scope_for_source(site[0])
            )
            assert capture.body == raw
            assert capture.evidence.content_type == mime
            assert capture.evidence.initiator_url is None
            assert capture.evidence.collector_dom_bytes_read == 0
            capture.validate_policy(selected)
            for formats in (
                (),
                ((DOCX_TYPE,) if mime == "application/pdf" else ("application/pdf",)),
            ):
                changed = selected.model_dump()
                changed["downloads"]["navigation_content_types"] = formats
                if formats:
                    with pytest.raises(ValueError):
                        capture.validate_policy(HumanBrowserConfig.model_validate(changed))
                else:
                    with pytest.raises(ValidationError):
                        HumanBrowserConfig.model_validate(changed)
            assert not page.is_closed() and not unrelated.is_closed()
            assert site[1]["/research/report"] == 1

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))


def test_native_parent_links_feed_unknown_browser_download_without_a_url_list(tmp_path):
    raw = native_pdf()
    html = ARTICLE.replace("appendix.pdf", "report").replace("Supporting appendix", "Port report")
    with download_site(raw, landing_html=html.encode()) as site:
        _, _, previous, _, origin = state(site)

        async def scenario(selected, page, unrelated):
            selected = navigation_policy(selected, origin)
            graph = tomllib.loads(Path("examples/research-graph.toml").read_text())
            graph["sink_path"] = str(tmp_path / "graph")
            values = previous.config.model_dump()
            values.update(
                human_browser=selected.model_dump(),
                request_timeout_seconds=30.0,
                wall_seconds=120.0,
                extraction=extraction_policy(tmp_path).model_dump(),
                document_extraction=document_config(tmp_path).document_extraction.model_dump(),
                graph=graph,
            )
            cfg = GhimeraConfig.model_validate(values)
            ladder = FetchLadder(
                (
                    HumanBrowserRoute(
                        selected, BoundPageHumanSession(selected, page=page, assistant=None)
                    ),
                    CurlRoute(cfg, resolver=ResolverFixture()),
                )
            )
            loop = GoalLoop(
                config=cfg,
                fetcher=ladder,
                extractor=DocumentExtractionSuite(
                    html=HtmlExtractor(cfg), documents=DocumentExtractor(cfg)
                ),
                scorer=KeywordScorer(),
                judge=FakeJudge(),
            )
            scope = Scope.model_validate(dict(scope_for_source(site[0]).model_dump(), max_depth=1))
            harvest = await loop.run(
                Goal(text="port", seeds=(origin + "/research/landing",)),
                scope,
                run_id="discovered-attachment",
            )
            assert len(harvest.documents) == 2, [
                (row.event, row.refusal, row.url) for row in harvest.ledger
            ]
            parent = next(d for d in harvest.documents if d.extracted.extraction is not None)
            child = next(
                d for d in harvest.documents if d.human_browser.acquisition == "browser_download"
            )
            assert any(link.url == child.url for link in parent.extracted.links)
            assert child.raw == raw and child.sha256 == hashlib.sha256(raw).hexdigest()
            assert child.url == origin + "/research/report"
            assert child.human_browser.policy_digest == selected.content_digest()
            assert harvest.graph is not None
            parent_node = next(
                node
                for node in harvest.graph.nodes
                if node.role == "document" and node.content_sha256 == parent.sha256
            )
            child_node = next(
                node
                for node in harvest.graph.nodes
                if node.role == "document" and node.human_browser == child.human_browser
            )
            child_source = next(
                node
                for node in harvest.graph.nodes
                if node.role == "source" and node.identity == child.url
            )
            assert any(
                edge.rule == "discovered"
                and edge.source == child_source.id
                and edge.target == parent_node.id
                for edge in harvest.graph.edges
            )
            assert any(
                edge.rule == "retrieved"
                and edge.source == child_node.id
                and edge.target == child_source.id
                for edge in harvest.graph.edges
            )
            assert Harvest.model_validate_json(harvest.model_dump_json()) == harvest
            assert site[1]["/research/landing"] == site[1]["/research/report"] == 1

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))


def test_navigation_format_allowlist_is_not_guessed_from_the_url(tmp_path):
    raw = b"not a PDF or DOCX despite the server filename"
    with download_site(raw) as site:
        origin = f"http://fixture.example:{site[0]}"

        async def scenario(selected, page, unrelated):
            selected = navigation_policy(selected, origin)
            session = BoundPageHumanSession(selected, page=page, assistant=None)
            with pytest.raises(GhimeraRefused, match="content_type_unwanted") as caught:
                await session.capture(origin + "/research/report")
            assert caught.value.bytes_read == len(raw)
            assert not page.is_closed() and not unrelated.is_closed()

        asyncio.run(with_browser(tmp_path, origin, scenario, accept_downloads=True))
