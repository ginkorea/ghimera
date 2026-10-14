"""In-process first-pass HTML reader: lxml text, links and language in milliseconds.

No subprocess and no vendor content filter: the page is parsed once with lxml,
configured boilerplate tags are dropped, body text is read under the generic
body selectors, links keep their anchors and text is capped. A page whose text
is thinner than ``fast_reader.min_text_chars`` (JavaScript-only shells, link
farms, pages without a title) returns ``None`` and goes to the richer tier:
the crawl4ai content filter, locator profiles, recovery, rendering and
document parsers are unchanged and still own every page the fast pass declines.

This module never fetches, never executes page script and never imports
crawl4ai. ``raw_markdown_sha256`` on its evidence digests the unfiltered text,
because this tier produces no markdown.
"""

import codecs
import hashlib
import re
from functools import lru_cache
from importlib.metadata import version
from typing import TYPE_CHECKING, Any
from urllib.parse import urldefrag, urljoin, urlsplit

from ghimera.extraction_types import ExtractionEvidence
from ghimera.models import Extracted, LinkCandidate, Page

if TYPE_CHECKING:
    from ghimera.extraction_config import ExtractionConfig

_CONTROL = re.compile(r"[\x00-\x20]")
FAST_READER_SCHEMA = "ghimera.fast-reader/1"
# Never text, whatever the operator's excluded_tags say.
ALWAYS_DROPPED = frozenset({"script", "style", "noscript", "template"})
BLOCKS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "caption",
        "dd",
        "details",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hr",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)


def encoding(page: Page, config: "ExtractionConfig") -> str:
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
            or _CONTROL.search(joined) is not None
        ):
            return None
        _ = parts.port
        return joined
    except ValueError:
        return None


def fast_reader_revision() -> str:
    return (
        f"{FAST_READER_SCHEMA}:lxml@{version('lxml')}+lingua@{version('lingua-language-detector')}"
    )


@lru_cache(maxsize=8)
def _detector(languages: tuple[str, ...]) -> Any:
    from lingua import IsoCode639_1, LanguageDetectorBuilder

    codes = [getattr(IsoCode639_1, code.upper()) for code in languages]
    return LanguageDetectorBuilder.from_iso_codes_639_1(*codes).with_low_accuracy_mode().build()


@lru_cache(maxsize=64)
def _selector(selector: str) -> Any:
    from lxml.cssselect import CSSSelector

    return CSSSelector(selector, translator="html")


def _drop(node: Any) -> None:
    """Remove an element and its content, keeping the text that follows it."""
    parent = node.getparent()
    if parent is None:
        return
    if node.tail:
        previous = node.getprevious()
        if previous is not None:
            previous.tail = (previous.tail or "") + node.tail
        else:
            parent.text = (parent.text or "") + node.tail
    parent.remove(node)


def _text(element: Any) -> str:
    """Native text with block boundaries as line breaks; whitespace runs collapsed.

    Marks block boundaries in this reader's own parsed copy, then reads the text
    in one C-level pass.
    """
    for node in element.iter(*BLOCKS):
        node.text = "\n" + (node.text or "")
        if node is not element:
            node.tail = "\n" + (node.tail or "")
    lines = (" ".join(line.split()) for line in "".join(element.itertext()).split("\n"))
    return "\n".join(line for line in lines if line)


def read_fast(page: Page, config: "ExtractionConfig") -> Extracted | None:
    """Return the first-pass reading, or ``None`` when the richer tier must read it."""
    policy = config.fast_reader
    if policy is None:
        raise ValueError("the fast reader requires its configured policy")
    from lxml import etree

    actual_encoding = (
        "utf-8"
        if page.rendered is not None or page.human_browser is not None
        else encoding(page, config)
    )
    source = (page.rendered.html if page.rendered is not None else page.body).decode(
        actual_encoding, errors="replace"
    )
    # Plain etree elements: no per-node HTML class lookup on a large page.
    parser = etree.HTMLParser(remove_comments=True, remove_pis=True, huge_tree=False)
    try:
        root = etree.fromstring(source, parser=parser)
    except (etree.ParserError, etree.XMLSyntaxError, ValueError):
        return None
    if root is None:
        return None

    def select(element: Any, selector: str) -> list[Any]:
        return _selector(selector)(element)  # type: ignore[no-any-return]

    def first(selector: str, attribute: str | None = None) -> str | None:
        for node in select(root, selector):
            value = node.get(attribute) if attribute else "".join(node.itertext())
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())
        return None

    title = first('meta[property="og:title"]', "content") or first("h1") or first("title")
    byline = first('meta[name="author"]', "content")
    date = first('meta[property="article:published_time"]', "content") or first(
        "time[datetime]", "datetime"
    )
    canonical = first('link[rel="canonical"]', "href")
    declared_language = first("html[lang]", "lang")
    dropped = ALWAYS_DROPPED | set(config.excluded_tags)
    for node in list(root.iter(*dropped)):
        _drop(node)
    body = next(
        (
            matches[0]
            for selector in config.generic_body_selectors
            if (matches := select(root, selector))
        ),
        None,
    )
    if body is None or not title:
        return None
    full = _text(body)
    text = full[: config.max_text_chars]
    if len(text) < policy.min_text_chars:
        return None
    sample = text[: config.language_max_chars]
    confidence = _detector(config.languages).compute_language_confidence_values(sample)
    if not confidence:
        return None
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
    beyond: set[str] = set()
    for anchor in body.iter("a"):
        href = anchor.get("href")
        if not href or not href.strip():
            continue
        if len(links) >= config.max_links:
            # Past the cap, distinct raw hrefs are counted without resolving each one.
            beyond.add(href.strip())
            continue
        target = public_link(page.final_url, href)
        if target and target not in seen:
            seen.add(target)
            links.append(
                LinkCandidate(url=target, anchor=" ".join("".join(anchor.itertext()).split()))
            )
    evidence = ExtractionEvidence(
        schema="chimera.extraction-evidence/1",
        source_sha256=hashlib.sha256(page.body).hexdigest(),
        rendered_sha256=page.rendered.html_sha256 if page.rendered is not None else None,
        source_url=page.final_url,
        text_sha256=hashlib.sha256(text.encode()).hexdigest(),
        config_digest=config.content_digest(),
        parser_revision=fast_reader_revision(),
        encoding=actual_encoding,
        decode_replacements=source.count("�"),
        selection="generic",
        profile_id=None,
        locators=(),
        missing_fields=(),
        declared_language=declared_language,
        language_confidence=highest.value,
        language_margin=margin,
        language_sample_chars=len(sample),
        language_hint_disagrees=bool(
            declared_language
            and language != "und"
            and declared_language.split("-", 1)[0].lower() != language
        ),
        raw_markdown_sha256=hashlib.sha256(full.encode()).hexdigest(),
        omitted_links=len(beyond),
        omitted_text_chars=len(full) - len(text),
    )
    return Extracted(
        title=title,
        text=text,
        language=language,
        links=tuple(links),
        byline=byline,
        date=date,
        canonical_url=public_link(page.final_url, canonical) if canonical else None,
        extraction=evidence,
    )
