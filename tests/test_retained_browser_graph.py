"""A real closed browser capture remains usable without new browser/source work."""

import asyncio
import hashlib

from ghimera import CorpusEvidenceReader, GhimeraConfig
from ghimera.doubles import FakeJudge
from ghimera.human_browser import ChromiumHumanSession
from ghimera.models import Document, Extracted, Goal, Harvest, LedgerRow, Receipt, Verdict
from ghimera.research_types import ResearchRequest, ResearchResult
from tests.test_c0 import config
from tests.test_corpus_evidence import policy as reader_policy
from tests.test_evidence_corpus import config as corpus_config
from tests.test_evidence_corpus import corpus, endpoint
from tests.test_human_browser import interactive_source, with_browser
from tests.test_research_reuse import assemble
from tests.test_retained_graph import configured

__all__ = ["endpoint"]


def test_retained_browser_graph_needs_no_live_browser_or_new_capture(tmp_path, endpoint):
    historical = []
    with interactive_source() as origin:

        async def capture(selected, page, unrelated):
            class NoAssistanceNeeded:
                async def assist(self, request):
                    raise AssertionError("challenge-free control must not request human help")

            current = await ChromiumHumanSession(selected, assistant=NoAssistanceNeeded()).capture(
                origin + "/research/tamper"
            )
            old_config = config(human_browser=selected)
            doc = Document(
                url=current.evidence.final_url,
                raw=current.dom,
                sha256=hashlib.sha256(current.dom).hexdigest(),
                human_browser=current.evidence,
                extracted=Extracted(
                    title="controlled native DOM",
                    text="Native evidence from the actual DOM.",
                    language="en",
                ),
                verdict=Verdict(
                    decision="accept",
                    kind="report",
                    publisher="unknown",
                    language="en",
                    reason="protocol fixture",
                ),
            )
            historical.append(
                Harvest(
                    schema="chimera.harvest/1",
                    goal=Goal(text="read native DOM"),
                    documents=(doc,),
                    graph=None,
                    ledger=(
                        LedgerRow(
                            sequence=0,
                            event="fetch",
                            route="human_browser_dom",
                            url=doc.url,
                            bytes_read=current.evidence.collector_dom_bytes_read,
                            human_browser=current.evidence,
                            reason="controlled_browser_capture",
                        ),
                    ),
                    receipt=Receipt(
                        fetches=1,
                        bytes_read=current.evidence.collector_dom_bytes_read,
                        judge_calls=0,
                        accepted_documents=1,
                        elapsed_seconds=0,
                        stop_reason="frontier_empty",
                        effective_config=old_config,
                        judge=FakeJudge().model,
                    ),
                )
            )

        asyncio.run(with_browser(tmp_path / "old-browser", origin, capture))

    async def research():
        # Both the source server and its browser are now closed.
        store = corpus(corpus_config(tmp_path, endpoint[0]), create=True)
        try:
            await store.append(historical[0])
            reader = CorpusEvidenceReader(reader_policy(store), store)
            raw = configured(tmp_path, reader).model_dump()
            raw["semantics"] = None
            raw["research"]["graph_context"] = None
            cfg = GhimeraConfig.model_validate(raw)
            loop, route, search = assemble(cfg, reader)
            result = await loop.run(ResearchRequest(intent="unrelated"), run_id="old-browser")
            assert result.status == "answered" and not route.requests and not search.requests
            assert cfg.human_browser is None
            assert not any(row.human_browser for row in result.harvest.ledger)
            (node,) = (node for node in result.harvest.graph.nodes if node.role == "document")
            assert node.human_browser == historical[0].documents[0].human_browser
            assert node.retained_source is not None
            assert result.harvest.receipt.fetches == result.harvest.receipt.bytes_read == 0
            assert ResearchResult.model_validate_json(result.model_dump_json()) == result
        finally:
            store.close()

    asyncio.run(research())
