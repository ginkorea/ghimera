"""Closed refusal vocabulary shared by every package boundary."""

import asyncio
from enum import StrEnum
from types import MappingProxyType


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
    TOR_REQUIRED = "tor_required"
    TOR_UNAVAILABLE = "tor_unavailable"
    INVALID_ONION_ADDRESS = "invalid_onion_address"


REFUSALS = MappingProxyType(
    {
        RefusalCode.ROBOTS_DISALLOWED: "Robots denies this URL; use another seed or shelf ruling.",
        RefusalCode.CHALLENGE_NOT_SOLVED: "Site challenge encountered; never solve or bypass it.",
        RefusalCode.LOGIN_WALL: "Login required; collect a public alternative.",
        RefusalCode.PAYWALL: "Paywall encountered; collect a public alternative.",
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
        RefusalCode.TOR_REQUIRED: (
            "This source requires configured Tor routing; never try it directly."
        ),
        RefusalCode.TOR_UNAVAILABLE: (
            "The required Tor path failed; inspect Tor without a direct retry."
        ),
        RefusalCode.INVALID_ONION_ADDRESS: (
            "Onion address encoding, version or checksum is invalid."
        ),
    }
)


class ChimeraRefused(Exception):
    def __init__(self, code: RefusalCode) -> None:
        self.code = code
        super().__init__(f"{code.value}: {REFUSALS[code]}")


class FetchFailure(ChimeraRefused):
    """Decoded bytes retained before an interrupted or oversized transfer."""

    def __init__(self, code: RefusalCode, bytes_read: int) -> None:
        self.bytes_read = bytes_read
        super().__init__(code)


class FetchCancelled(asyncio.CancelledError):
    """Preserve partial body spend without turning cancellation into a retry."""

    def __init__(self, bytes_read: int) -> None:
        self.bytes_read = bytes_read
        super().__init__()


class HttpStatusRefused(ChimeraRefused):
    """A final HTTP response, not a retryable transport failure."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(RefusalCode.FETCH_FAILED)
