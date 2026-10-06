"""Closed refusal vocabulary shared by every package boundary."""

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
    }
)


class ChimeraRefused(Exception):
    def __init__(self, code: RefusalCode) -> None:
        self.code = code
        super().__init__(f"{code.value}: {REFUSALS[code]}")
