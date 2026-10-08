"""Research accounts a native cached encoding as an original observation."""

import asyncio

from ghimera.corpus_evidence import CorpusEvidenceReader
from ghimera.research_reuse import ResearchRetrievalReport, RetainedResearchSession
from ghimera.research_types import ResearchRequest
from tests.test_corpus_evidence import policy as reader_policy
from tests.test_encoding_recovery import open_corpus, recovery_policy
from tests.test_evidence_corpus import harvest
from tests.test_research_reuse import assemble, configured, reuse_policy


def test_retained_query_ack_is_reconciled_without_new_contact_or_reset(tmp_path):
    async def operation():
        store = open_corpus(recovery_policy(tmp_path), create=True)
        try:
            material = await harvest(("zh", "港口基礎設施研究"))
            await store.append(material)
            reader = CorpusEvidenceReader(reader_policy(store), store)
            policy = reuse_policy(reader)
            first = RetainedResearchSession(policy, reader, "find ports")
            await first.query("find ports", remaining_seconds=30)
            original = first.report
            calls = store.encoding_recovery_state().reserved_calls
            second = RetainedResearchSession(policy, reader, "find ports")
            await second.query("find ports", remaining_seconds=30)
            reused = second.report
            assert reused.documents == original.documents == material.source_documents
            assert len(reused.observations) == 1  # Still charges run-local query allowance.
            old, recovered = original.observations[0], reused.observations[0]
            assert recovered.encoding_call == old.encoding_call
            assert recovered.encoding_recovery.reused
            assert not old.encoding_recovery.reused
            assert (
                recovered.encoding_recovery.original_call_id
                == old.encoding_recovery.original_call_id
            )
            assert (
                recovered.encoding_recovery.invocation_sha256
                == old.encoding_recovery.invocation_sha256
            )
            assert store.encoding_recovery_state().reserved_calls == calls
            assert ResearchRetrievalReport.model_validate_json(reused.model_dump_json()) == reused
            restored = RetainedResearchSession(policy, reader, "find ports", restored=reused)
            await restored.query("find ports", remaining_seconds=30)
            assert restored.report == reused
            loop, route, search = assemble(configured(reader), reader)
            result = await loop.run(ResearchRequest(intent="find ports"))
            assert result.status == "answered"
            assert not route.requests and not search.requests
            assert result.retrieval.observations[0].encoding_recovery.reused
            assert result.retrieval.observations[0].encoding_call == old.encoding_call
        finally:
            store.close()

    asyncio.run(operation())
