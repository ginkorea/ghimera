"""Closed refusal vocabulary shared by every package boundary."""

import asyncio
import sys
from enum import StrEnum
from types import FrameType, MappingProxyType

from ghimera.embedding_types import EncodingCall
from ghimera.model_types import ModelCallEvidence


class RefusalCode(StrEnum):
    ROBOTS_DISALLOWED = "robots_disallowed"
    CHALLENGE_NOT_SOLVED = "challenge_not_solved"
    LOGIN_WALL = "login_wall"
    PAYWALL = "paywall"
    OUT_OF_SCOPE = "out_of_scope"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CONTENT_TYPE_UNWANTED = "content_type_unwanted"
    LOCATOR_DRIFT = "locator_drift"
    EXTRACTION_FAILED = "extraction_failed"
    MODEL_UNAVAILABLE = "model_unavailable"
    NO_CRAWL_EGRESS = "no_crawl_egress"
    ADAPTER_CONTRACT = "adapter_contract"
    FETCH_FAILED = "fetch_failed"
    GRAPH_CONTRACT = "graph_contract"
    GRAPH_SINK_FAILED = "graph_sink_failed"
    LEDGER_SINK_FAILED = "ledger_sink_failed"
    TOR_REQUIRED = "tor_required"
    TOR_UNAVAILABLE = "tor_unavailable"
    INVALID_ONION_ADDRESS = "invalid_onion_address"
    RESEARCH_CONTRACT = "research_contract"
    UNSUPPORTED_ANSWER = "unsupported_answer"
    SEARCH_UNAVAILABLE = "search_unavailable"
    SOURCE_SESSION_UNAVAILABLE = "source_session_unavailable"
    LOCAL_INPUT_FAILED = "local_input_failed"
    SEMANTIC_EXTRACTION_FAILED = "semantic_extraction_failed"


REFUSALS = MappingProxyType(
    {
        RefusalCode.SEMANTIC_EXTRACTION_FAILED: (
            "Semantic extraction lacks valid native mentions, citations or configured ontology."
        ),
        RefusalCode.LOCAL_INPUT_FAILED: (
            "Local seed is unreadable, outside its admission policy, changed or unpinned."
        ),
        RefusalCode.ROBOTS_DISALLOWED: "Robots denies this URL; use another seed or shelf ruling.",
        RefusalCode.CHALLENGE_NOT_SOLVED: (
            "Challenge remains unresolved; configure a local challenge gateway or supply "
            "an authorized source session. Never treat a challenge as document content."
        ),
        RefusalCode.LOGIN_WALL: (
            "Login required; supply an authorized source session or another source."
        ),
        RefusalCode.PAYWALL: "Subscription required; supply an entitled session or another source.",
        RefusalCode.OUT_OF_SCOPE: "URL exceeds the exact-host scope or depth limit.",
        RefusalCode.BUDGET_EXHAUSTED: "A declared page, byte, time, or judge budget is exhausted.",
        RefusalCode.CONTENT_TYPE_UNWANTED: "Content type is outside the shelf's allowed types.",
        RefusalCode.LOCATOR_DRIFT: "Locators drifted; inspect the profile and extraction record.",
        RefusalCode.EXTRACTION_FAILED: "Extraction failed; inspect the ledger and source bytes.",
        RefusalCode.MODEL_UNAVAILABLE: "Model refused; configure an admitted served model.",
        RefusalCode.NO_CRAWL_EGRESS: "No crawl_egress node; never use government-cloud egress.",
        RefusalCode.ADAPTER_CONTRACT: "Adapter violated its bounded result contract; inspect it.",
        RefusalCode.FETCH_FAILED: "Fetching failed; inspect route failure records.",
        RefusalCode.GRAPH_CONTRACT: (
            "Graph vocabulary, citation, version or budget refused; inspect the configured profile."
        ),
        RefusalCode.GRAPH_SINK_FAILED: (
            "Graph checkpoint was not acknowledged; preserve the journal and inspect its sink."
        ),
        RefusalCode.LEDGER_SINK_FAILED: (
            "Run journal could not be persisted or verified; "
            "preserve its files and inspect storage."
        ),
        RefusalCode.TOR_REQUIRED: (
            "This source requires configured Tor routing; never try it directly."
        ),
        RefusalCode.TOR_UNAVAILABLE: (
            "The required Tor path failed; inspect Tor without a direct retry."
        ),
        RefusalCode.INVALID_ONION_ADDRESS: (
            "Onion address encoding, version or checksum is invalid."
        ),
        RefusalCode.RESEARCH_CONTRACT: (
            "Research plan, scope or evidence violates its pinned contract."
        ),
        RefusalCode.UNSUPPORTED_ANSWER: (
            "Answer support was not established; retain unresolved questions."
        ),
        RefusalCode.SEARCH_UNAVAILABLE: (
            "Grounded search failed; never invent replacement source URLs."
        ),
        RefusalCode.SOURCE_SESSION_UNAVAILABLE: (
            "Bind the configured source-session credentials before collection; never discover them."
        ),
    }
)


MAX_DETAIL_CHARS = 400


def refusal_detail(text: str) -> str:
    """One bounded single-line sentence; callers never pass secrets or page bodies."""
    single = " ".join(text.split())
    return single if len(single) <= MAX_DETAIL_CHARS else single[: MAX_DETAIL_CHARS - 3] + "..."


def _raise_site() -> str:
    """Name the check that refused when the raising code supplied no sentence."""
    frame: FrameType | None = sys._getframe(2)
    while frame is not None and (
        frame.f_code.co_filename == __file__ or frame.f_code.co_name == "__init__"
    ):
        frame = frame.f_back
    if frame is None:
        return "refused without a recorded check"
    module = frame.f_globals.get("__name__", "unknown")
    return f"refused by {module}.{frame.f_code.co_name} line {frame.f_lineno}"


class GhimeraRefused(Exception):
    """A closed refusal code plus one ``detail`` sentence: what was checked, what was found.

    ``detail`` never carries credentials, secrets or page bodies. When the raising
    code does not supply one, the refusing module, function and line are named so
    the cause is never blank.
    """

    def __init__(self, code: RefusalCode, detail: str | None = None) -> None:
        self.code = code
        self.detail = refusal_detail(detail) if detail else _raise_site()
        # Set by model/encoder clients for a service refusal worth retrying.
        self.transient = False
        super().__init__(f"{code.value}: {REFUSALS[code]}")

    def __str__(self) -> str:
        return f"{self.code.value}: {REFUSALS[self.code]} ({self.detail})"


class BudgetExhausted(GhimeraRefused):
    """A declared RUN ceiling is met: names which ceiling, with used and limit.

    Only this subclass (or an expired run deadline) ends collection. A page's
    own bound, such as one reader's timeout, refuses that page and the round
    continues.
    """

    def __init__(
        self,
        budget: str,
        used: float,
        limit: float | None,
        detail: str | None = None,
    ) -> None:
        self.budget, self.used, self.limit = budget, used, limit
        shown = "unbounded" if limit is None else f"{limit:g}"
        super().__init__(
            RefusalCode.BUDGET_EXHAUSTED,
            f"run budget {budget} exhausted: used {used:g} of limit {shown}"
            + (f"; {detail}" if detail else ""),
        )


class FetchFailure(GhimeraRefused):
    """Decoded bytes retained before an interrupted or oversized transfer."""

    def __init__(self, code: RefusalCode, bytes_read: int, detail: str | None = None) -> None:
        self.bytes_read = bytes_read
        super().__init__(code, detail)


class ModelFailure(GhimeraRefused):
    """A failed model call still has bounded request/response spend evidence."""

    def __init__(
        self,
        code: RefusalCode,
        model_call: ModelCallEvidence,
        detail: str | None = None,
        *,
        transient: bool = False,
    ) -> None:
        self.model_call = model_call
        super().__init__(code, detail)
        # A service-side refusal worth retrying (reloading, overloaded, dropped).
        self.transient = transient


class ModelCancelled(asyncio.CancelledError):
    def __init__(self, model_call: ModelCallEvidence) -> None:
        self.model_call = model_call
        super().__init__()


class EncodingFailure(GhimeraRefused):
    def __init__(
        self,
        code: RefusalCode,
        call: EncodingCall,
        detail: str | None = None,
        *,
        transient: bool = False,
    ) -> None:
        self.call = call
        super().__init__(code, detail)
        self.transient = transient


class EncodingCancelled(asyncio.CancelledError):
    def __init__(self, call: EncodingCall) -> None:
        self.call = call
        super().__init__()


class FetchCancelled(asyncio.CancelledError):
    """Preserve partial body spend without turning cancellation into a retry."""

    def __init__(self, bytes_read: int) -> None:
        self.bytes_read = bytes_read
        super().__init__()


class HttpStatusRefused(GhimeraRefused):
    """A final HTTP response, not a retryable transport failure."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(RefusalCode.FETCH_FAILED, f"final HTTP status {status}")
