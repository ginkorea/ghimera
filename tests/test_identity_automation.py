"""Native Collector identity integration; controlled wires are not model accuracy."""

import asyncio
import json
from datetime import date
from time import monotonic

import pytest
from pydantic import ValidationError

from ghimera.budget import RunBudget
from ghimera.config import GhimeraConfig
from ghimera.doubles import FakeExtractor, FakeJudge, FakeRoute, KeywordScorer
from ghimera.fetch import FetchLadder
from ghimera.graph import DirectoryGraphSink, MemoryGraphSink, ResearchGraph
from ghimera.graph_planning import build_context, validate_context
from ghimera.identity_automation import validate_identity_rows
from ghimera.journal import DirectoryLedgerSink, read_journal
from ghimera.ledger import Ledger
from ghimera.loop import GoalLoop
from ghimera.model_client import SelfHostedModel
from ghimera.model_http import ModelHttpResponse
from ghimera.model_work import uncertain_model_sequences
from ghimera.models import Extracted, Goal, Harvest, LedgerRow, Scope
from ghimera.refusals import GhimeraRefused, RefusalCode
from tests.test_collector import (
    assembled,
    encoder_endpoint,
    model_endpoint,
    search_endpoint,
    source_site,
)
from tests.test_graph_planning import planning_config, with_journal

__all__ = ["encoder_endpoint", "model_endpoint", "search_endpoint", "source_site"]


def configured(tmp_path, *, roles=("entity",), **updates):
    raw = with_journal(planning_config(tmp_path), tmp_path).model_dump(by_alias=True)
    raw.update(
        model_work={
            "schema": "ghimera.model-work/1",
            "max_input_bytes": 200_000,
            "max_unanswered_calls": 1,
            "uncertain_policy": "hold",
            "results": {
                "schema": "ghimera.model-results/1",
                "max_result_bytes": 100_000,
                "max_total_result_bytes": 3_000_000,
            },
        },
        identity_automation={
            "schema": "ghimera.identity-automation/1",
            "roles": roles,
            "proposer_role": "analyst",
            "reviewer_role": "reviewer",
            "require_distinct_models": False,
            "max_mentions": 8,
            "max_pairs_per_pass": 20,
            "max_proposal_calls": 4,
            "max_review_calls": 4,
            "max_context_chars": 70_000,
            "max_evidence_chars": 5000,
            "max_reason_chars": 500,
            "as_of": None,
        },
    )
    raw["identity_automation"].update(updates)
    raw["graph"]["max_batch_bytes"] = 500_000
    raw["graph"]["identity_resolution"] = {
        "schema": "ghimera.identity-resolution/1",
        "roles": roles,
        "max_decisions": 30,
        "max_members_per_decision": 8,
        "max_evidence_per_decision": 8,
        "max_reason_chars": 500,
    }
    raw["graph"]["roles"] = list(raw["graph"]["roles"])
    for role in roles:
        if role not in {r["name"] for r in raw["graph"]["roles"]}:
            raw["graph"]["roles"].append({"name": role, "kind": "entity"})
    for relation in raw["graph"]["relations"]:
        if relation["name"] == "mentions":
            relation["target_roles"] = roles
    raw["semantics"]["entity_roles"] = roles
    raw["semantics"]["relation_rules"] = ()
    raw["research"]["graph_context"].update(
        schema="ghimera.graph-planning/5",
        max_gaps=2,
        entity_roles=roles,
        relation_rules=(),
        max_entities=8,
        max_context_chars=100_000,
        max_evidence_chars=20_000,
        resolved_as_of=raw["identity_automation"]["as_of"],
    )
    return GhimeraConfig.model_validate(raw)


def response(model, output):
    return ModelHttpResponse(
        200,
        json.dumps(
            {
                "id": "controlled-wire",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(output, ensure_ascii=False),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
            }
        ).encode(),
        "application/json",
    )


class IdentityWire:
    def __init__(
        self, service, *, verdict="supported", operation="merge", dates=(None, None), drift=False
    ):
        self.config, self.verdict, self.operation = service, verdict, operation
        self.dates, self.drift, self.requests = dates, drift, []

    async def post(self, body):
        packet = json.loads(json.loads(body)["messages"][1]["content"])
        self.requests.append(packet)
        if packet["task"] == "identity_propose":
            req = packet["identity_request"]
            mentions = {m["node_id"]: m for m in req["mentions"]}
            output = {
                "schema": "ghimera.identity-proposal/1",
                "request_digest": "a" * 64 if self.drift else packet["proposal_digest"],
                "hypotheses": [
                    {
                        "pair_id": p["id"],
                        "operation": self.operation,
                        "evidence": []
                        if self.operation == "unresolved"
                        else list(
                            {
                                json.dumps(mentions[m]["context"], sort_keys=True): mentions[m][
                                    "context"
                                ]
                                for m in p["members"]
                            }.values()
                        ),
                        "reason": "Both native contexts explicitly identify the same institution.",
                        "valid_from": self.dates[0],
                        "valid_to": self.dates[1],
                    }
                    for p in req["pairs"]
                ],
            }
        else:
            proposal = packet["identity_request"]["proposal"]
            output = {
                "schema": "ghimera.identity-review/1",
                "proposal_digest": packet["proposal_digest"],
                "assessments": [
                    {
                        "pair_id": h["pair_id"],
                        "identity": self.verdict,
                        "role_compatibility": self.verdict,
                        "temporal": self.verdict,
                        "evidence": h["evidence"],
                        "reason": "Independently inspected the offered native contexts.",
                    }
                    for h in proposal["hypotheses"]
                ],
            }
        return response(self.config.served_model, output)


class MentionWire:
    def __init__(self, service, mentions):
        self.config, self.mentions = service, mentions

    async def post(self, body):
        packet = json.loads(json.loads(body)["messages"][1]["content"])
        window = packet["evidence"]["windows"][0]
        quote = window["citation"]["quote"]
        return response(
            self.config.served_model,
            {
                "mentions": [
                    {
                        "key": f"m{i}",
                        "role": role,
                        "surface": surface,
                        "citation_id": window["citation_id"],
                        "occurrence": 0,
                        "confidence": 0.95,
                    }
                    for i, (role, surface) in enumerate(self.mentions)
                    if surface in quote
                ],
                "relations": [],
            },
        )


class NativeExtractor(FakeExtractor):
    def __init__(self, texts):
        self.texts = texts

    async def extract(self, page):
        return Extracted(title="native source", language="und", text=self.texts[page.final_url])


def loop_for(
    cfg, texts, mentions, *, verdict="supported", operation="merge", dates=(None, None), drift=False
):
    proposer = IdentityWire(cfg.models.analyst, operation=operation, dates=dates, drift=drift)
    reviewer = IdentityWire(cfg.models.reviewer, verdict=verdict)
    loop = GoalLoop(
        config=cfg,
        fetcher=FetchLadder((FakeRoute(),)),
        extractor=NativeExtractor(texts),
        scorer=KeywordScorer(),
        judge=FakeJudge(),
        semantic_extractor=SelfHostedModel(
            cfg, cfg.models.analyst, http=MentionWire(cfg.models.analyst, mentions)
        ),
        identity_proposer=SelfHostedModel(cfg, cfg.models.analyst, http=proposer),
        identity_reviewer=SelfHostedModel(cfg, cfg.models.reviewer, http=reviewer),
    )
    return loop, proposer, reviewer


async def observed(loop, texts, run_id="identity-1"):
    from ghimera.models import Scope

    session = await loop.open(Goal(text="map aliases", seeds=tuple(texts)), run_id=run_id)
    await loop.collect(
        session,
        Scope(allowed_hosts=("example.org",), content_types=("text/html",), max_depth=0),
        tuple(texts),
        allow_grade=False,
    )
    return session


def test_native_cross_script_decision_is_separately_reviewed_then_planned_and_replayed(tmp_path):
    cfg = configured(tmp_path)
    texts = {
        "https://example.org/a": "海事局 is also known as Maritime Bureau.",
        "https://example.org/b": "Maritime Bureau is the English name of 海事局.",
    }
    loop, proposer, reviewer = loop_for(
        cfg, texts, (("entity", "海事局"), ("entity", "Maritime Bureau"))
    )

    async def scenario():
        session = await observed(loop, texts)
        before = session.graph.snapshot()
        assert len(proposer.requests) == len(reviewer.requests) == 1
        assert session.budget.identity_proposal_calls == session.budget.identity_review_calls == 1
        assert before.identity_decisions and all(
            d.basis == "model_reviewed" for d in before.identity_decisions
        )
        context = build_context(cfg, session.ledger.snapshot())
        assert any(len(i.members) > 1 for i in context.resolved.identities)
        assert {d.id for d in before.identity_decisions} <= context.references
        validate_context(cfg, context, session.evidence_documents)
        await loop.resolve_identity(session)
        assert len(proposer.requests) == len(reviewer.requests) == 1
        assert session.graph.snapshot() == before
        restored = ResearchGraph(
            cfg.graph,
            "identity-1",
            DirectoryGraphSink(cfg.graph, "identity-1"),
            identity_config=cfg,
            identity_ledger=session.ledger,
        )
        await restored.start("map aliases")
        assert restored.snapshot() == before
        harvest = loop.finish(session, "frontier_empty")
        Harvest.model_validate_json(harvest.model_dump_json())
        report = read_journal(cfg.journal, "identity-1")
        assert report.state == "complete"
        session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("verdict", ["ambiguous", "unsupported"])
def test_homonyms_stay_source_local_after_separate_review(tmp_path, verdict):
    cfg = configured(tmp_path)
    texts = {
        "https://example.org/a": "Jordan heads the northern office.",
        "https://example.org/b": "Jordan is a distinct southern employee.",
    }
    loop, proposer, reviewer = loop_for(cfg, texts, (("entity", "Jordan"),), verdict=verdict)

    async def scenario():
        session = await observed(loop, texts)
        assert len(proposer.requests) == len(reviewer.requests) == 1
        assert session.graph.snapshot().identity_decisions == ()
        assert session.ledger.snapshot()[-1].identity_observation.withheld_pairs == 1
        context = build_context(cfg, session.ledger.snapshot())
        assert all(len(i.members) == 1 for i in context.resolved.identities)
        loop.finish(session, "frontier_empty")
        session.close()

    asyncio.run(scenario())


def test_missing_support_remains_unresolved_even_if_reviewer_approves(tmp_path):
    cfg = configured(tmp_path)
    texts = {"https://example.org/a": "Bureau A and Bureau B are merely listed here."}
    loop, proposer, reviewer = loop_for(
        cfg, texts, (("entity", "Bureau A"), ("entity", "Bureau B")), operation="unresolved"
    )

    async def scenario():
        session = await observed(loop, texts)
        observation = session.ledger.snapshot()[-1].identity_observation
        assert observation.proposal.hypotheses[0].evidence == ()
        assert observation.review.assessments[0].supported
        assert not observation.decisions and observation.withheld_pairs == 1
        assert session.graph.snapshot().identity_decisions == ()
        assert len(proposer.requests) == len(reviewer.requests) == 1
        loop.finish(session, "frontier_empty")
        session.close()

    asyncio.run(scenario())


def test_explicit_policy_and_distinct_model_requirement_refuse_before_calls(tmp_path):
    with pytest.raises(ValidationError):
        configured(tmp_path, require_distinct_models=True)
    cfg = configured(tmp_path)
    raw = cfg.model_dump(by_alias=True)
    raw["identity_automation"].pop("max_pairs_per_pass")
    with pytest.raises(ValidationError):
        GhimeraConfig.model_validate(raw)


def test_proposal_drift_does_not_reach_reviewer_or_graph(tmp_path):
    cfg = configured(tmp_path)
    texts = {"https://example.org/a": "Bureau A", "https://example.org/b": "Bureau B"}
    loop, proposer, reviewer = loop_for(
        cfg, texts, (("entity", "Bureau A"), ("entity", "Bureau B")), drift=True
    )

    async def scenario():
        session = await loop.open(Goal(text="map aliases", seeds=tuple(texts)), run_id="drift-1")
        from ghimera.models import Scope

        with pytest.raises(GhimeraRefused):
            await loop.collect(
                session,
                Scope(allowed_hosts=("example.org",), content_types=("text/html",), max_depth=0),
                tuple(texts),
                allow_grade=False,
            )
        assert len(proposer.requests) == 1 and not reviewer.requests
        assert session.graph.snapshot().identity_decisions == ()
        assert read_journal(cfg.journal, "drift-1").state == "unsealed"
        session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "as_of,merged", [("2020-06-01", True), ("2023-01-01", False), (None, False)]
)
def test_dated_office_alias_does_not_merge_person_or_generalize_time(tmp_path, as_of, merged):
    cfg = configured(tmp_path, roles=("person", "office"), as_of=as_of)
    texts = {
        "https://example.org/a": "Alex held Director East, also called Eastern Director, "
        "from 2020-01-01 to 2021-12-31."
    }
    loop, proposer, reviewer = loop_for(
        cfg,
        texts,
        (("person", "Alex"), ("office", "Director East"), ("office", "Eastern Director")),
        dates=("2020-01-01", "2021-12-31"),
    )

    async def scenario():
        session = await observed(loop, texts)
        view = build_context(cfg, session.ledger.snapshot()).resolved
        assert any(len(i.members) > 1 for i in view.identities) == merged
        people = {n.id for n in session.graph.snapshot().nodes if n.role == "person"}
        assert all(len(i.members) == 1 for i in view.identities if people.intersection(i.members))
        assert len(proposer.requests[0]["identity_request"]["pairs"]) == 1
        assert session.graph.snapshot().identity_decisions[0].valid_from == date(2020, 1, 1)
        loop.finish(session, "frontier_empty")
        session.close()

    asyncio.run(scenario())


def test_invented_date_refuses_before_independent_review(tmp_path):
    cfg = configured(tmp_path)
    texts = {"https://example.org/a": "Bureau A is also called Bureau B."}
    loop, proposer, reviewer = loop_for(
        cfg, texts, (("entity", "Bureau A"), ("entity", "Bureau B")), dates=("2020-01-01", None)
    )

    async def scenario():
        session = await loop.open(Goal(text="map aliases"), run_id="dated-refusal")
        with pytest.raises(GhimeraRefused):
            await loop.collect(
                session,
                Scope(allowed_hosts=("example.org",), content_types=("text/html",), max_depth=0),
                tuple(texts),
                allow_grade=False,
            )
        assert len(proposer.requests) == 1 and not reviewer.requests
        assert session.graph.snapshot().identity_decisions == ()
        session.close()

    asyncio.run(scenario())


def test_manual_retraction_reaches_planning_without_new_calls_or_changed_sources(tmp_path):
    cfg = configured(tmp_path)
    texts = {"https://example.org/a": "Bureau A is also called Bureau B."}
    loop, proposer, reviewer = loop_for(
        cfg, texts, (("entity", "Bureau A"), ("entity", "Bureau B"))
    )

    async def scenario():
        session = await observed(loop, texts)
        original = session.graph.snapshot()
        decision = original.identity_decisions[0]
        retraction = await session.graph.decide_identity(
            operation="retract",
            members=(),
            retracts=(decision.id,),
            evidence=decision.evidence,
            authority="actual-operator",
            revision="manual/1",
            reason="Operator revokes this asserted identity.",
        )
        await loop.resolve_identity(session)
        assert retraction.basis == "human_reviewed" and decision.basis == "model_reviewed"
        view = build_context(cfg, session.ledger.snapshot())
        assert view.resolution_history == (decision, retraction)
        assert all(len(i.members) == 1 for i in view.resolved.identities)
        assert (
            session.graph.snapshot().nodes == original.nodes
            and session.graph.snapshot().edges == original.edges
        )
        assert len(proposer.requests) == len(reviewer.requests) == 1
        loop.finish(session, "frontier_empty")
        session.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["identity_propose", "identity_review"])
def test_exact_ack_survives_application_crash_and_reopen_without_contact(
    tmp_path, monkeypatch, phase
):
    cfg = configured(tmp_path)
    texts = {"https://example.org/a": "Bureau A is also called Bureau B."}
    loop, proposer, reviewer = loop_for(
        cfg, texts, (("entity", "Bureau A"), ("entity", "Bureau B"))
    )

    async def scenario():
        session = await loop.open(Goal(text="map aliases"), run_id="ack-crash")
        append = session.ledger.append

        def crash(row):
            if row.event == phase:
                raise RuntimeError("process died after actual model ACK")
            append(row)

        monkeypatch.setattr(session.ledger, "append", crash)
        with pytest.raises(RuntimeError):
            await loop.collect(
                session,
                Scope(allowed_hosts=("example.org",), content_types=("text/html",), max_depth=0),
                tuple(texts),
                allow_grade=False,
            )
        rows = session.ledger.snapshot()
        assert not uncertain_model_sequences(rows)
        receipt = loop.snapshot(session).receipt
        session.ledger.close()
        report = read_journal(cfg.journal, "ack-crash")
        sink = DirectoryLedgerSink(
            cfg, "ack-crash", session.goal, loop._judge.model, resume_rows=report.rows
        )
        session.ledger = Ledger(sink=sink, restored_rows=report.rows)
        session.budget = RunBudget(cfg, monotonic)
        session.budget.restore(receipt, report.rows, 0, 0)
        session.graph = ResearchGraph(
            cfg.graph,
            "ack-crash",
            DirectoryGraphSink(cfg.graph, "ack-crash"),
            identity_config=cfg,
            identity_ledger=session.ledger,
        )
        await session.graph.start("map aliases")
        await loop.resolve_identity(session)
        assert len(proposer.requests) == len(reviewer.requests) == 1
        assert session.budget.identity_proposal_calls == session.budget.identity_review_calls == 1
        assert session.graph.snapshot().identity_decisions
        loop.finish(session, "frontier_empty")
        session.close()

    asyncio.run(scenario())


def test_graph_ack_without_journal_observation_remains_held(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    texts = {"https://example.org/a": "Bureau A is also called Bureau B."}
    loop, proposer, reviewer = loop_for(
        cfg, texts, (("entity", "Bureau A"), ("entity", "Bureau B"))
    )

    async def scenario():
        session = await loop.open(Goal(text="map aliases"), run_id="graph-crash")
        append = session.ledger.append

        def crash(row):
            if row.event == "identity_resolution":
                raise RuntimeError("process died after actual graph ACK")
            append(row)

        monkeypatch.setattr(session.ledger, "append", crash)
        with pytest.raises(RuntimeError):
            await loop.collect(
                session,
                Scope(allowed_hosts=("example.org",), content_types=("text/html",), max_depth=0),
                tuple(texts),
                allow_grade=False,
            )
        monkeypatch.setattr(session.ledger, "append", append)
        assert session.graph.snapshot().identity_decisions
        with pytest.raises(ValueError, match="unacknowledged"):
            await loop.resolve_identity(session)
        assert len(proposer.requests) == len(reviewer.requests) == 1
        session.close()

    asyncio.run(scenario())


def test_identity_history_is_wholly_omitted_when_planning_cannot_close_members(tmp_path):
    cfg = configured(tmp_path)
    texts = {"https://example.org/a": "Bureau A is also called Bureau B."}
    loop, _, _ = loop_for(cfg, texts, (("entity", "Bureau A"), ("entity", "Bureau B")))

    async def scenario():
        session = await observed(loop, texts)
        raw = cfg.model_dump(by_alias=True)
        raw["research"]["graph_context"]["max_entities"] = 1
        limited = GhimeraConfig.model_validate(raw)
        view = build_context(limited, session.ledger.snapshot())
        assert not view.entities and not view.resolution_history
        assert view.omitted_resolution_decisions == 1 and view.omitted_entities == 2
        session.close()

    asyncio.run(scenario())


def test_caller_supplied_model_decision_without_original_journal_is_refused(tmp_path):
    cfg = configured(tmp_path)
    texts = {"https://example.org/a": "Bureau A is also called Bureau B."}
    loop, _, _ = loop_for(cfg, texts, (("entity", "Bureau A"), ("entity", "Bureau B")))

    async def scenario():
        session = await observed(loop, texts)
        original = session.graph.snapshot()
        foreign = ResearchGraph(cfg.graph, "caller", MemoryGraphSink())
        await foreign.start("map aliases")
        await foreign.append(nodes=original.nodes, edges=original.edges)
        with pytest.raises(GhimeraRefused) as caught:
            await foreign.append(identity_decisions=original.identity_decisions)
        assert caught.value.code == RefusalCode.GRAPH_CONTRACT
        assert foreign.snapshot().identity_decisions == ()
        session.close()

    asyncio.run(scenario())


def test_unknown_identity_outcome_remains_charged_and_never_repeated(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    texts = {"https://example.org/a": "Bureau A is also called Bureau B."}
    loop, proposer, reviewer = loop_for(
        cfg, texts, (("entity", "Bureau A"), ("entity", "Bureau B"))
    )

    async def scenario():
        session = await loop.open(Goal(text="map aliases"), run_id="unknown-identity")
        append = session.ledger.append

        def crash(row):
            if (
                row.model_ack is not None
                and row.model == loop._identity.proposer.model
                and any(
                    r.model_intent is not None
                    and r.model_intent.phase == "identity_propose"
                    and r.sequence == row.model_ack.intent_sequence
                    for r in session.ledger.snapshot()
                )
            ):
                raise RuntimeError("process died before durable ACK")
            append(row)

        monkeypatch.setattr(session.ledger, "append", crash)
        with pytest.raises(RuntimeError):
            await loop.collect(
                session,
                Scope(allowed_hosts=("example.org",), content_types=("text/html",), max_depth=0),
                tuple(texts),
                allow_grade=False,
            )
        monkeypatch.setattr(session.ledger, "append", append)
        charged = session.budget.judge_calls
        assert uncertain_model_sequences(session.ledger.snapshot())
        with pytest.raises(GhimeraRefused):
            await loop.resolve_identity(session)
        assert session.budget.judge_calls == charged and session.budget.identity_proposal_calls == 1
        assert len(proposer.requests) == 1 and not reviewer.requests
        receipt = loop.snapshot(session).receipt
        with pytest.raises(ValueError, match="uncertain model reservations"):
            RunBudget(cfg, monotonic).restore(receipt, session.ledger.snapshot(), 0, 0)
        session.close()

    asyncio.run(scenario())


def test_native_identity_pair_and_call_budgets_are_not_extended(tmp_path):
    cfg = configured(tmp_path, max_pairs_per_pass=1, max_proposal_calls=1, max_review_calls=1)
    texts = {"https://example.org/a": "Bureau A is also called Bureau B and Bureau C."}
    loop, proposer, reviewer = loop_for(
        cfg, texts, (("entity", "Bureau A"), ("entity", "Bureau B"), ("entity", "Bureau C"))
    )

    async def scenario():
        session = await observed(loop, texts)
        first = session.ledger.snapshot()[-1].identity_observation
        assert len(first.request.pairs) == 1 and first.request.omitted_pairs == 2
        with pytest.raises(GhimeraRefused) as caught:
            await loop.resolve_identity(session)
        assert caught.value.code == RefusalCode.BUDGET_EXHAUSTED
        assert len(proposer.requests) == len(reviewer.requests) == 1
        assert session.budget.identity_proposal_calls == session.budget.identity_review_calls == 1
        session.close()

    asyncio.run(scenario())


def test_bounded_mentions_progress_to_uninspected_original_pairs(tmp_path):
    cfg = configured(
        tmp_path, max_mentions=2, max_pairs_per_pass=1, max_proposal_calls=3, max_review_calls=3
    )
    texts = {"https://example.org/a": "Bureau A is also called Bureau B and Bureau C."}
    loop, proposer, reviewer = loop_for(
        cfg, texts, (("entity", "Bureau A"), ("entity", "Bureau B"), ("entity", "Bureau C"))
    )

    async def scenario():
        session = await observed(loop, texts)
        await loop.resolve_identity(session)
        await loop.resolve_identity(session)
        await loop.resolve_identity(session)
        observations = tuple(
            row.identity_observation
            for row in session.ledger.snapshot()
            if row.identity_observation is not None
        )
        assert len(observations) == len(proposer.requests) == len(reviewer.requests) == 3
        assert len({o.request.pairs[0].id for o in observations}) == 3
        assert [o.request.omitted_pairs for o in observations] == [2, 1, 0]
        assert all(len(o.request.mentions) == 2 for o in observations)
        loop.finish(session, "frontier_empty")
        session.close()

    asyncio.run(scenario())


def test_legacy_automation_absence_preserves_native_identities_and_wires(tmp_path):
    from ghimera.model_types import IdentityCallEvidence

    cfg = planning_config(tmp_path)
    assert "identity_automation" not in cfg.model_dump(by_alias=True)
    assert (
        "identity_history"
        not in LedgerRow(sequence=0, event="policy", reason="legacy").model_dump()
    )
    schema = IdentityCallEvidence.model_json_schema()
    assert schema["properties"]["task"]["enum"] == ["identity_propose", "identity_review"]
    assert "gateway" in schema["$defs"]["ModelServiceConfig"]["properties"]


def test_operator_example_has_explicit_versioned_limits_and_date():
    import tomllib
    from pathlib import Path

    from ghimera.graph_planning_types import GraphPlanningConfig
    from ghimera.identity_automation_types import IdentityAutomationConfig

    fragment = tomllib.loads(Path("examples/identity-automation.toml").read_text())
    automation = IdentityAutomationConfig.model_validate(fragment["identity_automation"])
    planning = GraphPlanningConfig.model_validate(fragment["research"]["graph_context"])
    assert automation.require_distinct_models and planning.resolved_as_of == automation.as_of


def test_coherent_request_tamper_and_stale_graph_are_refused(tmp_path, monkeypatch):
    cfg = configured(tmp_path)
    texts = {"https://example.org/a": "Bureau A is also called Bureau B."}
    loop, proposer, reviewer = loop_for(
        cfg, texts, (("entity", "Bureau A"), ("entity", "Bureau B"))
    )

    async def scenario():
        session = await loop.open(Goal(text="map aliases"), run_id="stale-identity")
        post = reviewer.post

        async def drift(body):
            reply = await post(body)
            await session.graph.discovered(
                "https://example.org/new", session.graph.snapshot().nodes[0].id
            )
            return reply

        monkeypatch.setattr(reviewer, "post", drift)
        with pytest.raises(GhimeraRefused) as caught:
            await loop.collect(
                session,
                Scope(allowed_hosts=("example.org",), content_types=("text/html",), max_depth=0),
                tuple(texts),
                allow_grade=False,
            )
        assert caught.value.code == RefusalCode.GRAPH_CONTRACT
        assert session.graph.snapshot().identity_decisions == ()
        rows = session.ledger.snapshot()
        proposal_row = next(row for row in rows if row.identity_proposal is not None)
        request = proposal_row.identity_request.model_copy(
            update={"intent": "coherently edited intent"}
        )
        result = proposal_row.identity_proposal.model_copy(
            update={"request_digest": request.content_digest()}
        )
        altered = tuple(
            row.model_copy(update={"identity_request": request, "identity_proposal": result})
            if row == proposal_row
            else row
            for row in rows
        )
        with pytest.raises(ValueError, match="original matching invocation"):
            validate_identity_rows(cfg, altered)
        assert len(proposer.requests) == len(reviewer.requests) == 1
        session.close()

    asyncio.run(scenario())


def test_native_research_next_round_consumes_reviewed_identity_projection(tmp_path):
    from ghimera.research import ResearchLoop
    from ghimera.research_types import ResearchRequest, ResearchResult, SearchHit, SearchResponse
    from tests.test_graph_planning import GraphPlannerFixture
    from tests.test_intent_research import AnalystFixture, ReviewerFixture, SearchFixture

    cfg = configured(tmp_path)
    texts = {"https://example.org/a": "海事局 is also called Maritime Bureau."}
    collector, proposer, reviewer = loop_for(
        cfg, texts, (("entity", "海事局"), ("entity", "Maritime Bureau"))
    )

    class Search(SearchFixture):
        async def request(self, request):
            self.requests.append(request)
            return SearchResponse(
                raw=b"native controlled search",
                hits=(SearchHit(url=next(iter(texts)), title="native", snippet="discovery only"),),
            )

    planner, search = GraphPlannerFixture(), Search()
    loop = ResearchLoop(
        config=cfg,
        collector=collector,
        search=search,
        planner=planner,
        analyst=AnalystFixture(missing=True),
        reviewer=ReviewerFixture(),
    )
    result = asyncio.run(
        loop.run(ResearchRequest(intent="map aliases"), run_id="connected-research")
    )
    assert result.status == "partial" and len(planner.requests) >= 2
    first, second = planner.requests[0].graph_context, planner.requests[1].graph_context
    assert not first.entities and not first.resolution_history
    assert second.resolution_history and any(len(i.members) > 1 for i in second.resolved.identities)
    assert len(proposer.requests) == len(reviewer.requests) == 1
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result


def test_collector_facade_consumes_resolved_projection_on_next_round(
    tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint, monkeypatch
):
    from ghimera import Collector
    from ghimera.model_client import SelfHostedModels
    from ghimera.model_http import PinnedModelHttp
    from ghimera.research_types import ResearchResult
    from tests.test_http_fetch import ResolverFixture

    base, url = assembled(tmp_path, source_site, search_endpoint, model_endpoint, encoder_endpoint)
    identity = configured(tmp_path)
    raw = base.model_dump(by_alias=True)
    for field in ("graph", "semantics", "identity_automation", "model_work", "journal"):
        raw[field] = identity.model_dump(by_alias=True)[field]
    raw["research"]["graph_context"] = identity.research.graph_context.model_dump(by_alias=True)
    raw["research"]["max_model_input_chars"] = 200_000
    raw["judge_budget"] = 30
    cfg = GhimeraConfig.model_validate(raw)
    source_site[4]["/plain"] = source_site[4]["/plain"].replace(
        b"</article>", "<p>海事局 is also called Maritime Bureau.</p></article>".encode()
    )
    plans, proposals, reviews = [], [], []

    class Wire:
        def __init__(self, service):
            self.config = service
            self.native = PinnedModelHttp(service)
            self.identity = IdentityWire(service)
            self.mentions = MentionWire(
                service, (("entity", "海事局"), ("entity", "Maritime Bureau"))
            )
            self.assessments = 0

        async def post(self, body):
            packet = json.loads(json.loads(body)["messages"][1]["content"])
            task = packet["task"]
            if task in {"identity_propose", "identity_review"}:
                (proposals if task == "identity_propose" else reviews).append(packet)
                return await self.identity.post(body)
            if task == "semantic_extract":
                return await self.mentions.post(body)
            if task == "plan":
                plans.append(packet)
            if task == "assessment":
                self.assessments += 1
                if self.assessments == 1:
                    return response(
                        self.config.served_model,
                        {
                            "coverage": [
                                {
                                    "question_id": "q1",
                                    "status": "unresolved",
                                    "reason": "Controlled first round requires another plan.",
                                    "citations": [],
                                }
                            ]
                        },
                    )
            return await self.native.post(body)

    ports = {
        role: SelfHostedModel(cfg, cfg.models.service(role), http=Wire(cfg.models.service(role)))
        for role in ("planner", "analyst", "reviewer", "judge")
    }
    monkeypatch.setattr(
        SelfHostedModels,
        "from_config",
        classmethod(lambda cls, config, **kw: SelfHostedModels(**ports)),
    )
    result = asyncio.run(
        Collector(cfg, source_resolver=ResolverFixture()).run(
            "map aliases", run_id="collector-identities"
        )
    )
    assert result.status == "answered" and result.harvest.documents[0].url == url
    assert len(plans) == 2 and not plans[0]["graph_context"]["entities"]
    assert plans[1]["graph_context"]["resolution_history"]
    assert any(len(i["members"]) > 1 for i in plans[1]["graph_context"]["resolved"]["identities"])
    assert len(proposals) == len(reviews) == 1
    assert all(d.basis == "model_reviewed" for d in result.harvest.graph.identity_decisions)
    assert ResearchResult.model_validate_json(result.model_dump_json()) == result
