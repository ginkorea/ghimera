"""Bounded passive RSS/Atom/sitemap/JSON Feed parsing; no URL is contacted here."""

import hashlib
import re
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin
from xml.etree.ElementTree import Element, ParseError, XMLPullParser

from pydantic import BaseModel, ConfigDict, ValidationError

from ghimera.source_feed_config import JSON_TYPES, XML_TYPES, FeedFormat, SourceFeedConfig
from ghimera.source_feed_types import SourceFeedEntry, SourceFeedEvidence

ATOM = "{http://www.w3.org/2005/Atom}"
SITEMAP = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


class PlainText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._blocked: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._blocked.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._blocked and tag == self._blocked[-1]:
            self._blocked.pop()

    def handle_data(self, data: str) -> None:
        if not self._blocked:
            self.parts.append(data)


def plain(value: str) -> str:
    parser = PlainText()
    parser.feed(value)
    parser.close()
    return " ".join(part.strip() for part in parser.parts if part.strip())


def text(parent: Element, name: str) -> str:
    node = parent.find(name)
    return "".join(node.itertext()).strip() if node is not None else ""


def atom_text(parent: Element, name: str) -> str:
    node = parent.find(ATOM + name)
    if node is None:
        return ""
    if node.get("type", "text") == "html":
        return plain("".join(node.itertext()))
    if node.get("type") == "xhtml":
        return " ".join(part.strip() for part in node.itertext() if part.strip())
    return "".join(node.itertext()).strip()


def xml(
    raw: bytes, source_url: str, policy: SourceFeedConfig
) -> tuple[Element, dict[Element, str]]:
    # This initial native dialect admits UTF-8 only. Reject before the parser
    # can expand entities; NUL rejection also prevents encoded declaration bypass.
    source = raw.decode("utf-8-sig", errors="strict")
    if "\x00" in source or "<!DOCTYPE" in source.upper() or "<!ENTITY" in source.upper():
        raise ValueError("feed XML cannot contain declarations or external entities")
    declared = re.match(r"""^<\?xml\s+[^?]*encoding\s*=\s*["']([^"']+)["']""", source)
    if declared is not None and declared.group(1).lower() not in {"utf-8", "utf8"}:
        raise ValueError("this native feed dialect requires declared UTF-8")
    parser: XMLPullParser[Element] = XMLPullParser(events=("start", "end"))
    root: Element | None = None
    bases: dict[Element, str] = {}
    stack: list[str] = []
    count = 0
    for offset in range(0, len(source), 65536):
        parser.feed(source[offset : offset + 65536])
        for observation in parser.read_events():
            if len(observation) != 2:
                raise ValueError("feed XML parser returned an unexpected event")
            event, node = observation
            if event not in {"start", "end"} or not isinstance(node, Element):
                raise ValueError("feed XML parser returned an unexpected node")
            if event == "start":
                count += 1
                if count > policy.max_xml_nodes or len(stack) >= policy.max_xml_depth:
                    raise ValueError("feed XML exceeds its node/depth bound")
                if root is None:
                    root = node
                base = urljoin(stack[-1] if stack else source_url, node.get(XML_BASE, ""))
                bases[node] = base
                stack.append(base)
            else:
                stack.pop()
    parser.close()
    if root is None or stack:
        raise ValueError("feed XML is incomplete")
    return root, bases


def xml_entries(
    raw: bytes, source_url: str, policy: SourceFeedConfig
) -> tuple[FeedFormat, str, str, tuple[SourceFeedEntry, ...]]:
    root, bases = xml(raw, source_url, policy)
    entries: list[SourceFeedEntry] = []

    def entry(
        node: Element,
        observed: str,
        title: str,
        content: str = "",
        date: str = "",
        identity: str = "",
        *,
        role: Literal["document", "feed"] = "document",
    ) -> None:
        entries.append(
            SourceFeedEntry(
                role=role,
                base_url=bases[node],
                observed_url=observed,
                title=title,
                content=content,
                declared_date=date,
                declared_id=identity,
            )
        )
        if len(entries) > policy.max_entries:
            raise ValueError("feed exceeds its entry bound")

    if root.tag == "rss" and root.get("version") == "2.0":
        channel = root.find("channel")
        if channel is None:
            raise ValueError("RSS requires a channel")
        fmt: FeedFormat = "rss"
        title, language = plain(text(channel, "title")), text(channel, "language")
        for node in channel.findall("item"):
            guid = node.find("guid")
            observed = text(node, "link")
            if not observed and guid is not None and guid.get("isPermaLink", "true") == "true":
                observed = (guid.text or "").strip()
            heading, content = plain(text(node, "title")), plain(text(node, "description"))
            entry(node, observed, heading, content, text(node, "pubDate"), text(node, "guid"))
            if policy.include_attachments:
                for attachment in node.findall("enclosure"):
                    entry(attachment, attachment.get("url", ""), heading)
    elif root.tag == ATOM + "feed":
        fmt = "atom"
        title, language = atom_text(root, "title"), root.get(XML_LANG, "")
        for node in root.findall(ATOM + "entry"):
            heading = atom_text(node, "title")
            content = atom_text(node, "summary") or atom_text(node, "content")
            links = node.findall(ATOM + "link")
            alternate = next((n for n in links if n.get("rel", "alternate") == "alternate"), None)
            entry(
                alternate if alternate is not None else node,
                alternate.get("href", "") if alternate is not None else "",
                heading,
                content,
                text(node, ATOM + "published") or text(node, ATOM + "updated"),
                text(node, ATOM + "id"),
            )
            if policy.include_attachments:
                for attachment in links:
                    if attachment.get("rel") == "enclosure":
                        entry(attachment, attachment.get("href", ""), heading)
    elif root.tag in {SITEMAP + "urlset", SITEMAP + "sitemapindex"}:
        fmt, title, language = "sitemap", "", ""
        index = root.tag == SITEMAP + "sitemapindex"
        for node in root.findall(SITEMAP + ("sitemap" if index else "url")):
            observed = text(node, SITEMAP + "loc")
            entry(
                node,
                observed,
                observed,
                date=text(node, SITEMAP + "lastmod"),
                role="feed" if index else "document",
            )
    else:
        raise ValueError("source is not an admitted RSS/Atom/sitemap dialect")
    return fmt, title, language, tuple(entries)


class JsonRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class Attachment(JsonRecord):
    url: str


class JsonItem(JsonRecord):
    id: str
    url: str = ""
    external_url: str = ""
    title: str = ""
    content_text: str = ""
    content_html: str = ""
    summary: str = ""
    date_published: str = ""
    date_modified: str = ""
    attachments: tuple[Attachment, ...] = ()


class JsonFeed(JsonRecord):
    version: Literal["https://jsonfeed.org/version/1", "https://jsonfeed.org/version/1.1"]
    title: str
    language: str = ""
    items: tuple[JsonItem, ...]


def parse_feed(
    raw: bytes, source_url: str, content_type: str, policy: SourceFeedConfig
) -> SourceFeedEvidence:
    if not raw or len(raw) > policy.max_input_bytes or content_type not in policy.content_types:
        raise ValueError("feed source exceeds its MIME/byte admission")
    try:
        if content_type in XML_TYPES:
            fmt, title, language, entries = xml_entries(raw, source_url, policy)
        elif content_type in JSON_TYPES:
            if policy.site_api is not None:
                from ghimera.source_api import api_entries

                entries = api_entries(raw, source_url, policy.site_api, policy.max_entries)
                evidence = SourceFeedEvidence(
                    schema="ghimera.source-feed-evidence/1",
                    policy=policy,
                    source_url=source_url,
                    source_sha256=hashlib.sha256(raw).hexdigest(),
                    content_type=content_type,
                    format="json_api",
                    title=policy.site_api.title,
                    declared_language=policy.site_api.language,
                    entries=entries,
                )
                if len(evidence.model_dump_json().encode()) > policy.max_output_bytes:
                    raise ValueError("API output exceeds its configured response bound")
                return evidence
            wire = JsonFeed.model_validate_json(raw)
            fmt, title, language = "json_feed", wire.title, wire.language
            entries_list: list[SourceFeedEntry] = []
            for item in wire.items:
                entries_list.append(
                    SourceFeedEntry(
                        role="document",
                        base_url=source_url,
                        observed_url=item.url or item.external_url,
                        title=item.title,
                        content=item.content_text or plain(item.content_html) or item.summary,
                        declared_date=item.date_published or item.date_modified,
                        declared_id=item.id,
                    )
                )
                if policy.include_attachments:
                    entries_list.extend(
                        SourceFeedEntry(
                            role="document",
                            base_url=source_url,
                            observed_url=attachment.url,
                            title=item.title,
                            content="",
                            declared_date="",
                            declared_id=item.id,
                        )
                        for attachment in item.attachments
                    )
                if len(entries_list) > policy.max_entries:
                    raise ValueError("feed exceeds its entry bound")
            entries = tuple(entries_list)
        else:
            raise ValueError("unsupported source feed MIME")
        evidence = SourceFeedEvidence(
            schema="ghimera.source-feed-evidence/1",
            policy=policy,
            source_url=source_url,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            content_type=content_type,
            format=fmt,
            title=title,
            declared_language=language,
            entries=entries,
        )
        if len(evidence.model_dump_json().encode()) > policy.max_output_bytes:
            raise ValueError("feed output exceeds its configured response bound")
        return evidence
    except (UnicodeError, ParseError, ValidationError) as exc:
        raise ValueError("malformed source feed") from exc
