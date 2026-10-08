"""Two fresh-process cursor branches; fixture protocol/control, not model quality."""

import json
import subprocess
import sys

from ghimera.config import GhimeraConfig
from ghimera.result_archive import ResearchResultArchive
from ghimera.source_work import SourceWorkStore
from tests.test_collector import encoder_endpoint, model_endpoint, search_endpoint, source_site
from tests.test_command_recovery import counts
from tests.test_source_completion_recovery import (
    command_invoke,
    offline_settings,
    recovering,
    settings,
    starting,
)

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def test_fresh_command_primary_cut_processes_only_unstarted_frontier_under_original_quantum(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint
):
    _, cfg = settings(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    raw = cfg.model_dump()
    raw["research"]["max_pages_per_round"] = 4
    cfg = GhimeraConfig.model_validate(raw)
    opts = starting(tmp_path, cfg)
    dead = command_invoke(tmp_path, opts, "source_ack")
    assert dead.returncode == 79, dead.stderr
    cut = SourceWorkStore.completion(cfg, opts.run_id)
    original = cut.snapshot.progress.harvest
    assert cut.snapshot.leg == "primary" and cut.snapshot.session.frontier
    assert original.receipt.fetches - cut.snapshot.starting_fetches < cut.snapshot.fetch_limit
    before = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    # The pending fixture source now has distinct native text, so its new
    # encoding can be distinguished from the completed source's retained window.
    source_site[4]["/plain"] = source_site[4]["/plain"].replace(
        b"The port authority", b"A different port agency"
    )
    original_windows = {
        text for entry in encoder_endpoint[1] for text in entry[1]["input"] if "authority" in text
    }
    assert original_windows
    done = command_invoke(tmp_path, recovering(opts, cut.sha256), "recover")
    assert done.returncode == 0 and not done.stderr, done.stderr
    after = counts(source_site, search_endpoint, model_endpoint, encoder_endpoint)
    assert before[0]["/plain"] == after[0]["/plain"] == 1
    assert after[0]["/z-next"] == 1 and before[1] == after[1]
    new_inputs = [text for entry in encoder_endpoint[1][before[3] :] for text in entry[1]["input"]]
    assert "find ports" not in new_inputs and not original_windows.intersection(new_inputs)
    result = ResearchResultArchive.read(opts.output_directory, max_bytes=opts.max_result_bytes)
    assert result.harvest.ledger[: len(cut.journal.rows)] == cut.journal.rows
    assert result.harvest.receipt.fetches == original.receipt.fetches + 2  # new robots + new source
    assert result.harvest.receipt.fetches - cut.snapshot.quantum_start == cut.snapshot.fetch_limit
    assert len(result.rounds) == cfg.research.max_rounds == 1
    assert result.harvest.documents[0].raw == original.documents[0].raw


CITED_ENTRYPOINT = """
import asyncio, json, os, sys
from pathlib import Path
from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.loop import GoalLoop
from ghimera.models import Extracted
from ghimera.research import ResearchLoop
from ghimera.research_types import ResearchRequest, SearchHit
from ghimera.source_work import SourceWorkStore
from tests.test_cited_by_expansion import CitedSearch
from tests.test_intent_research import AnalystFixture, ReviewerFixture
from tests.test_research_continuation import FollowupPlanner
cfg = GhimeraConfig.model_validate_json(Path(sys.argv[1]).read_bytes())
mode = sys.argv[2]
class Extractor(FakeExtractor):
    async def extract(self, page):
        return Extracted(title='Ports', text=page.body.decode(), language='en')
class Search(CitedSearch):
    async def request(self, request):
        response = await super().request(request)
        if 'cited by' in request.query.text:
            return response.model_copy(update={'hits': response.hits + (
                SearchHit(url='https://citing.example/z-ports',
                    title='Ports',snippet='fixture only'),)})
        return response
route, search, planner = FakeRoute(), Search(), FollowupPlanner()
collection = GoalLoop(config=cfg,fetcher=FetchLadder((route,)),extractor=Extractor(),
    scorer=KeywordScorer(),judge=FakeJudge())
loop = ResearchLoop(config=cfg,collector=collection,search=search,planner=planner,
    analyst=AnalystFixture(),reviewer=ReviewerFixture())
native_processed = SourceWorkStore.processed
def processed(self, token, result, ledger_end, **kwargs):
    native_processed(self,token,result,ledger_end,**kwargs)
    if mode == 'crash' and kwargs.get('control') is not None and result.url == 'https://citing.example/ports':
        os._exit(81)
SourceWorkStore.processed = processed
async def main():
    if mode == 'crash':
        await loop.run(ResearchRequest(intent='find ports'),run_id='cited')
    else:
        cut = SourceWorkStore.completion(cfg,'cited')
        result = await loop.recover('cited',snapshot_sha256=cut.sha256,boundary='source_completion')
        print(json.dumps({'result':result.model_dump(mode='json'),
            'sources':[request.url for request in route.requests],
            'search_calls':len(search.requests),'planner_calls':len(planner.requests)}))
asyncio.run(main())
"""


def test_fresh_cited_by_cut_preserves_own_leg_cursor_and_never_repeats_discovery(tmp_path):
    from tests.test_reference_expansion import references

    raw = offline_settings(tmp_path, pages=4).model_dump()
    raw["references"] = references(
        discover_cited_by=True, cited_by_query_budget=2, outside_scope="observed_public"
    )
    cfg = GhimeraConfig.model_validate(raw)
    path = tmp_path / "cited-config.json"
    path.write_text(cfg.model_dump_json())

    def invoke(mode):
        return subprocess.run(
            [sys.executable, "-c", CITED_ENTRYPOINT, str(path), mode],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    dead = invoke("crash")
    assert dead.returncode == 81, dead.stderr
    cut = SourceWorkStore.completion(cfg, "cited")
    state, original = cut.snapshot, cut.snapshot.progress.harvest
    assert state.leg == "cited_by" and state.session.frontier
    assert state.quantum_start < state.starting_fetches < original.receipt.fetches
    assert state.fetch_limit == cfg.research.max_pages_per_round - (
        state.starting_fetches - state.quantum_start
    )
    assert original.receipt.fetches - state.starting_fetches < state.fetch_limit
    done = invoke("recover")
    assert done.returncode == 0 and not done.stderr, done.stderr
    payload = json.loads(done.stdout)
    assert payload["sources"] == ["https://citing.example/z-ports"]
    assert payload["search_calls"] == payload["planner_calls"] == 0
    from ghimera.research_types import ResearchResult

    result = ResearchResult.model_validate(payload["result"])
    assert result.harvest.ledger[: len(cut.journal.rows)] == cut.journal.rows
    assert result.harvest.receipt.fetches == original.receipt.fetches + 1
    assert result.harvest.receipt.fetches - state.quantum_start == cfg.research.max_pages_per_round
    assert result.search_observations == state.progress.search_observations
    assert len(result.rounds) == 1
    assert result.rounds[0].discovered_urls == state.discovered_urls
    assert result.harvest.documents[: len(original.documents)] == original.documents
