"""Immutable validated package objects; TAIPAN dataclass conversion belongs to C4."""

import hashlib
import ipaddress
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from ghimera.browser_operation_types import BrowserSourceAction
from ghimera.browser_types import RenderResult
from ghimera.challenge_types import ChallengeEvidence
from ghimera.config import GhimeraConfig, Probability
from ghimera.dedup_types import ContentDrift, DedupEvidence
from ghimera.document_types import DocumentLayout, DocumentParseEvidence
from ghimera.embedding_types import EncodingCall, IntentReferenceEvidence
from ghimera.extraction_attempts import HtmlExtractionAttempt, validate_chain
from ghimera.extraction_types import ExtractionEvidence
from ghimera.graph_planning_types import PlanningGraph
from ghimera.graph_types import (
    GraphPdfReading,
    GraphReadingPage,
    GraphRetainedOrigin,
    GraphSnapshot,
    GraphVisualReading,
)
from ghimera.human_browser_types import (
    AssistanceObservation,
    BrowserSourceEvidence,
    validate_browser_body,
)
from ghimera.identity_automation_types import (
    IdentityHistory,
    IdentityObservation,
    IdentityProposal,
    IdentityProposalRequest,
    IdentityReview,
)
from ghimera.judgment_types import (
    DocumentJudgmentEvidence,
    JudgmentContextReservation,
    ScoringNativeReading,
    ScoringSourceBinding,
)
from ghimera.local_input_types import LocalInputEvidence
from ghimera.model_reconciliation_types import ModelAttemptConsumption, ModelReconciliationDecision
from ghimera.model_types import IdentityCallEvidence, ModelCallEvidence
from ghimera.model_work_types import ModelAcknowledgement, ModelIntent, ModelReplay
from ghimera.page_transcription_config import PdfTranscriptionConfig
from ghimera.page_transcription_types import PageTranscriptionCall, ReviewedPageTranscription
from ghimera.query_work_types import QueryAcknowledgement, QueryReservation
from ghimera.reference_types import DocumentReference, ReferenceDecision, ReferenceQuery
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.research_reranking_types import RerankReservation
from ghimera.run_encoding_types import (
    RunEncodingAcknowledgement,
    RunEncodingIntent,
    RunEncodingReplay,
)
from ghimera.scoring_types import SimilarityEvidence
from ghimera.semantic_selection_types import SemanticSelection
from ghimera.semantic_types import (
    FactorizedSemanticReview,
    GroundedSemanticReview,
    ReviewSelection,
    SemanticRefusal,
    SemanticReview,
    SemanticWindow,
)
from ghimera.source_acquisition_types import SourceAcquisitionReturn
from ghimera.source_feed_types import SourceFeedEvidence
from ghimera.source_refresh_types import SourceRefreshUse
from ghimera.source_session_types import SourceSessionUse
from ghimera.transport_types import TransportEvidence
from ghimera.visual_types import ImageCandidate, ImageEvidence

NonEmpty = Annotated[str, Field(min_length=1)]
NonNegative = Annotated[int, Field(strict=True, ge=0)]
StopReason = Literal["frontier_empty", "budget_exhausted", "saturated", "goal_satisfied", "failed"]


class Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        serialize_by_alias=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )


class Goal(Record):
    text: NonEmpty
    seeds: tuple[str, ...] = ()

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal text must not be blank")
        return value


class Scope(Record):
    allowed_hosts: Annotated[tuple[str, ...], Field(min_length=1)]
    max_depth: NonNegative
    content_types: Annotated[tuple[str, ...], Field(min_length=1)]
    allowed_ports: Annotated[
        tuple[Annotated[int, Field(strict=True, ge=1, le=65535)], ...], Field(min_length=1)
    ] = (80, 443)

    @field_validator("allowed_hosts")
    @classmethod
    def exact_public_hosts(cls, hosts: tuple[str, ...]) -> tuple[str, ...]:
        for host in hosts:
            if host != host.lower() or host.endswith(".") or ":" in host or "/" in host:
                raise ValueError("allowed_hosts must be lowercase exact DNS names without ports")
            if "." not in host or any(
                not label or not label.replace("-", "").isalnum() for label in host.split(".")
            ):
                raise ValueError("allowed_hosts must be exact public DNS names")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                raise ValueError("IP literals are not permitted as crawl hosts")
        return hosts

    def permits(self, url: str) -> bool:
        try:
            parts = urlsplit(url)
            return (
                parts.scheme in {"http", "https"}
                and parts.username is None
                and parts.password is None
                and parts.hostname in self.allowed_hosts
                and (parts.port or (443 if parts.scheme == "https" else 80)) in self.allowed_ports
                and not any(ord(char) < 33 for char in url)
            )
        except ValueError:
            return False


class LinkCandidate(Record):
    url: NonEmpty
    anchor: str = ""
    score: Probability = 0.0


class FetchRequest(Record):
    url: NonEmpty
    max_bytes: Annotated[int, Field(strict=True, gt=0)]
    timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    headers: tuple[tuple[Literal["if-none-match", "if-modified-since"], str], ...] = ()
    scope: Scope | None = Field(default=None, exclude_if=lambda v: v is None)

    @field_validator("headers")
    @classmethod
    def one_line(cls, value: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
        if any("\r" in text or "\n" in text for _, text in value):
            raise ValueError("request metadata must be single-line HTTP headers")
        return value


class Page(Record):
    url: NonEmpty
    final_url: NonEmpty
    status: Annotated[int, Field(strict=True, ge=100, le=599)] | None
    content_type: NonEmpty
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()
    revalidated: bool = False
    source_refresh: SourceRefreshUse | None = Field(default=None, exclude_if=lambda v: v is None)
    transport: TransportEvidence | None = None
    rendered: RenderResult | None = None
    source_session: SourceSessionUse | None = None
    challenge_use: ChallengeEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    local_input: LocalInputEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    human_browser: BrowserSourceEvidence | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def rendering_binding(self) -> "Page":
        if self.source_refresh is not None and (
            not self.revalidated
            or self.status != 200
            or self.final_url != self.source_refresh.source_url
            or hashlib.sha256(self.body).hexdigest() != self.source_refresh.source_sha256
            or self.human_browser is not None
            or self.local_input is not None
            or self.challenge_use is not None
        ):
            raise ValueError(
                "refresh reuse requires its exact original and native HTTP revalidation"
            )
        if (self.status is None) != (self.human_browser is not None):
            raise ValueError("only explicit browser acquisition has no HTTP status")
        if self.human_browser is not None:
            validate_browser_body(self.body, self.human_browser)
            if (
                self.url != self.human_browser.request_url
                or self.final_url != self.human_browser.final_url
                or self.content_type != self.human_browser.content_type
                or self.headers
                or self.revalidated
                or any(
                    value is not None
                    for value in (
                        self.transport,
                        self.rendered,
                        self.source_session,
                        self.challenge_use,
                        self.local_input,
                    )
                )
            ):
                raise ValueError(
                    "browser acquisition cannot impersonate an HTTP response or isolated render"
                )
        if self.local_input is not None and (
            self.url != self.local_input.source_id
            or self.final_url != self.url
            or self.local_input.sha256 != hashlib.sha256(self.body).hexdigest()
            or self.local_input.size_bytes != len(self.body)
            or self.local_input.content_type != self.content_type
            or any(
                value is not None
                for value in (
                    self.transport,
                    self.rendered,
                    self.source_session,
                    self.challenge_use,
                )
            )
        ):
            raise ValueError("local parser input must bind its snapshot without network evidence")
        if self.source_session is not None and self.source_session.request_url != self.final_url:
            raise ValueError("source session selection must bind this response URL")
        if self.rendered is not None and (
            self.rendered.source_sha256 != hashlib.sha256(self.body).hexdigest()
            or self.rendered.source_url != self.final_url
        ):
            raise ValueError("rendering must bind original retained response bytes and URL")
        return self

    def header(self, name: str) -> str | None:
        return next((value for key, value in self.headers if key == name.lower()), None)


class Extracted(Record):
    title: NonEmpty
    text: NonEmpty
    language: NonEmpty
    links: tuple[LinkCandidate, ...] = ()
    byline: str | None = None
    date: str | None = None
    canonical_url: str | None = None
    extraction: ExtractionEvidence | None = None
    document_parse: DocumentParseEvidence | None = None
    document_layout: DocumentLayout | None = None
    pdf_transcription: "PdfTranscriptionEvidence | None" = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_feed: SourceFeedEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    references: tuple[DocumentReference, ...] = ()

    @model_validator(mode="after")
    def text_binding(self) -> "Extracted":
        if self.source_feed is not None and (
            self.text != self.source_feed.text
            or self.title != self.source_feed.reading_title
            or self.language != "und"
            or tuple((link.url, link.anchor) for link in self.links) != self.source_feed.links
            or any(
                value is not None
                for value in (
                    self.extraction,
                    self.document_parse,
                    self.document_layout,
                    self.pdf_transcription,
                )
            )
        ):
            raise ValueError("feed extraction must preserve native declarations and exact links")
        if self.pdf_transcription is not None and (
            self.text != self.pdf_transcription.text
            or self.language != self.pdf_transcription.config.language_hint
            or self.document_parse is not None
            or self.document_layout is not None
            or self.extraction is not None
        ):
            raise ValueError("generated PDF reading must remain distinct from native extraction")
        eligible = {(link.url, link.anchor) for link in self.links}
        if any(
            (item.target_url, item.anchor) not in eligible
            or not item.matches(self.text, self.document_layout)
            for item in self.references
        ):
            raise ValueError("references must bind observed native text/layout and extracted links")
        if len({item.target_url for item in self.references}) != len(self.references):
            raise ValueError("reference URLs must be unique within a document")
        if (
            self.extraction is not None
            and self.extraction.text_sha256 != hashlib.sha256(self.text.encode()).hexdigest()
        ):
            raise ValueError("extraction evidence must bind its native text")
        if (self.document_parse is None) != (self.document_layout is None):
            raise ValueError("document conversion requires both provenance and retained layout")
        if self.document_parse is not None and self.document_layout is not None:
            if self.document_parse.text_sha256 != hashlib.sha256(self.text.encode()).hexdigest():
                raise ValueError("document conversion must bind native text")
            if self.document_parse.layout_sha256 != self.document_layout.sha256:
                raise ValueError("document conversion must bind retained layout")
        return self


class PdfTranscriptionEvidence(Record):
    schema_version: Literal["ghimera.pdf-transcription-evidence/1"] = Field(alias="schema")
    source_url: NonEmpty
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    config: PdfTranscriptionConfig
    pages: Annotated[tuple[ReviewedPageTranscription, ...], Field(min_length=1)]
    native_reading: Extracted | None
    native_refusal: Literal[RefusalCode.EXTRACTION_FAILED] | None

    @property
    def text(self) -> str:
        return "\n\n".join(page.proposal.text for page in self.pages)

    def cited_pages(self, start: int, end: int) -> tuple[int, ...]:
        offset = 0
        indices = []
        for page in self.pages:
            stop = offset + len(page.proposal.text)
            if start < stop and end > offset:
                indices.append(page.page.page_index)
            offset = stop + 2
        return tuple(indices)

    def graph_reading(self) -> GraphPdfReading:
        """Source-bound graph references to the full evidence retained on this reading."""
        offset = 0
        pages = []
        for page in self.pages:
            end = offset + len(page.proposal.text)
            pages.append(
                GraphReadingPage(
                    page_index=page.page.page_index,
                    start=offset,
                    end=end,
                    text_sha256=hashlib.sha256(page.proposal.text.encode()).hexdigest(),
                    image_sha256=page.page.image_sha256,
                    transcription_call_sha256=hashlib.sha256(
                        page.calls[0].model_dump_json().encode()
                    ).hexdigest(),
                    review_call_sha256=hashlib.sha256(
                        page.calls[1].model_dump_json().encode()
                    ).hexdigest(),
                )
            )
            offset = end + 2
        return GraphPdfReading(
            schema="ghimera.graph-pdf-reading/1",
            source_sha256=self.source_sha256,
            text_sha256=hashlib.sha256(self.text.encode()).hexdigest(),
            config_sha256=self.config.content_digest(),
            pages=tuple(pages),
        )

    @model_validator(mode="after")
    def complete_source(self) -> "PdfTranscriptionEvidence":
        if (self.native_reading is None) != (self.native_refusal is not None):
            raise ValueError("retain the original extraction or its exact eligible refusal")
        if self.native_reading is not None and (
            self.native_reading.pdf_transcription is not None
            or self.native_reading.document_parse is None
            or self.native_reading.document_parse.source_sha256 != self.source_sha256
            or self.native_reading.document_parse.source_url != self.source_url
        ):
            raise ValueError("native reading must bind this original PDF without recursion")
        render = self.config.pages.renderer
        if (
            len(self.pages) > render.max_pages
            or sum(len(page.page.png) for page in self.pages) > render.max_total_image_bytes
            or len(self.text) > self.config.max_document_text_chars
            or any(
                not page.accepted
                or page.page.page_index != index
                or page.page.page_count != len(self.pages)
                or page.page.source_sha256 != self.source_sha256
                or page.config != self.config.pages
                or page.language_hint != self.config.language_hint
                for index, page in enumerate(self.pages)
            )
        ):
            raise ValueError("PDF reading requires complete, ordered and reviewed source pages")
        return self


Extracted.model_rebuild()


class Verdict(Record):
    model_call: ModelCallEvidence | None = None
    decision: Literal["accept", "reject", "hold"]
    kind: NonEmpty
    publisher: NonEmpty
    language: NonEmpty
    reason: NonEmpty
    date: str | None = None

    @field_validator("reason", "kind", "publisher", "language")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("verdict fields must not be blank")
        return value


class Grade(Record):
    model_call: ModelCallEvidence | None = None
    satisfied: bool
    confidence: Probability
    reason: NonEmpty


class ModelIdentity(Record):
    model_id: NonEmpty
    revision: NonEmpty
    location: Literal["self_hosted", "external", "test_double"]


class DocumentSource(Record):
    url: NonEmpty
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    raw: bytes
    extracted: Extracted
    verdict: Verdict
    transport: TransportEvidence | None = None
    rendered: RenderResult | None = None
    source_session: SourceSessionUse | None = None
    source_refresh: SourceRefreshUse | None = Field(default=None, exclude_if=lambda v: v is None)
    challenge_use: ChallengeEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    local_input: LocalInputEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    human_browser: BrowserSourceEvidence | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    images: tuple[ImageEvidence, ...] = Field(default=(), exclude_if=lambda value: not value)

    def validate_policy(self, config: GhimeraConfig) -> None:
        if self.source_refresh is not None:
            self.source_refresh.validate_policy(config.source_refresh, self.url, self.sha256)
        if (
            self.extracted.source_feed is not None
            and self.extracted.source_feed.policy != config.source_feeds
        ):
            raise ValueError("feed extraction must bind the effective run configuration")
        transcription = self.extracted.pdf_transcription
        if transcription is not None:
            if transcription.config != config.pdf_transcription:
                raise ValueError("PDF transcription must bind the effective run recipe")
            if len(self.raw) > transcription.config.pages.renderer.max_input_bytes:
                raise ValueError("PDF transcription must respect its original-byte bound")
            if transcription.native_reading is not None:
                self.model_copy(update={"extracted": transcription.native_reading}).validate_policy(
                    config
                )
        if self.images:
            if config.visuals is None:
                raise ValueError("retained images require the effective visual recipe")
            visual_digest = hashlib.sha256(config.visuals.model_dump_json().encode()).hexdigest()
            from ghimera.pdf_figures import validate_pdf_images

            validate_pdf_images(self, config.visuals)
            if len(self.images) > config.visuals.max_images_per_page or any(
                image.config_sha256 != visual_digest
                or len(image.raw) > config.visuals.max_image_bytes
                or image.ocr.width * image.ocr.height > config.visuals.max_pixels
                or not set(image.ocr.language_pack_sha256) <= set(config.visuals.languages)
                for image in self.images
            ):
                raise ValueError("visual evidence must bind its exact policy and limits")
            selections: dict[str, tuple[ImageCandidate, ...]] = {}
            for image in self.images:
                if image.candidate.pdf_crop is not None:
                    continue
                selected = image.candidate.responsive
                if selected is None:
                    if config.visuals.responsive is not None:
                        raise ValueError("responsive images require their source selection record")
                    continue
                if (
                    config.visuals.responsive is None
                    or selected.policy_sha256 != config.visuals.responsive.identity
                ):
                    raise ValueError("responsive selection must bind its effective policy")
                from ghimera.image_candidates import image_candidates_from_markup

                markup = self.rendered.html if self.rendered is not None else self.raw
                encoding = selected.markup_encoding
                if encoding not in selections:
                    selections[encoding] = image_candidates_from_markup(
                        self.url, self.raw, markup, encoding, config.visuals
                    )
                if image.candidate not in selections[encoding]:
                    raise ValueError("responsive selection must replay from the retained markup")
        if self.human_browser is not None:
            self.human_browser.validate_policy(config.human_browser)
        if self.local_input is not None:
            self.local_input.validate_policy(config.local_inputs)
        if self.challenge_use is not None:
            self.challenge_use.validate_policy(config.challenges, self.url)
        if self.source_session is not None:
            self.source_session.validate_policy(config.source_sessions, self.url)
        if self.rendered is not None:
            if config.browser is None or len(self.raw) > config.browser.max_input_bytes:
                raise ValueError("browser rendering requires its input policy and limits")
            self.rendered.validate_policy(
                config.browser, max_redirects=config.http.max_redirects if config.http else 0
            )
            for resource in self.rendered.resources:
                if resource.source_session is not None:
                    resource.source_session.validate_policy(config.source_sessions, resource.url)
        evidence = self.extracted.extraction
        if evidence is not None and (
            config.extraction is None
            or evidence.config_digest != config.extraction.content_digest()
        ):
            raise ValueError("extraction must bind the effective run configuration")
        if evidence is not None and config.extraction is not None:
            evidence.validate_policy(config.extraction)
        parsed = self.extracted.document_parse
        if parsed is not None and (
            config.document_extraction is None
            or parsed.config_digest != config.document_extraction.content_digest()
        ):
            raise ValueError("document conversion must bind the effective run configuration")
        if parsed is not None and config.document_extraction is not None:
            media_policy = config.document_extraction.media
            if (parsed.media is None) != (media_policy is None):
                raise ValueError("document conversion must retain its configured media evidence")
            if parsed.media is not None and media_policy is not None:
                parsed.media.validate_policy(media_policy, self.raw)

    @model_validator(mode="after")
    def source_binding(self) -> "DocumentSource":
        if self.source_refresh is not None and (
            self.source_refresh.source_url != self.url
            or self.source_refresh.source_sha256 != self.sha256
            or self.human_browser is not None
            or self.local_input is not None
            or self.challenge_use is not None
        ):
            raise ValueError("refresh provenance belongs to its exact native source version")
        if any(
            image.candidate.parent_url != self.url or image.candidate.parent_sha256 != self.sha256
            for image in self.images
        ):
            raise ValueError("retained visuals must bind their original parent")
        if len({image.sha256 for image in self.images}) != len(self.images):
            raise ValueError("duplicate visual bytes must not be retained twice")
        if self.human_browser is not None:
            validate_browser_body(self.raw, self.human_browser)
            if self.human_browser.final_url != self.url or any(
                value is not None
                for value in (
                    self.transport,
                    self.rendered,
                    self.source_session,
                    self.challenge_use,
                    self.local_input,
                )
            ):
                raise ValueError(
                    "browser-observed sources retain their distinct acquisition evidence"
                )
        if self.local_input is not None and (
            self.local_input.source_id != self.url
            or self.local_input.sha256 != self.sha256
            or self.local_input.size_bytes != len(self.raw)
            or any(
                value is not None
                for value in (
                    self.transport,
                    self.rendered,
                    self.source_session,
                    self.challenge_use,
                )
            )
        ):
            raise ValueError("local source provenance must bind its retained original bytes")
        if self.url.startswith("urn:ghimera:local:") and self.local_input is None:
            raise ValueError("local sources require explicit import provenance")
        if self.source_session is not None and self.source_session.request_url != self.url:
            raise ValueError("source session selection must bind this source occurrence")
        digest = hashlib.sha256(self.raw).hexdigest()
        if self.sha256 != digest:
            raise ValueError("document digest must bind retained source bytes")
        feed = self.extracted.source_feed
        if feed is not None:
            if feed.source_url != self.url:
                raise ValueError("feed extraction must bind this source occurrence")
            feed.validate_source(self.raw)
        transcription = self.extracted.pdf_transcription
        if transcription is not None and (
            transcription.source_sha256 != digest
            or transcription.source_url != self.url
            or not self.raw.startswith(b"%PDF-")
        ):
            raise ValueError("reviewed PDF transcription must bind this original source")
        if self.rendered is not None and (
            self.rendered.source_sha256 != digest or self.rendered.source_url != self.url
        ):
            raise ValueError("browser rendering must bind this source occurrence")
        if self.extracted.extraction is not None and (
            self.extracted.extraction.source_sha256 != digest
            or self.extracted.extraction.source_url != self.url
        ):
            raise ValueError("extraction evidence must bind this source occurrence")
        if self.extracted.extraction is not None and (
            self.extracted.extraction.rendered_sha256
            != (self.rendered.html_sha256 if self.rendered is not None else None)
        ):
            raise ValueError("HTML extraction must identify its rendered or original input")
        if self.extracted.document_parse is not None and (
            self.extracted.document_parse.source_sha256 != digest
            or self.extracted.document_parse.source_url != self.url
        ):
            raise ValueError("document conversion must bind this source occurrence")
        if (
            self.extracted.document_parse is not None
            and self.extracted.document_parse.media is not None
        ):
            self.extracted.document_parse.media.validate_source(self.raw)
        return self


class DuplicateOccurrence(DocumentSource):
    dedup: DedupEvidence

    @model_validator(mode="after")
    def dedup_binding(self) -> "DuplicateOccurrence":
        if self.dedup.current.source_sha256 != self.sha256:
            raise ValueError("duplicate evidence must bind this source occurrence")
        if self.verdict.decision != "accept":
            raise ValueError("duplicate occurrences must have their own acceptance verdict")
        return self


class Document(DocumentSource):
    duplicate_urls: tuple[str, ...] = ()
    occurrences: tuple[DuplicateOccurrence, ...] = ()

    def evidence_sources(self) -> tuple["Document", ...]:
        """Every retained occurrence remains independently citable by its raw digest."""
        return (self,) + tuple(
            Document.model_validate(item.model_dump(exclude={"dedup"})) for item in self.occurrences
        )

    @model_validator(mode="after")
    def occurrences_binding(self) -> "Document":
        keys = {(item.url, item.sha256) for item in self.occurrences}
        if len(keys) != len(self.occurrences):
            raise ValueError("source occurrences cannot repeat")
        if any(item.dedup.representative_sha256 != self.sha256 for item in self.occurrences):
            raise ValueError("duplicate occurrence must name this representative")
        if self.occurrences and self.duplicate_urls != tuple(
            dict.fromkeys(item.url for item in self.occurrences if item.url != self.url)
        ):
            raise ValueError("duplicate URLs must project the retained source occurrences")
        return self


class RetainedOriginal(Record):
    """Self-contained original plus current query; historical calls stay in the original."""

    document: Document
    origin: GraphRetainedOrigin
    query_text: NonEmpty
    encoding_call: EncodingCall

    @property
    def revision(self) -> str:
        return "ghimera-retained-original/1@" + self.origin.document_sha256

    @model_validator(mode="after")
    def bound(self) -> "RetainedOriginal":
        call, origin = self.encoding_call, self.origin
        encoded = call.service.text_prefix + self.query_text
        if (
            self.document.verdict.decision != "accept"
            or origin.document_sha256
            != hashlib.sha256(self.document.model_dump_json().encode()).hexdigest()
            or origin.query_sha256 != hashlib.sha256(self.query_text.encode()).hexdigest()
            or origin.encoding_call_sha256
            != hashlib.sha256(call.model_dump_json().encode()).hexdigest()
            or call.outcome != "success"
            or call.input_sha256 != (hashlib.sha256(encoded.encode()).hexdigest(),)
            or call.input_chars != len(encoded)
        ):
            raise ValueError(
                "retained original requires its exact representation and current query"
            )
        return self


class LedgerRow(Record):
    sequence: NonNegative
    event: (
        Literal[
            "fetch",
            "fallback",
            "refusal",
            "verdict",
            "grade",
            "duplicate",
            "stop",
            "policy",
            "plan",
            "assessment",
            "answer",
            "review",
            "discovery",
            "extraction",
            "extraction_attempt",
            "content_drift",
            "render",
            "encoding",
            "scoring",
            "intent_reference",
            "reference",
            "reference_query",
            "challenge",
            "local_input",
            "semantic",
            "semantic_review",
            "identity_propose",
            "identity_review",
            "identity_resolution",
            "identity_history",
            "visual",
            "visual_model",
            "transcription_model",
            "transcription",
            "retained_source",
            "model_intent",
            "model_ack",
            "model_replay",
        ]
        | SkipJsonSchema[
            Literal[
                "model_attempt",
                "judgment_context",
                "scoring_source",
                "semantic_selection",
                "query_intent",
                "query_ack",
                "source_acquisition",
                "run_encoding_intent",
                "run_encoding_replay",
            ]
        ]
    )
    url: str | None = None
    route: str | None = None
    status: int | None = None
    bytes_read: NonNegative = 0
    latency_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0
    refusal: RefusalCode | None = None
    reason: str
    transport: TransportEvidence | None = None
    model: ModelIdentity | None = None
    query: str | None = None
    search_response_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    model_call: IdentityCallEvidence | ModelCallEvidence | None = None
    model_intent: ModelIntent | None = Field(default=None, exclude_if=lambda v: v is None)
    model_ack: ModelAcknowledgement | None = Field(default=None, exclude_if=lambda v: v is None)
    query_reservation: SkipJsonSchema[QueryReservation | None] = Field(
        default=None, exclude_if=lambda v: v is None
    )
    query_ack: SkipJsonSchema[QueryAcknowledgement | None] = Field(
        default=None, exclude_if=lambda v: v is None
    )
    model_replay: ModelReplay | None = Field(default=None, exclude_if=lambda v: v is None)
    rerank_reservation: SkipJsonSchema[RerankReservation | None] = Field(
        default=None, exclude_if=lambda v: v is None
    )
    model_decision: SkipJsonSchema[ModelReconciliationDecision | None] = Field(
        default=None, exclude_if=lambda v: v is None
    )
    model_attempt: SkipJsonSchema[ModelAttemptConsumption | None] = Field(
        default=None, exclude_if=lambda v: v is None
    )
    extraction: ExtractionEvidence | None = None
    extraction_attempt: HtmlExtractionAttempt | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    document_parse: DocumentParseEvidence | None = None
    source_feed: SourceFeedEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    dedup: DedupEvidence | None = None
    transcription_call: PageTranscriptionCall | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    content_drift: ContentDrift | None = None
    rendered: RenderResult | None = None
    encoding_call: EncodingCall | None = None
    run_encoding_intent: SkipJsonSchema[RunEncodingIntent | None] = Field(
        default=None, exclude_if=lambda v: v is None
    )
    run_encoding_ack: SkipJsonSchema[RunEncodingAcknowledgement | None] = Field(
        default=None, exclude_if=lambda v: v is None
    )
    run_encoding_sequence: SkipJsonSchema[NonNegative | None] = Field(
        default=None, exclude_if=lambda v: v is None
    )
    run_encoding_replay: SkipJsonSchema[RunEncodingReplay | None] = Field(
        default=None, exclude_if=lambda v: v is None
    )
    similarity: SimilarityEvidence | None = None
    scoring_source: SkipJsonSchema[ScoringSourceBinding | None] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    source_acquisition: SkipJsonSchema[SourceAcquisitionReturn | None] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    scoring_reading: SkipJsonSchema[ScoringNativeReading | None] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    judgment_context: SkipJsonSchema[JudgmentContextReservation | None] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    document_judgment: SkipJsonSchema[DocumentJudgmentEvidence | None] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    semantic_selection: SkipJsonSchema[SemanticSelection | None] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    intent_reference: IntentReferenceEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    reference: ReferenceDecision | None = None
    reference_query: ReferenceQuery | None = None
    source_session: SourceSessionUse | None = None
    source_refresh: SourceRefreshUse | None = Field(default=None, exclude_if=lambda v: v is None)
    challenge: ChallengeEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    challenge_use: ChallengeEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    local_input: LocalInputEvidence | None = Field(default=None, exclude_if=lambda v: v is None)
    semantic_window: SemanticWindow | None = Field(default=None, exclude_if=lambda v: v is None)
    semantic_refusal: SemanticRefusal | None = Field(default=None, exclude_if=lambda v: v is None)
    semantic_review: GroundedSemanticReview | FactorizedSemanticReview | SemanticReview | None = (
        Field(default=None, exclude_if=lambda v: v is None)
    )
    semantic_review_selection: ReviewSelection | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    planning_graph: PlanningGraph | None = Field(default=None, exclude_if=lambda v: v is None)
    identity_request: IdentityProposalRequest | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    identity_proposal: IdentityProposal | None = Field(default=None, exclude_if=lambda v: v is None)
    identity_review: IdentityReview | None = Field(default=None, exclude_if=lambda v: v is None)
    identity_observation: IdentityObservation | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    identity_history: IdentityHistory | None = Field(default=None, exclude_if=lambda v: v is None)
    human_browser: BrowserSourceEvidence | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    human_assistance: tuple[AssistanceObservation, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )
    browser_action: BrowserSourceAction | None = Field(default=None, exclude_if=lambda v: v is None)
    retained_source: GraphRetainedOrigin | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    retained_failure: GraphRetainedOrigin | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def identity_evidence(self) -> "LedgerRow":
        if (
            (self.event == "query_intent") != (self.query_reservation is not None)
            or (self.query_ack is not None and self.event not in {"query_ack", "fetch"})
            or (self.event == "query_ack" and self.query_ack is None)
        ):
            raise ValueError("query reservation/ACK belong only to their native events")
        if self.query_reservation is not None and (
            self.bytes_read
            or self.model is not None
            or self.refusal is not None
            or self.model_intent is not None
            or self.model_ack is not None
        ):
            raise ValueError("query intent cannot fabricate returned source/model evidence")
        if (self.rerank_reservation is not None) != (
            self.model_intent is not None and self.model_intent.phase == "reranking"
        ):
            raise ValueError("rerank source/quota binding belongs to its original model intent")
        if (self.event == "model_intent") != (self.model_intent is not None) or (
            self.event == "model_ack"
        ) != (self.model_ack is not None):
            raise ValueError("model operation rows require their typed invocation evidence")
        if (self.event == "model_replay") != (self.model_replay is not None):
            raise ValueError("model replay rows require their original observation reference")
        if self.model_decision is not None and self.event != "model_intent":
            raise ValueError("model decisions require an atomic separately reserved intent")
        if (self.event == "model_attempt") != (self.model_attempt is not None):
            raise ValueError("model attempt consumption requires its typed authorization")
        if self.event in {"model_intent", "model_ack", "model_replay", "model_attempt"} and (
            self.model is None
            or self.bytes_read != 0
            or self.transport is not None
            or self.model_call is not None
            or self.refusal is not None
        ):
            raise ValueError("model operation evidence is not a source read or approved result")
        if self.model_ack is not None and self.model_ack.intent_sequence >= self.sequence:
            raise ValueError("model acknowledgement must refer to an earlier intent")
        if self.model_replay is not None and not (
            self.model_replay.intent_sequence < self.model_replay.ack_sequence < self.sequence
        ):
            raise ValueError("model replay must refer to an earlier intent and acknowledgement")
        if (
            self.model_ack is not None
            and self.model_ack.refused_call is not None
            and (
                self.model is None
                or self.model_ack.refused_call.service.model_id != self.model.model_id
                or self.model_ack.refused_call.service.revision != self.model.revision
            )
        ):
            raise ValueError("a known refused completion must retain its invoked model identity")
        if self.scoring_source is not None and self.event != "scoring":
            raise ValueError("scoring source attribution belongs to its native scoring operation")
        if (self.event == "source_acquisition") != (self.source_acquisition is not None) or (
            self.source_acquisition is not None
            and (
                self.url != self.source_acquisition.source_url
                or self.refusal is not None
                or self.model_call is not None
                or self.bytes_read != 0
            )
        ):
            raise ValueError(
                "acquisition return is exact local provenance, not extra spend or a model ACK"
            )
        if (self.event == "scoring_source") != (self.scoring_reading is not None) or (
            self.scoring_reading is not None and self.url != self.scoring_reading.source_url
        ):
            raise ValueError("retained scoring reading must identify its original source")
        if (self.event == "judgment_context") != (self.judgment_context is not None):
            raise ValueError("judgment context belongs to its exact pre-invocation observation")
        if (
            self.judgment_context is not None
            and self.url != self.judgment_context.context.source_url
        ):
            raise ValueError("judgment context must identify its exact original source URL")
        if self.document_judgment is not None and (
            self.event != "verdict" or self.refusal is not None
        ):
            raise ValueError("judgment disposition requires an actual successful native verdict")
        if self.source_refresh is not None and (
            self.event != "policy"
            or self.reason != "source_refresh_revalidated"
            or self.url != self.source_refresh.source_url
            or self.bytes_read != 0
            or self.status is not None
            or self.refusal is not None
        ):
            raise ValueError("refresh reuse is a zero-transfer policy observation, not a fetch")
        if self.source_feed is not None and (
            self.event != "extraction"
            or self.url != self.source_feed.source_url
            or self.reason != "source-feed-parser/1"
            or self.extraction is not None
            or self.document_parse is not None
            or self.refusal is not None
        ):
            raise ValueError("feed parse evidence belongs to its exact extraction observation")
        if self.retained_failure is not None and (
            self.event != "refusal"
            or self.refusal is None
            or self.url is None
            or self.reason != "retained_semantics_refused"
        ):
            raise ValueError("retained processing refusal requires its exact source origin")
        if (self.event == "retained_source") != (self.retained_source is not None):
            raise ValueError("retained source observations require their current query origin")
        if self.retained_source is not None and (
            self.url is None
            or self.bytes_read != 0
            or self.status is not None
            or self.refusal is not None
            or self.route is not None
            or self.transport is not None
            or self.model_call is not None
            or self.reason != "retained_original_admitted"
        ):
            raise ValueError(
                "retained original admission is not a fetch or a historical model call"
            )
        if (self.event == "transcription") != (self.transcription_call is not None):
            raise ValueError("completed transcription calls require their exact typed evidence")
        if self.browser_action is not None and (
            self.event != "policy"
            or self.url != self.browser_action.url
            or self.route is not None
            or self.status is not None
            or self.bytes_read != 0
            or self.human_browser is not None
            or self.refusal is not None
            or self.reason != "browser_source_action_reserved"
        ):
            raise ValueError("browser action is a known budget reservation, not an HTTP result")
        if (
            self.route == "human_browser_dom"
            or self.human_browser is not None
            or self.human_assistance
        ):
            if (
                self.event != "fetch"
                or self.status is not None
                or any(
                    value is not None
                    for value in (
                        self.transport,
                        self.rendered,
                        self.source_session,
                        self.challenge_use,
                        self.local_input,
                    )
                )
            ):
                raise ValueError(
                    "browser capture observations are not HTTP/isolated-render evidence"
                )
            if self.human_browser is not None and (
                self.refusal is not None
                or self.human_assistance
                or self.url != self.human_browser.request_url
                or self.bytes_read != self.human_browser.collector_bytes_read
            ):
                raise ValueError("successful browser capture must retain its exact source spend")
            if self.human_assistance and self.refusal is None:
                raise ValueError("failed browser assistance requires its terminal refusal")
            if self.refusal is None and self.human_browser is None:
                raise ValueError("successful browser route cannot discard capture evidence")
        if self.event in {"identity_propose", "identity_review"}:
            result = (
                self.identity_proposal if self.event == "identity_propose" else self.identity_review
            )
            if self.identity_request is None or (result is None) == (self.refusal is None):
                raise ValueError(
                    "identity call requires its exact request and observed result or refusal"
                )
            if result is not None and self.model_call != result.model_call:
                raise ValueError("identity call evidence must be the original observed call")
        elif (
            self.identity_request is not None
            or self.identity_review is not None
            or self.identity_proposal is not None
        ):
            raise ValueError("identity model results belong to their native call observations")
        if (self.event == "identity_resolution") != (self.identity_observation is not None):
            raise ValueError("identity resolution requires its graph acknowledgement observation")
        if (self.event == "identity_history") != (self.identity_history is not None):
            raise ValueError("identity history requires its native graph checkpoint observation")
        if self.event == "semantic_review":
            if (self.semantic_review is None) == (self.refusal is None):
                raise ValueError("semantic review requires its assessment or refusal")
            if self.semantic_review is not None and (
                self.model_call is None
                or self.model_call != self.semantic_review.model_call
                or self.model_call.task != "semantic_review"
                or self.model_call.outcome != "success"
            ):
                raise ValueError("semantic review must bind its actual model call")
        elif self.semantic_review is not None:
            raise ValueError("semantic review belongs to its review call observation")
        if self.planning_graph is not None and self.event != "plan":
            raise ValueError("planning graph belongs to its observed planning call")
        if self.event == "semantic":
            if (self.semantic_window is None) == (self.refusal is None):
                raise ValueError("semantic calls require a source-bound projection or refusal")
            if self.semantic_window is not None and (
                self.url != self.semantic_window.source_url
                or self.model_call != self.semantic_window.proposal.model_call
            ):
                raise ValueError("semantic ledger metadata must match its window and call")
        elif self.semantic_window is not None:
            raise ValueError("semantic projections belong to their call observation")
        if (self.event == "semantic_selection") != (self.semantic_selection is not None):
            raise ValueError("semantic selection requires its native pre-contact plan event")
        if self.semantic_selection is not None and (
            self.url != self.semantic_selection.context.source_url
            or self.model_call is not None
            or self.model is not None
            or self.refusal is not None
            or self.model_intent is not None
            or self.model_ack is not None
            or self.semantic_window is not None
            or self.semantic_refusal is not None
        ):
            raise ValueError(
                "native semantic selection claims no model result or graph acknowledgement"
            )
        if self.scoring_source is not None and self.event != "scoring":
            raise ValueError("native scoring source binding belongs to its scoring observation")
        if (self.event == "scoring_source") != (self.scoring_reading is not None) or (
            self.scoring_reading is not None and self.url != self.scoring_reading.source_url
        ):
            raise ValueError("native scorer reading requires its exact pre-contact source event")
        if self.semantic_refusal is not None and (
            self.event != "semantic"
            or self.refusal is None
            or self.semantic_window is not None
            or self.url != self.semantic_refusal.source_url
            or (
                self.semantic_refusal.proposal is not None
                and self.model_call != self.semantic_refusal.proposal.model_call
            )
        ):
            raise ValueError("semantic refusal must bind its failed window and original extractor")
        if self.event == "local_input":
            if (self.local_input is None) == (self.refusal is None):
                raise ValueError("local import needs snapshot evidence or a refusal")
            if self.status is not None or self.transport is not None:
                raise ValueError("local imports are not HTTP fetches")
            if self.local_input is not None and (
                self.url != self.local_input.source_id
                or self.bytes_read != self.local_input.size_bytes
            ):
                raise ValueError("import observations must bind the original byte snapshot")
        elif self.local_input is not None:
            raise ValueError("local import metadata belongs only to its input observation")
        if self.challenge_use is not None and self.event != "fetch":
            raise ValueError("clearance use belongs to its source fetch")
        if self.event == "challenge":
            if (self.challenge is None) == (self.refusal is None):
                raise ValueError("challenge attempt needs clearance evidence or refusal")
        elif self.challenge is not None:
            raise ValueError("challenge metadata belongs to its attempt")
        if self.search_response_sha256 is not None and (
            self.event != "fetch"
            or self.route is None
            or not self.route.startswith("search:")
            or self.refusal is not None
            or self.query is None
        ):
            raise ValueError("search response digest requires a successful search fetch")
        if (self.event == "intent_reference") != (self.intent_reference is not None):
            raise ValueError(
                "intent reference events require their prepared vectors and call binding"
            )
        if self.intent_reference is not None:
            references = self.intent_reference.references
            if (
                self.url is not None
                or self.refusal is not None
                or self.model is None
                or self.model.location != "self_hosted"
                or (self.model.model_id, self.model.revision)
                != (references.model_id, references.revision)
            ):
                raise ValueError("intent reference metadata must identify its self-hosted encoder")
        if (self.event == "extraction_attempt") != (self.extraction_attempt is not None):
            raise ValueError("parse attempt events require their bounded source observation")
        if self.extraction_attempt is not None and (
            self.url != self.extraction_attempt.source_url
            or self.refusal != self.extraction_attempt.refusal
            or self.latency_seconds != self.extraction_attempt.latency_seconds
        ):
            raise ValueError("parse attempt ledger metadata must match its observation")
        if (self.event == "reference_query") != (self.reference_query is not None):
            raise ValueError("reference query events require their explicit source binding")
        if self.reference_query is not None and (
            self.url != self.reference_query.source.url or self.query != self.reference_query.query
        ):
            raise ValueError("reference query must match its ledger source and query")
        if (self.event == "reference") != (self.reference is not None):
            raise ValueError("reference events require their explicit native source decision")
        if self.reference is not None and self.url != self.reference.reference.target_url:
            raise ValueError("reference event URL must match its target")
        if (self.event == "encoding") != (self.encoding_call is not None):
            raise ValueError("encoding events require their explicit call evidence")
        if (self.event == "run_encoding_intent") != (self.run_encoding_intent is not None):
            raise ValueError("run encoding intents require their original typed reservation")
        if (self.event == "run_encoding_replay") != (self.run_encoding_replay is not None):
            raise ValueError("run encoding replay requires original intent and ACK identity")
        if self.event in {"run_encoding_intent", "run_encoding_replay"} and (
            self.bytes_read != 0
            or self.status is not None
            or self.refusal is not None
            or self.model_call is not None
            or self.model_intent is not None
            or self.model_ack is not None
            or self.model_replay is not None
            or self.model is None
        ):
            raise ValueError("run encoding intent/replay is not HTTP, graph or judge evidence")
        if self.run_encoding_sequence is not None and self.event != "encoding":
            raise ValueError("run encoding result links belong to original encoding observations")
        if self.run_encoding_ack is not None and (
            self.event != "encoding"
            or self.refusal is not None
            or self.encoding_call != self.run_encoding_ack.result.call
            or self.run_encoding_sequence != self.run_encoding_ack.original_intent_sequence
        ):
            raise ValueError("retained run vectors require their successful original encoding call")
        if (self.event == "scoring") != (self.similarity is not None):
            raise ValueError("scoring events require their native similarity evidence")
        if self.encoding_call is not None:
            service = self.encoding_call.service
            if self.model is None or (self.model.model_id, self.model.revision) != (
                service.model_id,
                service.revision,
            ):
                raise ValueError("encoding event identity must match its call evidence")
            if (self.encoding_call.outcome == "success") != (self.refusal is None):
                raise ValueError("encoding refusal must match its call outcome")
        if self.event == "render":
            if (self.rendered is None) == (self.refusal is None):
                raise ValueError("render event requires either its result or a refusal")
            if self.rendered is not None and self.url != self.rendered.source_url:
                raise ValueError("render event must identify its source occurrence")
        elif self.rendered is not None:
            raise ValueError("render evidence belongs to its render event")
        if self.dedup is not None and self.event != "duplicate":
            raise ValueError("dedup evidence belongs only to a duplicate observation")
        if (self.content_drift is not None) != (self.event == "content_drift"):
            raise ValueError("content drift events require their explicit revision evidence")
        return self


def count_fetch_attempts(config: GhimeraConfig, rows: tuple[LedgerRow, ...]) -> int:
    """Guarded capture is an aggregate observation; its known actions own spend."""
    browser = config.human_browser
    guarded = browser is not None and browser.navigation is not None
    return sum(
        row.browser_action is not None
        or row.event == "challenge"
        or row.query_reservation is not None
        and row.query_reservation.fetch_reservation is not None
        or (
            row.event == "fetch"
            and row.query_ack is None
            and not (guarded and row.route == "human_browser_dom")
        )
        for row in rows
    )


class Receipt(Record):
    fetches: NonNegative
    bytes_read: NonNegative
    judge_calls: NonNegative
    encoding_calls: NonNegative = 0
    encoding_chars: NonNegative = 0
    accepted_documents: NonNegative
    elapsed_seconds: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    stop_reason: StopReason
    effective_config: GhimeraConfig
    judge: ModelIdentity


class Harvest(Record):
    schema_version: Literal["chimera.harvest/1", "chimera.harvest/2"] = Field(alias="schema")
    goal: Goal
    documents: tuple[Document, ...]
    ledger: tuple[LedgerRow, ...]
    receipt: Receipt
    graph: GraphSnapshot | None = None
    retained_sources: tuple[RetainedOriginal, ...] = Field(default=(), exclude_if=lambda v: not v)

    @property
    def source_documents(self) -> tuple[Document, ...]:
        return tuple(item for doc in self.documents for item in doc.evidence_sources())

    @property
    def graph_source_documents(self) -> tuple[Document, ...]:
        return self.source_documents + tuple(item.document for item in self.retained_sources)

    @model_validator(mode="after")
    def consistent(self) -> "Harvest":
        from ghimera.retained_graph import validate_harvest as validate_retained_harvest

        validate_retained_harvest(self)
        from ghimera.human_browser_validation import validate_harvest as validate_browser_harvest
        from ghimera.references import validate_reference_ledger
        from ghimera.scoring_validation import validate_reference_rows

        validate_reference_ledger(self)
        validate_browser_harvest(self)
        feed_policy = self.receipt.effective_config.source_feeds
        feeds = tuple(row.source_feed for row in self.ledger if row.source_feed is not None)
        if any(feed.policy != feed_policy for feed in feeds):
            raise ValueError("feed ledger evidence must bind the effective run configuration")
        if any(
            doc.extracted.source_feed is not None and doc.extracted.source_feed not in feeds
            for doc in self.source_documents
        ):
            raise ValueError("fresh feed documents must preserve their parse observation")
        inputs = tuple(row for row in self.ledger if row.event == "local_input")
        input_policy = self.receipt.effective_config.local_inputs
        if inputs and (
            input_policy is None
            or len(inputs) > input_policy.max_files_per_run
            or sum(row.bytes_read for row in inputs) > input_policy.max_total_bytes
        ):
            raise ValueError("local imports exceed the effective input policy")
        for row in inputs:
            if row.local_input is not None:
                row.local_input.validate_policy(input_policy)
        for document in self.source_documents:
            if document.source_refresh is not None and not any(
                row.source_refresh == document.source_refresh for row in self.ledger
            ):
                raise ValueError("refreshed documents require their revalidation observation")
            transcription = document.extracted.pdf_transcription
            if transcription is not None and any(
                not any(
                    row.url == document.url and row.transcription_call == call
                    for row in self.ledger
                )
                for page in transcription.pages
                for call in page.calls
            ):
                raise ValueError("PDF reading must retain every original model-call observation")
            if document.local_input is not None and not any(
                row.local_input == document.local_input for row in inputs
            ):
                raise ValueError("local documents must retain their import observation")
        parsing: dict[tuple[str, str, str | None], list[HtmlExtractionAttempt]] = {}
        for row in self.ledger:
            if row.source_refresh is not None:
                row.source_refresh.validate_policy(
                    self.receipt.effective_config.source_refresh,
                    row.url or "",
                    row.source_refresh.source_sha256,
                )
                if not any(
                    fetch.event == "fetch"
                    and fetch.sequence < row.sequence
                    and fetch.url == row.url
                    and fetch.status == 304
                    and fetch.refusal is None
                    for fetch in self.ledger
                ):
                    raise ValueError(
                        "refresh policy observation requires an actual preceding HTTP 304"
                    )
            for clearance in (row.challenge, row.challenge_use):
                if clearance is not None:
                    if row.url is None:
                        raise ValueError("clearance observation requires its source URL")
                    clearance.validate_policy(self.receipt.effective_config.challenges, row.url)
            if row.extraction_attempt is not None:
                attempt = row.extraction_attempt
                extraction_policy = self.receipt.effective_config.extraction
                if extraction_policy is None:
                    raise ValueError("parse attempts require their effective extraction policy")
                attempt.validate_policy(extraction_policy)
                parsing.setdefault(
                    (attempt.source_url, attempt.source_sha256, attempt.rendered_sha256), []
                ).append(attempt)
            if row.extraction is not None:
                extraction_policy = self.receipt.effective_config.extraction
                if extraction_policy is None:
                    raise ValueError("extraction ledger evidence requires its effective policy")
                row.extraction.validate_policy(extraction_policy)
            if row.source_session is not None:
                if row.event != "fetch" or row.url is None:
                    raise ValueError("source session metadata belongs to a source fetch")
                row.source_session.validate_policy(
                    self.receipt.effective_config.source_sessions, row.url
                )
            if row.rendered is not None:
                for resource in row.rendered.resources:
                    if resource.source_session is not None:
                        resource.source_session.validate_policy(
                            self.receipt.effective_config.source_sessions, resource.url
                        )
        for chain in parsing.values():
            validate_chain(tuple(chain))
            if chain[-1].outcome == "success" and not any(
                row.extraction is not None and row.extraction.attempts == tuple(chain)
                for row in self.ledger
            ):
                raise ValueError("successful parsing must retain its extraction result")
        for row in self.ledger:
            if row.extraction is not None and row.extraction.attempts:
                evidence = row.extraction
                observed = parsing.get(
                    (evidence.source_url, evidence.source_sha256, evidence.rendered_sha256), []
                )
                if tuple(observed) != evidence.attempts:
                    raise ValueError(
                        "extraction results must preserve every prior attempt ledger row"
                    )
        for doc in self.source_documents:
            doc_evidence = doc.extracted.extraction
            if (
                doc_evidence is not None
                and doc_evidence.attempts
                and tuple(
                    parsing.get(
                        (
                            doc_evidence.source_url,
                            doc_evidence.source_sha256,
                            doc_evidence.rendered_sha256,
                        ),
                        [],
                    )
                )
                != doc_evidence.attempts
            ):
                raise ValueError("document parsing/recovery must reconcile with the run ledger")
        if tuple(row.sequence for row in self.ledger) != tuple(range(len(self.ledger))):
            raise ValueError("ledger sequence must be contiguous")
        validate_reference_rows(self.receipt.effective_config, self.goal.text, self.ledger)
        from ghimera.judgment_validation import validate_judgment_rows

        validate_judgment_rows(self.receipt.effective_config, self.goal.text, self.ledger)
        from ghimera.source_acquisition_types import validate_acquisition_rows

        validate_acquisition_rows(self.receipt.effective_config, self.ledger)
        if self.receipt.fetches != count_fetch_attempts(self.receipt.effective_config, self.ledger):
            raise ValueError("fetch count does not match ledger")
        challenge_rows = tuple(row for row in self.ledger if row.event == "challenge")
        challenge_policy = self.receipt.effective_config.challenges
        if challenge_rows and (
            challenge_policy is None or len(challenge_rows) > challenge_policy.max_attempts_per_run
        ):
            raise ValueError("challenge attempts exceed their configured run budget")
        if self.receipt.bytes_read != sum(row.bytes_read for row in self.ledger):
            raise ValueError("byte spend does not match ledger")
        from ghimera.model_reconciliation import validate_policy as validate_model_decisions
        from ghimera.model_work import validate_model_rows
        from ghimera.research_reranking import validate_rerank_rows

        native_model_policy = self.receipt.effective_config.model_work
        validate_model_decisions(self.receipt.effective_config, self.ledger)
        validate_rerank_rows(self.receipt.effective_config, self.ledger)
        from ghimera.query_work import validate_query_rows

        validate_query_rows(self.receipt.effective_config, self.ledger)
        observed_judge_calls = (
            validate_model_rows(
                native_model_policy, self.receipt.effective_config.judge_budget, self.ledger
            )
            if native_model_policy is not None
            else sum(
                row.event
                in {
                    "verdict",
                    "grade",
                    "plan",
                    "assessment",
                    "answer",
                    "review",
                    "semantic",
                    "semantic_review",
                    "identity_propose",
                    "identity_review",
                    "visual_model",
                    "transcription_model",
                }
                for row in self.ledger
            )
        )
        if self.receipt.judge_calls != observed_judge_calls:
            raise ValueError("judge spend does not match ledger")
        if self.receipt.accepted_documents != len(self.documents):
            raise ValueError("accepted count does not match harvest")
        encoding = tuple(row.encoding_call for row in self.ledger if row.encoding_call is not None)
        from ghimera.run_encoding import validate_run_encoding_rows

        usage = validate_run_encoding_rows(
            self.receipt.effective_config, self.ledger, goal_text=self.goal.text
        )
        if (self.receipt.encoding_calls, self.receipt.encoding_chars) != usage:
            raise ValueError("encoding spend does not match ledger")
        scoring_policy = self.receipt.effective_config.scoring
        if encoding and (
            scoring_policy is None
            or any(
                call.service not in (scoring_policy.encoder, scoring_policy.intent_encoder)
                for call in encoding
            )
        ):
            raise ValueError("encoding ledger must bind the effective service configuration")
        if scoring_policy is not None and (
            self.receipt.encoding_calls > scoring_policy.encoding_call_budget
            or self.receipt.encoding_chars > scoring_policy.encoding_char_budget
        ):
            raise ValueError("encoding spend exceeds the shared run budget")
        native_sources = {
            hashlib.sha256(document.extracted.text.encode()).hexdigest(): document.extracted
            for document in self.source_documents
        }
        for row in self.ledger:
            if row.similarity is not None:
                similarity = row.similarity
                if similarity.goal_sha256 != hashlib.sha256(self.goal.text.encode()).hexdigest():
                    raise ValueError("similarity observations must bind this run's original intent")
                if scoring_policy is None:
                    raise ValueError("similarity requires its effective scoring policy")
                for link in similarity.links:
                    expected = scoring_policy.keyword_weight * link.keyword_score + (
                        1.0 - scoring_policy.keyword_weight
                    ) * max(0.0, link.cosine)
                    if link.score != expected:
                        raise ValueError(
                            "frontier scores must reconcile to cosine and keyword policy"
                        )
                native = native_sources.get(similarity.text_sha256)
                if native is not None:
                    if len(native.text) != similarity.total_chars or any(
                        hashlib.sha256(native.text[window.start : window.end].encode()).hexdigest()
                        != window.text_sha256
                        for window in similarity.windows
                    ):
                        raise ValueError("similarity observations must bind retained native spans")
        if any(doc.verdict.decision != "accept" for doc in self.documents):
            raise ValueError("only accepted documents belong in harvest")
        if len({doc.sha256 for doc in self.documents}) != len(self.documents):
            raise ValueError("cluster representatives must have distinct raw content identities")
        for row in self.ledger:
            if row.rendered is not None:
                render_config = self.receipt.effective_config
                if render_config.browser is None:
                    raise ValueError("render ledger requires its configured policy")
                row.rendered.validate_policy(
                    render_config.browser,
                    max_redirects=render_config.http.max_redirects if render_config.http else 0,
                )
        for doc in self.documents:
            doc.validate_policy(self.receipt.effective_config)
            if doc.occurrences:
                from ghimera.content_dedup import ContentIndex

                policy = self.receipt.effective_config.dedup
                if policy is None:
                    raise ValueError("retained dedup evidence requires its effective policy")
                index = ContentIndex(policy)
                index.add(doc)
                for item in doc.occurrences:
                    if index.match(item) != item.dedup:
                        raise ValueError("duplicate similarity evidence does not match its content")
                    item.validate_policy(self.receipt.effective_config)
        if any(row.dedup is not None or row.content_drift is not None for row in self.ledger):
            from ghimera.content_dedup import ContentIndex, fingerprint

            identity_policy = self.receipt.effective_config.dedup
            if identity_policy is None:
                raise ValueError("identity ledger requires its effective configuration")
            sources = {(doc.sha256, doc.url): doc for doc in self.source_documents}
            representatives = {doc.sha256: doc for doc in self.documents}
            revisions = {
                (doc.sha256, fingerprint(doc, identity_policy).canonical_url): doc
                for doc in self.source_documents
            }
            for row in self.ledger:
                if row.dedup is not None:
                    if row.url is None:
                        raise ValueError("duplicate ledger must identify its source URL")
                    current = sources.get((row.dedup.current.source_sha256, row.url))
                    representative = representatives.get(row.dedup.representative_sha256)
                    if current is None or representative is None:
                        raise ValueError("duplicate ledger must reference retained source evidence")
                    verification = ContentIndex(identity_policy)
                    verification.add(representative)
                    if verification.match(current) != row.dedup:
                        raise ValueError("duplicate ledger similarity evidence was altered")
                if row.content_drift is not None:
                    drift = row.content_drift
                    previous = revisions.get((drift.previous_sha256, drift.canonical_url))
                    now = revisions.get((drift.current_sha256, drift.canonical_url))
                    if previous is None or now is None or row.url != now.url:
                        raise ValueError("drift ledger requires both retained source revisions")
                    verification = ContentIndex(identity_policy)
                    verification.add(previous)
                    if verification.drift(now) != drift:
                        raise ValueError("content drift evidence was altered")
        graph_enabled = (
            self.receipt.effective_config.graph is not None
            and self.receipt.effective_config.graph.enabled
        )
        if graph_enabled != (self.graph is not None):
            raise ValueError("enabled graph cannot be silently absent from harvest")
        if self.graph is not None:
            config = self.receipt.effective_config.graph
            if config is None or self.graph.config_digest != config.content_digest():
                raise ValueError("graph must bind the effective configuration")
            source_readings: dict[tuple[str, str, str], list[GraphPdfReading | None]] = {}
            source_visuals: dict[tuple[str, str, str], list[tuple[GraphVisualReading, ...]]] = {}
            from ghimera.visual_evidence import graph_visual_readings

            for source in self.graph_source_documents:
                source_reading_key = (
                    source.url,
                    source.sha256,
                    hashlib.sha256(source.extracted.text.encode()).hexdigest(),
                )
                source_readings.setdefault(source_reading_key, []).append(
                    source.extracted.pdf_transcription.graph_reading()
                    if source.extracted.pdf_transcription is not None
                    else None
                )
                source_visuals.setdefault(source_reading_key, []).append(
                    graph_visual_readings(source.images)
                )
            for node in self.graph.nodes:
                if node.source_refresh is not None and node.retained_source is None:
                    node.source_refresh.validate_policy(
                        self.receipt.effective_config.source_refresh,
                        node.source_url or "",
                        node.content_sha256 or "",
                    )
                    if not any(row.source_refresh == node.source_refresh for row in self.ledger):
                        raise ValueError("refreshed graph source must retain its HTTP observation")
                if node.role == "document":
                    node_reading_key = (
                        node.source_url or "",
                        node.content_sha256 or "",
                        node.text_sha256 or "",
                    )
                    if (
                        node_reading_key in source_readings
                        and node.pdf_reading not in source_readings[node_reading_key]
                    ):
                        raise ValueError(
                            "graph document must preserve the actual PDF reading evidence"
                        )
                    if node.visual_readings and (
                        node_reading_key not in source_visuals
                        or node.visual_readings not in source_visuals[node_reading_key]
                    ):
                        raise ValueError("graph visual readings must bind retained source images")
                    if (
                        node_reading_key in source_visuals
                        and node.visual_readings not in source_visuals[node_reading_key]
                    ):
                        raise ValueError("graph document must preserve its visual readings")
                if node.local_input is not None and node.retained_source is None:
                    node.local_input.validate_policy(input_policy)
                    if not any(row.local_input == node.local_input for row in inputs):
                        raise ValueError(
                            "local graph documents must retain their input observation"
                        )
        from ghimera.graph_planning import validate_rows as validate_planning_rows
        from ghimera.semantic_graph import validate_harvest

        try:
            validate_harvest(self)
            validate_planning_rows(self.receipt.effective_config, self.ledger)
            from ghimera.identity_automation import validate_identity_rows

            validate_identity_rows(self.receipt.effective_config, self.ledger, self.graph)
        except GhimeraRefused:
            raise ValueError("semantic source projection cannot be revalidated") from None
        return self
