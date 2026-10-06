"""Replayable native URL observations; these are not bibliographic truth claims."""

from typing import Annotated, Literal
from urllib.parse import urldefrag, urljoin, urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.document_types import DocumentLayout

NonNegative = Annotated[int, Field(strict=True, ge=0)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)


def reference_url(base: str, value: str) -> str | None:
    """Normalize an observed link, without granting scope or resolving DNS."""
    try:
        target = urldefrag(urljoin(base, value)).url
        parts = urlsplit(target)
        if (
            parts.scheme in {"http", "https"}
            and parts.hostname
            and parts.username is None
            and parts.password is None
            and not any(ord(char) < 33 for char in target)
        ):
            if parts.port is not None and not 1 <= parts.port <= 65535:
                return None
            return target
    except ValueError:
        pass
    return None


class ReferenceSpan(Frozen):
    start: NonNegative
    end: NonNegative
    quote: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def extent(self) -> "ReferenceSpan":
        if self.end - self.start != len(self.quote):
            raise ValueError("reference locator must cover its exact quotation")
        return self


class _LayoutText(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str
    hyperlink: str | None = None


class _LayoutLinks(BaseModel):
    model_config = ConfigDict(extra="ignore")
    texts: tuple[_LayoutText, ...]


class DocumentReference(Frozen):
    schema_version: Literal["chimera.document-reference/1"] = Field(alias="schema")
    target_url: Annotated[str, Field(min_length=1)]
    anchor: str
    kind: Literal["native_url", "docling_hyperlink"]
    span: ReferenceSpan | None = None
    layout_index: NonNegative | None = None
    observed_url: str | None = None
    base_url: str = ""

    @model_validator(mode="after")
    def shape(self) -> "DocumentReference":
        if self.kind == "native_url":
            if self.span is None or self.layout_index is not None or self.observed_url is not None:
                raise ValueError("native reference requires only a text locator")
            observed = self.span.quote
        else:
            if self.span is not None or self.layout_index is None or self.observed_url is None:
                raise ValueError("Docling reference requires a retained layout locator")
            observed = self.observed_url
        if reference_url(self.base_url, observed) != self.target_url:
            raise ValueError("reference target must be the observed, normalized URL")
        return self

    def matches(self, text: str, layout: DocumentLayout | None) -> bool:
        if self.span is not None:
            return text[self.span.start : self.span.end] == self.span.quote
        if layout is None or self.layout_index is None:
            return False
        links = _LayoutLinks.model_validate_json(layout.docling_json).texts
        return self.layout_index < len(links) and (
            links[self.layout_index].hyperlink == self.observed_url
            and links[self.layout_index].text == self.anchor
        )


class ReferenceSource(Frozen):
    url: str
    sha256: Hash
    text_sha256: Hash


class SearchReference(Frozen):
    """Provider-observed candidate, never an assertion that a citation exists."""

    schema_version: Literal["chimera.search-reference/1"] = Field(alias="schema")
    target_url: Annotated[str, Field(min_length=1)]
    anchor: str
    snippet: str
    provider: Annotated[str, Field(min_length=1)]
    provider_revision: Annotated[str, Field(min_length=1)]
    query: Annotated[str, Field(min_length=1)]
    response_sha256: Hash
    query_sequence: NonNegative

    @model_validator(mode="after")
    def public_shape(self) -> "SearchReference":
        if reference_url("", self.target_url) != self.target_url:
            raise ValueError("search reference must use its actual normalized provider URL")
        return self


class ReferenceQuery(Frozen):
    schema_version: Literal["chimera.reference-query/1"] = Field(alias="schema")
    source: ReferenceSource
    parent_hops: NonNegative
    origin_url: str | None = None
    query: Annotated[str, Field(min_length=1)]
    provider: Annotated[str, Field(min_length=1)]
    provider_revision: Annotated[str, Field(min_length=1)]


class ReferenceDecision(Frozen):
    schema_version: Literal["chimera.reference-decision/1"] = Field(alias="schema")
    source: ReferenceSource
    reference: DocumentReference | SearchReference
    parent_hops: NonNegative
    origin_url: str | None = None
    score: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    outcome: Literal[
        "queued",
        "disabled",
        "hop_limit",
        "out_of_scope",
        "budget_limit",
        "below_score",
        "already_seen",
    ]
