"""Bounded passive HTML admission; never download an excluded candidate."""

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser

from ghimera.models import Page
from ghimera.responsive_images import SourceSet, safe_image_url, selected_sources
from ghimera.visual_config import VisualConfig
from ghimera.visual_types import ImageCandidate, ResponsiveSelection

VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


@dataclass(frozen=True)
class VisualParent:
    """Captured source fields only: no synthetic HTTP response for archive replay."""

    final_url: str
    body: bytes


class CandidateParser(HTMLParser):
    def __init__(
        self, page: VisualParent, config: VisualConfig, markup: bytes, encoding: str
    ) -> None:
        super().__init__(convert_charrefs=True)
        self.page, self.config = page, config
        self.candidates: list[ImageCandidate] = []
        self._index = 0
        self._excluded: list[str] = []
        self._figure: list[int] | None = None
        self._caption = False
        self._parent_sha = hashlib.sha256(page.body).hexdigest()
        self._markup_sha = hashlib.sha256(markup).hexdigest()
        self._encoding = encoding
        self._elements: list[str] = []
        self._picture: list[SourceSet] | None = None
        self._picture_count = 0
        self._picture_overflow = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        parent = self._elements[-1] if self._elements else None
        if tag not in VOID_ELEMENTS:
            self._elements.append(tag)
        if tag in {"script", "style", "noscript", "nav", "header", "footer"}:
            self._excluded.append(tag)
        if self._excluded:
            return
        responsive = self.config.responsive
        if tag == "picture" and responsive is not None:
            self._picture, self._picture_count, self._picture_overflow = [], 0, False
        if tag == "source" and parent == "picture" and self._picture is not None:
            self._picture_count += 1
            if responsive is not None and self._picture_count <= responsive.max_picture_sources:
                values = {key: value or "" for key, value in attrs}
                self._picture.append(SourceSet(values, self._picture_count - 1))
            else:
                self._picture_overflow = True
        if tag == "figure":
            self._figure = []
        if tag == "figcaption":
            self._caption = True
        if tag != "img":
            return
        self._index += 1
        if self._index > self.config.max_candidates_per_page:
            return
        values = {key: value or "" for key, value in attrs}
        if values.get("aria-hidden") == "true" or values.get("role") == "presentation":
            return

        if responsive is not None:
            sources = selected_sources(
                self.page.final_url,
                self._markup_sha,
                self._encoding,
                values,
                tuple(self._picture or ()),
                responsive,
                self.config.image_types,
                picture_overflow=self._picture_overflow,
            )
            for url, selection in sources:
                if len(self.candidates) >= self.config.max_candidates_per_page:
                    break
                self._add(values, url, selection=selection)
            return
        observed = values.get("data-src") or values.get("src", "")
        if single_url := safe_image_url(self.page.final_url, observed):
            self._add(values, single_url)

    def _add(
        self,
        values: dict[str, str],
        url: str,
        *,
        selection: ResponsiveSelection | None = None,
    ) -> None:

        def dimension(name: str) -> int | None:
            value = values.get(name, "")
            return int(value) if re.fullmatch(r"[0-9]{1,8}", value) else None

        candidate = ImageCandidate(
            url=url,
            parent_url=self.page.final_url,
            parent_sha256=self._parent_sha,
            element_index=self._index,
            caption=" ".join(values.get(k, "") for k in ("alt", "title")).strip(),
            attributes=" ".join(values.get(k, "") for k in ("id", "class", "role", "aria-hidden")),
            declared_width=dimension("width"),
            declared_height=dimension("height"),
            responsive=selection,
        )
        self.candidates.append(candidate)
        if self._figure is not None:
            self._figure.append(len(self.candidates) - 1)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._elements:
            self._elements = self._elements[
                : len(self._elements) - 1 - self._elements[::-1].index(tag)
            ]
        if tag in self._excluded:
            self._excluded = self._excluded[: self._excluded.index(tag)]
        if tag == "figcaption":
            self._caption = False
        if tag == "figure":
            self._figure = None
        if tag == "picture":
            self._picture = None
            self._picture_count, self._picture_overflow = 0, False

    def handle_data(self, data: str) -> None:
        if self._caption and not self._excluded and self._figure is not None:
            for index in self._figure:
                item = self.candidates[index]
                self.candidates[index] = item.model_copy(
                    update={"caption": (item.caption + " " + data).strip()}
                )


def admitted(candidate: ImageCandidate, config: VisualConfig) -> bool:
    observed = (candidate.url + " " + candidate.caption + " " + candidate.attributes).casefold()
    if any(token in observed for token in config.excluded_tokens):
        return False
    if candidate.declared_width is not None and candidate.declared_width < config.min_width:
        return False
    if candidate.declared_height is not None and candidate.declared_height < config.min_height:
        return False
    return any(term in observed for term in config.candidate_terms)


def image_candidates(page: Page, config: VisualConfig) -> tuple[ImageCandidate, ...]:
    if page.content_type.split(";", 1)[0] not in {"text/html", "application/xhtml+xml"}:
        return ()
    # Browser DOM is UTF-8; declared HTTP charset is respected for native HTML.
    charset = "utf-8"
    if page.rendered is None and page.human_browser is None:
        for part in page.content_type.split(";")[1:]:
            if part.strip().lower().startswith("charset="):
                charset = part.strip().split("=", 1)[1].strip("\"'")
    body = page.rendered.html if page.rendered is not None else page.body
    return image_candidates_from_markup(page.final_url, page.body, body, charset, config)


def image_candidates_from_markup(
    parent_url: str, original: bytes, markup: bytes, encoding: str, config: VisualConfig
) -> tuple[ImageCandidate, ...]:
    """Pure policy replay over retained source bytes; performs no resource requests."""
    parser = CandidateParser(VisualParent(parent_url, original), config, markup, encoding)
    try:
        decoded = markup.decode(encoding, errors="replace")
    except (LookupError, ValueError) as exc:
        raise ValueError("visual markup requires a supported text encoding") from exc
    parser.feed(decoded)
    parser.close()
    return tuple(parser.candidates)
