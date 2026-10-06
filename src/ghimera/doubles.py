"""Offline collaborators for C0 contracts, explicitly not production fallbacks."""

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.fetch import FetchRoute
from ghimera.ledger import Ledger
from ghimera.models import (
    Document,
    Extracted,
    FetchRequest,
    Goal,
    Grade,
    LinkCandidate,
    ModelIdentity,
    Page,
    Verdict,
)
from ghimera.refusals import GhimeraRefused, RefusalCode
from ghimera.scoring import Scorer


class FakeRoute(FetchRoute):
    name = "offline_fixture"
    needs_browser = False
    cost = 0

    def __init__(
        self,
        *,
        same_content: bool = False,
        refusal: RefusalCode | None = None,
    ) -> None:
        self.requests: list[FetchRequest] = []
        self.same_content = same_content
        self.refusal = refusal

    async def attempt(self, request: FetchRequest) -> Page:
        self.requests.append(request)
        if self.refusal is not None:
            raise GhimeraRefused(self.refusal)
        raw = b"fixture body" if self.same_content else f"body {request.url}".encode()
        return Page(
            url=request.url,
            final_url=request.url,
            status=200,
            content_type="text/html",
            body=raw[: request.max_bytes],
        )

    def escalation_reason(self, page: Page) -> str | None:
        return None


class FakeExtractor:
    def validate_config(self, config: GhimeraConfig) -> None:
        return None

    @property
    def revision(self) -> str:
        return "offline-fixture-extractor@1"

    async def extract(self, page: Page) -> Extracted:
        return Extracted(
            title="fixture",
            text=page.body.decode(),
            language="en",
            links=(
                LinkCandidate(url=page.final_url + "/next", anchor="ports"),
                LinkCandidate(url="https://evil.example/x", anchor="ports"),
            ),
        )


class FakeJudge:
    @property
    def model(self) -> ModelIdentity:
        return ModelIdentity(model_id="offline-test-judge", revision="1", location="test_double")

    def __init__(self, *, satisfied: bool = False, hold_first: bool = False) -> None:
        self.satisfied = satisfied
        self.hold_first = hold_first

    async def document(self, goal: Goal, document: Extracted, *, second_look: bool) -> Verdict:
        return Verdict(
            decision="hold" if self.hold_first and not second_look else "accept",
            kind="fixture",
            publisher="fixture",
            language=document.language,
            reason="offline test verdict, not analyst evidence",
        )

    async def grade(self, goal: Goal, documents: tuple[Document, ...]) -> Grade:
        return Grade(
            satisfied=self.satisfied,
            confidence=1.0 if self.satisfied else 0.0,
            reason="offline test grade, not analyst evidence",
        )


class FakeEncoder:
    @property
    def model(self) -> ModelIdentity:
        return ModelIdentity(model_id="offline-test-encoder", revision="1", location="test_double")

    async def encode(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0) if "ports" in text else (0.0, 1.0) for text in texts)


class KeywordScorer(Scorer):
    name = "keyword"
    cost = 0

    async def rank(
        self, goal: Goal, document: Extracted, budget: RunBudget, ledger: Ledger
    ) -> tuple[LinkCandidate, ...]:
        terms = tuple(goal.text.casefold().split())
        return tuple(
            link.model_copy(
                update={
                    "score": float(
                        any(term in (link.url + " " + link.anchor).casefold() for term in terms)
                    )
                }
            )
            for link in document.links
        )
