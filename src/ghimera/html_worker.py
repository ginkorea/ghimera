"""Passive vendor parsing in an owned subprocess; never fetch another URL.

Vendor imports are confined here because Crawl4AI imports its broader optional
API stack eagerly. No crawler, browser, LLM filter or model client is constructed.
The audit guard is defense in depth, not an OS-level anonymity/sandbox claim.
"""

import codecs
import contextlib
import hashlib
import html
import sys
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urldefrag, urljoin, urlsplit

from ghimera.extraction import ExtractionRequest, ExtractionResponse
from ghimera.extraction_config import ExtractionConfig, LocatorProfile
from ghimera.extraction_types import ExtractionEvidence, LocatorEvent
from ghimera.models import Extracted, LinkCandidate, Page
from ghimera.refusals import GhimeraRefused, RefusalCode


def no_network(event: str, arguments: tuple[object, ...]) -> None:
    if event in {
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.sendto",
        "subprocess.Popen",
        "os.system",
    }:
        raise PermissionError("passive extraction cannot initiate network or subprocess I/O")


class CleanHtml(HTMLParser):
    """Discard configured boilerplate without executing or normalizing source prose."""

    def __init__(self, excluded: tuple[str, ...]) -> None:
        super().__init__(convert_charrefs=True)
        self._excluded = frozenset(excluded)
        self._blocked: list[str] = []
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._excluded:
            self._blocked.append(tag)
        elif not self._blocked:
            attributes = "".join(
                f' {name}="{html.escape(value or "", quote=True)}"' for name, value in attrs
            )
            self._chunks.append(f"<{tag}{attributes}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._blocked:
            index = len(self._blocked) - 1 - self._blocked[::-1].index(tag)
            del self._blocked[index:]
        elif not self._blocked:
            self._chunks.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in self._excluded:
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not self._blocked:
            self._chunks.append(html.escape(data, quote=False))

    def cleaned(self, source: str) -> str:
        self.feed(source)
        self.close()
        return "".join(self._chunks)


def encoding(page: Page, config: ExtractionConfig) -> str:
    for part in page.content_type.split(";")[1:]:
        if part.strip().lower().startswith("charset="):
            name = part.strip().split("=", 1)[1].strip().strip("\"'")
            return codecs.lookup(name).name
    return codecs.lookup(config.default_encoding).name


def public_link(base: str, value: str) -> str | None:
    try:
        joined = urldefrag(urljoin(base, value)).url
        parts = urlsplit(joined)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or any(ord(c) < 33 for c in joined)
        ):
            return None
        _ = parts.port
        return joined
    except ValueError:
        return None


def parse(request: ExtractionRequest, events: list[LocatorEvent]) -> Extracted:
    # These actual pinned vendor types are imported only after the worker guard.
    from crawl4ai.content_filter_strategy_lxml import PruningContentFilterLXML
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    from lingua import IsoCode639_1, LanguageDetectorBuilder
    from scrapling.parser import Selector

    config, page = request.config, request.page
    actual_encoding = "utf-8" if page.rendered is not None else encoding(page, config)
    source = (page.rendered.html if page.rendered is not None else page.body).decode(
        actual_encoding, errors="replace"
    )
    profile = next(
        (value for value in config.profiles if value.host == urlsplit(page.final_url).hostname),
        None,
    )
    active_profile = None if request.generic_only else profile
    profile_key = profile.model_dump_json() if profile else "generic"
    locator_key = f"{urlsplit(page.final_url).hostname}:{profile_key}"
    storage_path = config.locator_directory / (
        hashlib.sha256(locator_key.encode()).hexdigest() + ".sqlite"
    )
    if storage_path.is_symlink():
        raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
    root = Selector(
        source,
        url=page.final_url,
        encoding="utf-8",
        huge_tree=False,
        adaptive=active_profile is not None,
        storage_args={"storage_file": str(storage_path), "url": page.final_url},
    )

    def attribute(node: Selector, name: str) -> str | None:
        value: object = node.attrib.get(name)
        return value if isinstance(value, str) and value.strip() else None

    def located(
        field: Literal["body", "title", "byline", "date"],
        selector: str | None,
        selected_profile: LocatorProfile,
    ) -> Selector | None:
        if selector is None:
            return None
        identity = f"{selected_profile.profile_id}:{field}"
        direct = root.css(selector)
        if direct:
            result = root.css(selector, identifier=identity, auto_save=True)
            status: Literal["direct", "relocated", "missing"] = "direct"
        else:
            result = root.css(
                selector, identifier=identity, adaptive=True, percentage=config.locator_min_percent
            )
            status = "relocated" if result else "missing"
        events.append(LocatorEvent(field=field, status=status, selector=selector))
        return result[0] if result else None

    def generic(selectors: tuple[str, ...]) -> Selector | None:
        for selector in selectors:
            matches = root.css(selector)
            if matches:
                return matches[0]
        return None

    def metadata(selector: str, attr: str | None = None) -> str | None:
        node = generic((selector,))
        if node is None:
            return None
        value = attribute(node, attr) if attr else str(node.get_all_text(separator=" ", strip=True))
        return value.strip() if value and value.strip() else None

    def field_value(field: Literal["title", "byline", "date"], selector: str | None) -> str | None:
        if active_profile is None:
            return None
        node = located(field, selector, active_profile)
        if node is None:
            return None
        if field == "date" and attribute(node, "datetime"):
            return attribute(node, "datetime")
        value = str(node.get_all_text(separator=" ", strip=True)).strip()
        return value or None

    body = located("body", active_profile.body, active_profile) if active_profile else None
    selection: Literal["profile", "relocated", "generic"] = "profile"
    if body is None:
        body = generic(config.generic_body_selectors)
        selection = "generic"
    elif any(row.field == "body" and row.status == "relocated" for row in events):
        selection = "relocated"
    if body is None:
        raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
    title = (
        field_value("title", profile.title if profile else None)
        or metadata('meta[property="og:title"]', "content")
        or metadata("h1")
        or metadata("title")
    )
    byline = field_value("byline", profile.byline if profile else None) or metadata(
        'meta[name="author"]', "content"
    )
    date = (
        field_value("date", profile.date if profile else None)
        or metadata('meta[property="article:published_time"]', "content")
        or metadata("time[datetime]", "datetime")
    )
    canonical = metadata('link[rel="canonical"]', "href")
    canonical_url = public_link(page.final_url, canonical) if canonical else None
    declared_language = metadata("html[lang]", "lang")
    selected_html = CleanHtml(config.excluded_tags).cleaned(str(body.html_content))
    markdown = DefaultMarkdownGenerator(
        content_filter=PruningContentFilterLXML(
            threshold=config.pruning_threshold,
            threshold_type=config.pruning_threshold_type,
            min_word_threshold=config.min_word_threshold,
        ),
        options={"ignore_images": True, "body_width": 0, "escape_html": False},
    ).generate_markdown(selected_html, base_url=page.final_url, citations=False)
    text, raw_markdown = markdown.fit_markdown.strip(), markdown.raw_markdown
    if (
        not title
        or not text
        or text.startswith(("Error generating", "Error in markdown", "Error converting"))
        or len(text) > config.max_text_chars
    ):
        raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
    sample = str(body.get_all_text(separator=" ", strip=True, ignore_tags=config.excluded_tags))[
        : config.language_max_chars
    ]
    codes: list[IsoCode639_1] = []
    for code in config.languages:
        candidate: object = getattr(IsoCode639_1, code.upper(), None)
        if not isinstance(candidate, IsoCode639_1):
            raise GhimeraRefused(RefusalCode.ADAPTER_CONTRACT)
        codes.append(candidate)
    detector = LanguageDetectorBuilder.from_iso_codes_639_1(*codes).with_low_accuracy_mode().build()
    confidence = detector.compute_language_confidence_values(sample)
    if not confidence:
        raise GhimeraRefused(RefusalCode.EXTRACTION_FAILED)
    highest = confidence[0]
    margin = highest.value - confidence[1].value if len(confidence) > 1 else highest.value
    language = (
        highest.language.iso_code_639_1.name.lower()
        if len(sample) >= config.language_min_chars
        and highest.value >= config.language_min_confidence
        and margin >= config.language_min_margin
        else "und"
    )
    links: list[LinkCandidate] = []
    seen: set[str] = set()
    clean_root = Selector(selected_html, url=page.final_url, huge_tree=False)
    for node in clean_root.css("a[href]"):
        href = attribute(node, "href")
        target = public_link(page.final_url, href) if href else None
        if target and target not in seen:
            seen.add(target)
            if len(links) < config.max_links:
                links.append(
                    LinkCandidate(
                        url=target, anchor=str(node.get_all_text(separator=" ", strip=True))
                    )
                )
    evidence = ExtractionEvidence(
        schema="chimera.extraction-evidence/1",
        source_sha256=hashlib.sha256(page.body).hexdigest(),
        rendered_sha256=page.rendered.html_sha256 if page.rendered is not None else None,
        source_url=page.final_url,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        config_digest=config.content_digest(),
        parser_revision="scrapling@0.4.2+crawl4ai@0.9.4+lingua@2.1.1",
        encoding=actual_encoding,
        decode_replacements=source.count("\ufffd"),
        selection=selection,
        profile_id=profile.profile_id if profile else None,
        locators=tuple(events),
        missing_fields=tuple(row.field for row in events if row.status == "missing"),
        declared_language=declared_language,
        language_confidence=highest.value,
        language_margin=margin,
        language_sample_chars=len(sample),
        language_hint_disagrees=bool(
            declared_language
            and language != "und"
            and declared_language.split("-", 1)[0].lower() != language
        ),
        raw_markdown_sha256=hashlib.sha256(raw_markdown.encode()).hexdigest(),
        omitted_links=len(seen) - len(links),
    )
    return Extracted(
        title=title,
        text=text,
        language=language,
        links=tuple(links),
        byline=byline,
        date=date,
        canonical_url=canonical_url,
        extraction=evidence,
    )


def _cmd_extract() -> int:
    sys.addaudithook(no_network)
    events: list[LocatorEvent] = []
    try:
        with contextlib.redirect_stdout(sys.stderr):
            request = ExtractionRequest.model_validate_json(sys.stdin.buffer.read())
            result = parse(request, events)
        wire = ExtractionResponse(result=result, locator_events=tuple(events))
    except GhimeraRefused as exc:
        wire = ExtractionResponse(refusal=exc.code, locator_events=tuple(events))
    except Exception:
        # Third-party parsing is an untrusted boundary. Never leak vendor errors
        # or return their "Error ..." prose as a document or retry using a fetcher.
        wire = ExtractionResponse(
            refusal=RefusalCode.EXTRACTION_FAILED, locator_events=tuple(events)
        )
    sys.stdout.write(wire.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(_cmd_extract())
