"""Caller-selected browser policy and DOM evidence, never fabricated HTTP bytes."""

import hashlib
import ipaddress
import re
from typing import Annotated, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ghimera.document_media import DocumentMime
from ghimera.source_session_types import origin_key, path_matches, safe_path

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
PositiveSeconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Barrier = Literal["challenge_not_solved", "login_wall", "paywall"]


class BrowserOrigin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    origin: str
    path_prefixes: Annotated[tuple[str, ...], Field(min_length=1)]
    allow_http: bool

    @model_validator(mode="after")
    def exact_scope(self) -> "BrowserOrigin":
        key, parts = origin_key(self.origin), urlsplit(self.origin)
        if key is None or parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError("browser scope requires an exact HTTP(S) origin")
        if key[0] == "http" and not self.allow_http:
            raise ValueError("plain HTTP browser scope requires explicit allow_http")
        if len(set(self.path_prefixes)) != len(self.path_prefixes) or any(
            not path.startswith("/")
            or "?" in path
            or "#" in path
            or "%" in path
            or safe_path(self.origin.rstrip("/") + path) != path
            for path in self.path_prefixes
        ):
            raise ValueError("browser scope requires unique unambiguous path prefixes")
        return self

    def permits(self, url: str) -> bool:
        path = safe_path(url)
        return (
            origin_key(url) == origin_key(self.origin)
            and path is not None
            and any(path_matches(path, prefix) for prefix in self.path_prefixes)
        )


class BrowserDownloadAction(BaseModel):
    """An exact source/action mapping, not permission to click arbitrary links."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    source_url: str
    navigation_url: str
    selector: Annotated[str, Field(min_length=1)] | None
    content_type: DocumentMime

    @model_validator(mode="after")
    def action_binding(self) -> "BrowserDownloadAction":
        if self.selector is None and self.navigation_url != self.source_url:
            raise ValueError("navigation downloads navigate to their exact source")
        if any(
            origin_key(url) is None or safe_path(url) is None
            for url in (self.source_url, self.navigation_url)
        ):
            raise ValueError("download actions require explicit HTTP(S) source URLs")
        return self


class BrowserDownloadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.browser-downloads/1"] = Field(alias="schema")
    adapter_revision: Literal["ghimera-patchright-download-stream/1"]
    max_file_bytes: Annotated[int, Field(strict=True, gt=0)]
    read_chunk_bytes: Annotated[int, Field(strict=True, gt=0)]
    actions: Annotated[tuple[BrowserDownloadAction, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_actions(self) -> "BrowserDownloadConfig":
        if len({action.source_url for action in self.actions}) != len(self.actions):
            raise ValueError("each browser download source has one action owner")
        return self


class HumanBrowserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.human-browser/1"] = Field(alias="schema")
    adapter: Literal["patchright_cdp", "patchright_page"]
    adapter_revision: Literal["ghimera-human-chromium/1"]
    driver_version: Literal["1.63.0"]
    session_id: Identifier
    control_endpoint: str | None = Field(default=None, repr=False, exclude_if=lambda v: v is None)
    target_id: Identifier
    lifecycle: Literal["caller_managed"]
    network_boundary: Literal["operator_managed_browser"]
    declared_route: Literal["direct", "tor"]
    origins: Annotated[tuple[BrowserOrigin, ...], Field(min_length=1)]
    assistance_reasons: tuple[Barrier, ...]
    max_assistance_attempts: Annotated[int, Field(strict=True, ge=0)]
    timeout_seconds: PositiveSeconds
    assistance_timeout_seconds: PositiveSeconds
    cleanup_timeout_seconds: PositiveSeconds
    max_dom_bytes: Annotated[int, Field(strict=True, gt=0)]
    downloads: BrowserDownloadConfig | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def explicit_binding(self) -> "HumanBrowserConfig":
        if self.adapter == "patchright_page":
            if self.control_endpoint is not None:
                raise ValueError("page binding takes a page port, not a debugger endpoint")
        elif self.control_endpoint is None:
            raise ValueError("CDP attachment requires its explicit endpoint")
        parts = urlsplit(self.control_endpoint or "")
        try:
            local = ipaddress.ip_address(parts.hostname or "").is_loopback
            port = parts.port
        except ValueError:
            local, port = False, None
        if self.adapter == "patchright_cdp" and (
            parts.scheme not in {"ws", "wss"}
            or not local
            or port is None
            or not 1 <= port <= 65535
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or not re.fullmatch(r"/devtools/browser/[A-Za-z0-9_-]+", parts.path)
            or any(ord(char) < 33 for char in self.control_endpoint or "")
        ):
            raise ValueError("browser attachment needs an explicit loopback WebSocket endpoint")
        if len({origin_key(item.origin) for item in self.origins}) != len(self.origins):
            raise ValueError("each eligible browser origin has one path-policy owner")
        if self.declared_route == "direct" and any(
            (urlsplit(item.origin).hostname or "").endswith(".onion") for item in self.origins
        ):
            raise ValueError("onion browser sources require Tor, never direct routing")
        if len(set(self.assistance_reasons)) != len(self.assistance_reasons):
            raise ValueError("browser assistance reasons must be unique")
        if bool(self.assistance_reasons) != bool(self.max_assistance_attempts):
            raise ValueError("assistance reasons and attempts must be enabled together")
        if self.downloads is not None and any(
            not self.permits(url)
            for action in self.downloads.actions
            for url in (action.source_url, action.navigation_url)
        ):
            raise ValueError("download actions must stay within the selected browser scope")
        if self.downloads is not None and self.adapter != "patchright_page":
            raise ValueError("downloads require the caller-bound page connection")
        return self

    def permits(self, url: str) -> bool:
        return any(item.permits(url) for item in self.origins)

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class BrowserAssistanceRequest(BaseModel):
    """A bounded request to a human; its completion cannot grant new scope."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.browser-assistance/1"] = Field(alias="schema")
    capture_id: Identifier
    attempt: Annotated[int, Field(strict=True, gt=0)]
    request_url: str
    final_url: str
    session_id: Identifier
    target_id: Identifier
    policy_digest: Digest
    reason: Barrier
    observed_dom_sha256: Digest
    observed_dom_bytes: Annotated[int, Field(strict=True, gt=0)]
    deadline_unix_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)]

    def content_digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class AssistanceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_digest: Digest
    action: Literal["resume", "decline", "timeout"]


class AssistanceObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request: BrowserAssistanceRequest
    action: Literal[
        "resume", "decline", "timeout", "cancelled", "invalid_decision", "assistance_failed"
    ]


class HumanAssistant(Protocol):
    async def assist(self, request: BrowserAssistanceRequest) -> AssistanceDecision: ...


class CaptureScope(Protocol):
    def permits(self, url: str) -> bool: ...


class HumanBrowserEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.human-browser-evidence/1"] = Field(alias="schema")
    acquisition: Literal["browser_dom"]
    capture_id: Identifier
    request_url: str
    final_url: str
    session_id: Identifier
    target_id: Identifier
    policy_digest: Digest
    adapter_revision: Literal["ghimera-human-chromium/1"]
    driver_version: Literal["1.63.0"]
    browser_version: Annotated[str, Field(min_length=1)]
    lifecycle: Literal["caller_managed"]
    network_boundary: Literal["operator_managed_browser"]
    declared_route: Literal["direct"]
    route_verification: Literal["operator_declaration_only"]
    browser_subresource_bytes: None
    browser_subresource_requests: None
    content_type: Literal["text/html", "application/xhtml+xml"]
    dom_sha256: Digest
    dom_bytes: Annotated[int, Field(strict=True, gt=0)]
    collector_dom_bytes_read: Annotated[int, Field(strict=True, gt=0)]
    assistance: tuple[AssistanceObservation, ...]

    @property
    def captured_sha256(self) -> str:
        return self.dom_sha256

    @property
    def collector_bytes_read(self) -> int:
        return self.collector_dom_bytes_read

    @model_validator(mode="after")
    def assistance_binding(self) -> "HumanBrowserEvidence":
        if self.collector_dom_bytes_read != self.dom_bytes + sum(
            item.request.observed_dom_bytes for item in self.assistance
        ):
            raise ValueError("DOM spend includes every earlier assisted interstitial")
        for attempt, observation in enumerate(self.assistance, start=1):
            request = observation.request
            if (
                observation.action != "resume"
                or request.attempt != attempt
                or request.capture_id != self.capture_id
                or request.request_url != self.request_url
                or request.session_id != self.session_id
                or request.target_id != self.target_id
                or request.policy_digest != self.policy_digest
            ):
                raise ValueError("successful DOM evidence requires its exact resumed assistance")
        return self

    def validate_policy(self, policy: HumanBrowserConfig | None) -> None:
        checked = HumanBrowserEvidence.model_validate(self.model_dump())
        if policy is None or (
            checked.policy_digest != policy.content_digest()
            or checked.session_id != policy.session_id
            or checked.target_id != policy.target_id
            or checked.adapter_revision != policy.adapter_revision
            or checked.driver_version != policy.driver_version
            or checked.declared_route != policy.declared_route
            or not policy.permits(checked.request_url)
            or not policy.permits(checked.final_url)
            or checked.dom_bytes > policy.max_dom_bytes
            or len(checked.assistance) > policy.max_assistance_attempts
            or any(
                item.request.reason not in policy.assistance_reasons
                or not policy.permits(item.request.final_url)
                or item.request.observed_dom_bytes > policy.max_dom_bytes
                for item in checked.assistance
            )
        ):
            raise ValueError("browser evidence must bind its effective policy and scope")


class BrowserCapture(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, ser_json_bytes="base64", val_json_bytes="base64"
    )
    dom: bytes
    evidence: HumanBrowserEvidence

    @model_validator(mode="after")
    def content_binding(self) -> "BrowserCapture":
        self._check_content()
        return self

    def _check_content(self) -> None:
        if (
            len(self.dom) != self.evidence.dom_bytes
            or hashlib.sha256(self.dom).hexdigest() != self.evidence.dom_sha256
        ):
            raise ValueError("browser DOM must match its captured digest and byte count")
        self.dom.decode("utf-8", errors="strict")

    def validate_policy(self, policy: HumanBrowserConfig) -> None:
        # Replay does not trust model_copy or an already constructed nested model.
        checked = BrowserCapture.model_validate(self.model_dump())
        checked.evidence.validate_policy(policy)


class BrowserDownloadEvidence(BaseModel):
    """Browser-observed file bytes; HTTP status/headers are deliberately absent."""

    model_config = ConfigDict(extra="forbid", frozen=True, serialize_by_alias=True)
    schema_version: Literal["ghimera.browser-download-evidence/1"] = Field(alias="schema")
    acquisition: Literal["browser_download"]
    capture_id: Identifier
    request_url: str
    final_url: str
    navigation_url: str
    initiator_url: str | None
    session_id: Identifier
    target_id: Identifier
    policy_digest: Digest
    adapter_revision: Literal["ghimera-human-chromium/1"]
    download_adapter_revision: Literal["ghimera-patchright-download-stream/1"]
    driver_version: Literal["1.63.0"]
    browser_version: Annotated[str, Field(min_length=1)]
    lifecycle: Literal["caller_managed"]
    network_boundary: Literal["operator_managed_browser"]
    declared_route: Literal["direct"]
    route_verification: Literal["operator_declaration_only"]
    browser_subresource_bytes: None
    browser_subresource_requests: None
    browser_download_bytes: None
    content_type: DocumentMime
    media_observation: Literal["pdf_header_at_start", "docx_archive_members"]
    file_sha256: Digest
    file_bytes: Annotated[int, Field(strict=True, gt=0)]
    collector_file_bytes_read: Annotated[int, Field(strict=True, gt=0)]
    collector_dom_bytes_read: Annotated[int, Field(strict=True, ge=0)]
    assistance: tuple[AssistanceObservation, ...]

    @property
    def captured_sha256(self) -> str:
        return self.file_sha256

    @property
    def collector_bytes_read(self) -> int:
        return self.collector_file_bytes_read + self.collector_dom_bytes_read

    @model_validator(mode="after")
    def file_binding(self) -> "BrowserDownloadEvidence":
        if self.collector_file_bytes_read != self.file_bytes:
            raise ValueError("successful download spend must equal its retained file")
        if (self.content_type == "application/pdf") != (
            self.media_observation == "pdf_header_at_start"
        ):
            raise ValueError("download media observation must agree with the retained format")
        if sum(item.request.observed_dom_bytes for item in self.assistance) > (
            self.collector_dom_bytes_read
        ):
            raise ValueError("download evidence cannot hide earlier assisted DOM spend")
        for attempt, item in enumerate(self.assistance, start=1):
            request = item.request
            if item.action != "resume" or (
                request.attempt != attempt
                or request.capture_id != self.capture_id
                or request.request_url != self.request_url
                or request.session_id != self.session_id
                or request.target_id != self.target_id
                or request.policy_digest != self.policy_digest
            ):
                raise ValueError("download assistance must bind this exact resumed capture")
        return self

    def validate_policy(self, policy: HumanBrowserConfig | None) -> None:
        checked = BrowserDownloadEvidence.model_validate(self.model_dump())
        if policy is None or policy.downloads is None:
            raise ValueError("browser downloads require an explicit download policy")
        action = next(
            (item for item in policy.downloads.actions if item.source_url == checked.request_url),
            None,
        )
        if action is None or (
            checked.policy_digest != policy.content_digest()
            or checked.session_id != policy.session_id
            or checked.target_id != policy.target_id
            or checked.adapter_revision != policy.adapter_revision
            or checked.download_adapter_revision != policy.downloads.adapter_revision
            or checked.driver_version != policy.driver_version
            or checked.declared_route != policy.declared_route
            or checked.navigation_url != action.navigation_url
            or checked.final_url != action.source_url
            or checked.content_type != action.content_type
            or not policy.permits(checked.final_url)
            or (action.selector is None) != (checked.initiator_url is None)
            or (checked.initiator_url is not None and not policy.permits(checked.initiator_url))
            or checked.file_bytes > policy.downloads.max_file_bytes
            or checked.collector_dom_bytes_read
            > policy.max_dom_bytes * (policy.max_assistance_attempts + 1)
            or len(checked.assistance) > policy.max_assistance_attempts
            or any(
                item.request.reason not in policy.assistance_reasons
                or not policy.permits(item.request.final_url)
                or item.request.observed_dom_bytes > policy.max_dom_bytes
                for item in checked.assistance
            )
        ):
            raise ValueError("download evidence must bind its effective action and limits")


BrowserSourceEvidence = Annotated[
    HumanBrowserEvidence | BrowserDownloadEvidence, Field(discriminator="acquisition")
]


def validate_browser_body(body: bytes, evidence: BrowserSourceEvidence) -> None:
    if isinstance(evidence, HumanBrowserEvidence):
        BrowserCapture(dom=body, evidence=evidence)
        return
    from ghimera.browser_download_stream import observe_document_media
    from ghimera.refusals import GhimeraRefused

    try:
        observed = observe_document_media(body, evidence.content_type)
    except GhimeraRefused:
        raise ValueError("browser download has no matching document media observation") from None
    if (
        len(body) != evidence.file_bytes
        or hashlib.sha256(body).hexdigest() != evidence.file_sha256
        or observed != evidence.media_observation
    ):
        raise ValueError("browser download must match its retained file bytes and format")


class BrowserDownloadCapture(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, ser_json_bytes="base64", val_json_bytes="base64"
    )
    body: bytes
    evidence: BrowserDownloadEvidence

    @model_validator(mode="after")
    def content_binding(self) -> "BrowserDownloadCapture":
        validate_browser_body(self.body, self.evidence)
        return self

    def validate_policy(self, policy: HumanBrowserConfig) -> None:
        checked = BrowserDownloadCapture.model_validate(self.model_dump())
        checked.evidence.validate_policy(policy)


class AuthorizedBrowserSession(Protocol):
    """Each capture detaches its own driver; it never closes a borrowed browser."""

    def validate_config(self, config: HumanBrowserConfig) -> None: ...

    async def capture(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
        timeout_seconds: float | None = None,
        scope: CaptureScope | None = None,
    ) -> BrowserCapture | BrowserDownloadCapture: ...
