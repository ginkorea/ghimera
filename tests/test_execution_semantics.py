"""Concurrent semantic recovery never adopts another source's reviewer rows."""

import asyncio
import time

from ghimera.budget import RunBudget
from ghimera.graph import MemoryGraphSink, ResearchGraph
from ghimera.ledger import Ledger
from ghimera.model_client import SelfHostedModel
from ghimera.semantic_graph import SemanticStage, validate_rows
from tests.test_semantic_graph import SemanticWire, document
from tests.test_semantic_recovery import recovery_config
from tests.test_semantic_verification import ReviewWire


def test_interleaved_failed_reviews_keep_source_local_gap_binding(tmp_path):
    cfg = recovery_config(tmp_path)

    async def run():
        first_started = asyncio.Event()
        other_returned = asyncio.Event()

        class InterleavedReview(ReviewWire):
            count = 0

            async def post(self, body):
                self.count += 1
                if self.count == 1:
                    first_started.set()
                    await other_returned.wait()
                    return await super().post(body)
                await first_started.wait()
                result = await super().post(body)
                other_returned.set()
                return result

        ledger, budget = Ledger(), RunBudget(cfg, time.monotonic)
        graph = ResearchGraph(cfg.graph, "interleaved-gaps", MemoryGraphSink())
        await graph.start("organization")
        stage = SemanticStage(
            cfg,
            SelfHostedModel(cfg, cfg.models.analyst, http=SemanticWire(cfg.models.analyst)),
            reviewer=SelfHostedModel(
                cfg,
                cfg.models.reviewer,
                http=InterleavedReview(cfg.models.reviewer, defect="digest"),
            ),
        )
        sources = (document(url="https://example.org/a"), document(url="https://example.org/b"))
        identities = [
            await graph.document(doc.url, doc.raw, doc.extracted.text, "extractor@1")
            for doc in sources
        ]
        await asyncio.wait_for(
            asyncio.gather(
                *(
                    stage.extract("organization", doc, identity, graph, budget, ledger)
                    for doc, identity in zip(sources, identities, strict=True)
                )
            ),
            timeout=2,
        )
        rows = ledger.snapshot()
        validate_rows(cfg, rows)
        failures = [row for row in rows if row.semantic_refusal is not None]
        assert len(failures) == 2
        for row in failures:
            assert len(row.semantic_refusal.review_sequences) == 1
            review = rows[row.semantic_refusal.review_sequences[0]]
            assert review.url == row.url
        assert budget.judge_calls == 4

    asyncio.run(run())
