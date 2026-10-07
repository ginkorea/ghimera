"""Bounded passive HTML admission; never download an excluded candidate."""

import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from ghimera.models import Page
from ghimera.visual_config import VisualConfig
from ghimera.visual_types import ImageCandidate


class CandidateParser(HTMLParser):
    def __init__(self, page: Page, config: VisualConfig) -> None:
        super().__init__(convert_charrefs=True)
        self.page, self.config = page, config
        self.candidates: list[ImageCandidate] = []
        self._index = 0
        self._excluded: list[str] = []
        self._figure: list[int] | None = None
        self._caption = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "nav", "header", "footer"}:
            self._excluded.append(tag)
        if self._excluded:
            return
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
        observed = values.get("data-src") or values.get("src", "")
        if not observed:
            return
        url = urljoin(self.page.final_url, observed)
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or any(ord(c) < 33 for c in url)
        ):
            return

        def dimension(name: str) -> int | None:
            value = values.get(name, "")
            return int(value) if re.fullmatch(r"[0-9]{1,8}", value) else None

        candidate = ImageCandidate(
            url=url,
            parent_url=self.page.final_url,
            parent_sha256=hashlib.sha256(self.page.body).hexdigest(),
            element_index=self._index,
            caption=" ".join(values.get(k, "") for k in ("alt", "title")).strip(),
            attributes=" ".join(values.get(k, "") for k in ("id", "class", "role", "aria-hidden")),
            declared_width=dimension("width"),
            declared_height=dimension("height"),
        )
        if values.get("aria-hidden") == "true" or values.get("role") == "presentation":
            return
        self.candidates.append(candidate)
        if self._figure is not None:
            self._figure.append(len(self.candidates) - 1)

    def handle_endtag(self, tag: str) -> None:
        if tag in self._excluded:
            self._excluded = self._excluded[: self._excluded.index(tag)]
        if tag == "figcaption":
            self._caption = False
        if tag == "figure":
            self._figure = None

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
    parser = CandidateParser(page, config)
    parser.feed(body.decode(charset, errors="replace"))
    parser.close()
    return tuple(parser.candidates)
