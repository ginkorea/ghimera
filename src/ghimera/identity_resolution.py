"""Reversible, time-scoped views over the native graph's reviewed decision journal.

Names and similarity never resolve identities. A caller supplies an explicitly
reviewed, source-backed decision; the graph owner validates and journals it.
Source-local nodes and semantic claims remain immutable through every view.
"""

from datetime import date, timedelta
from itertools import combinations
from typing import Annotated, Literal

from pydantic import Field

from ghimera.graph_types import (
    Count,
    GraphEdge,
    GraphNode,
    GraphRecord,
    IdentityDecision,
    IdentityResolutionConfig,
    Text,
)


class ResolvedIdentity(GraphRecord):
    representative: Text
    members: Annotated[tuple[Text, ...], Field(min_length=1)]
    decision_ids: tuple[Text, ...]
    status: Literal["reviewed_resolution", "source_local"]


class IdentityResolutionView(GraphRecord):
    schema_version: Literal["ghimera.identity-resolution-view/1"] = Field(alias="schema")
    as_of: date | None
    identities: tuple[ResolvedIdentity, ...]
    active_decision_ids: tuple[Text, ...]
    omitted_temporal_decisions: Count


def active_decisions(
    decisions: tuple[IdentityDecision, ...],
) -> tuple[IdentityDecision, ...]:
    active: dict[str, IdentityDecision] = {}
    seen: set[str] = set()
    for decision in decisions:
        if decision.id in seen:
            raise ValueError("identity history repeats a decision")
        seen.add(decision.id)
        if not set(decision.retracts) <= active.keys():
            raise ValueError("a reversal must name currently active prior decisions")
        for identity in decision.retracts:
            del active[identity]
        if decision.operation != "retract":
            active[decision.id] = decision
    return tuple(active.values())


def _eligible(decision: IdentityDecision, as_of: date | None) -> bool:
    if as_of is None:
        return decision.valid_from is None and decision.valid_to is None
    return (decision.valid_from is None or as_of >= decision.valid_from) and (
        decision.valid_to is None or as_of <= decision.valid_to
    )


def resolve_identities(
    nodes: tuple[GraphNode, ...],
    decisions: tuple[IdentityDecision, ...],
    *,
    as_of: date | None = None,
) -> IdentityResolutionView:
    """A dated decision is never silently generalized to every period."""
    active = active_decisions(decisions)
    selected = tuple(item for item in active if _eligible(item, as_of))
    parents = {node.id: node.id for node in nodes}

    def key(identity: str) -> str:
        while parents[identity] != identity:
            identity = parents[identity]
        return identity

    for decision in selected:
        if not set(decision.members) <= parents.keys():
            raise ValueError("identity decision requires its original source-local nodes")
        if decision.operation == "merge":
            for member in decision.members[1:]:
                left, right = sorted((key(decision.members[0]), key(member)))
                parents[right] = left
    for decision in selected:
        if decision.operation == "split" and any(
            key(left) == key(right) for left, right in combinations(decision.members, 2)
        ):
            raise ValueError("merge conflicts with an active reviewed split in this period")
    groups: dict[str, list[str]] = {}
    for identity in sorted(parents):
        groups.setdefault(key(identity), []).append(identity)
    return IdentityResolutionView(
        schema="ghimera.identity-resolution-view/1",
        as_of=as_of,
        identities=tuple(
            ResolvedIdentity(
                representative=identity,
                members=tuple(members),
                decision_ids=tuple(
                    sorted(
                        item.id
                        for item in selected
                        if item.operation == "merge" and set(item.members) <= set(members)
                    )
                ),
                status="reviewed_resolution" if len(members) > 1 else "source_local",
            )
            for identity, members in sorted(groups.items())
        ),
        active_decision_ids=tuple(item.id for item in selected),
        omitted_temporal_decisions=len(active) - len(selected) if as_of is None else 0,
    )


def validate_decisions(
    policy: IdentityResolutionConfig | None,
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...],
    previous: tuple[IdentityDecision, ...],
    added: tuple[IdentityDecision, ...],
) -> None:
    if not added:
        return
    if policy is None or len(previous) + len(added) > policy.max_decisions:
        raise ValueError("identity resolution is absent or exceeds its configured history bound")
    by_id = {node.id: node for node in nodes}
    history = list(previous)
    for decision in added:
        type(decision).model_validate_json(decision.model_dump_json())
        expected = (
            "resolution:"
            + decision.model_copy(update={"id": "resolution:" + "0" * 64}).content_digest()
        )
        active = {item.id: item for item in active_decisions(tuple(history))}
        if decision.id != expected or not set(decision.retracts) <= active.keys():
            raise ValueError("identity decision identity or reversal references do not match")
        members = decision.members or tuple(
            dict.fromkeys(
                member for identity in decision.retracts for member in active[identity].members
            )
        )
        if (
            len(decision.members) > policy.max_members_per_decision
            or len(decision.evidence) > policy.max_evidence_per_decision
            or len(decision.reason) > policy.max_reason_chars
            or not set(members) <= by_id.keys()
            or len({by_id[member].role for member in members}) != 1
            or any(by_id[member].role not in policy.roles for member in members)
        ):
            raise ValueError("identity decisions require bounded same-role observed members")
        # Bind every member to an original source observation, not a free-floating name.
        for member in members:
            if not any(edge.target == member and edge.evidence for edge in edges):
                raise ValueError("identity member has no source-backed observation")
            if not any(by_id[member].label in item.quote for item in decision.evidence):
                raise ValueError("review evidence must identify each original surface")
        for evidence in decision.evidence:
            doc = by_id.get(evidence.document_id)
            if (
                doc is None
                or doc.role != "document"
                or doc.text is None
                or doc.content_sha256 is None
                or not evidence.matches_reading(
                    doc.content_sha256,
                    doc.text,
                    pdf_reading=doc.pdf_reading,
                    visual_readings=doc.visual_readings,
                )
            ):
                raise ValueError("identity decision evidence does not bind retained source content")
        history.append(decision)
        # Test each temporal boundary and its neighbour, including open intervals.
        dates = {date.min, date.max}
        for item in active_decisions(tuple(history)):
            for boundary in (item.valid_from, item.valid_to):
                if boundary is not None:
                    dates.add(boundary)
                    if boundary > date.min:
                        dates.add(boundary - timedelta(days=1))
                    if boundary < date.max:
                        dates.add(boundary + timedelta(days=1))
        for as_of in dates:
            resolve_identities(nodes, tuple(history), as_of=as_of)
