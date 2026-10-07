"""Deterministic questions over selected observations; no destructive resolution.

Exact labels suggest research candidates, not global identity. An alias edge
stays a model assertion. Configured exclusivity suggests competing claims,
not proof that either source is wrong. Original graph records remain intact.
"""

from collections import defaultdict
from datetime import date
from itertools import combinations
from typing import Literal

from ghimera.graph_types import GraphEdge, GraphNode
from ghimera.identity_planning_types import (
    IdentityGroup,
    IdentityPlanningConfig,
    IdentityPlanningView,
    PlanningDispute,
)
from ghimera.refusals import GhimeraRefused, RefusalCode


class CandidateLinks:
    """Local union of candidate links; it never changes a source node's ID."""

    def __init__(self, nodes: tuple[GraphNode, ...]) -> None:
        self._parents = {node.id: node.id for node in nodes}

    def key(self, identity: str) -> str:
        cursor = identity
        while self._parents[cursor] != cursor:
            cursor = self._parents[cursor]
        return cursor

    def join(self, left: str, right: str) -> None:
        first, second = sorted((self.key(left), self.key(right)))
        self._parents[second] = first


def _interval(edge: GraphEdge) -> tuple[date, date, bool]:
    try:
        start = date.fromisoformat(edge.valid_from) if edge.valid_from else date.min
        end = date.fromisoformat(edge.valid_to) if edge.valid_to else date.max
    except ValueError:
        raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT) from None
    if (
        start > end
        or (edge.valid_from is not None and edge.valid_from != start.isoformat())
        or (edge.valid_to is not None and edge.valid_to != end.isoformat())
    ):
        raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
    # Sentinels only rule out a provably disjoint interval; never emit invented dates.
    return start, end, edge.valid_from is None or edge.valid_to is None


def build_identity_view(
    policy: IdentityPlanningConfig, nodes: tuple[GraphNode, ...], edges: tuple[GraphEdge, ...]
) -> IdentityPlanningView:
    by_id = {node.id: node for node in nodes}
    if len(by_id) != len(nodes) or any(
        not {edge.source, edge.target} <= by_id.keys() for edge in edges
    ):
        raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
    links = CandidateLinks(nodes)
    names: dict[tuple[str, str], str] = {}
    exact_pairs: list[tuple[str, str]] = []
    for node in nodes:
        name_key = (node.role, node.label)
        if name_key in names:
            links.join(names[name_key], node.id)
            exact_pairs.append((names[name_key], node.id))
        else:
            names[name_key] = node.id
    aliases: list[GraphEdge] = []
    cross_role = 0
    for edge in edges:
        if edge.rule not in policy.alias_rules:
            continue
        if edge.claim_status != "model_asserted" or not edge.evidence:
            raise GhimeraRefused(RefusalCode.RESEARCH_CONTRACT)
        if by_id[edge.source].role != by_id[edge.target].role:
            cross_role += 1
            continue
        links.join(edge.source, edge.target)
        aliases.append(edge)
    members: dict[str, list[str]] = defaultdict(list)
    for identity in sorted(by_id):
        members[links.key(identity)].append(identity)
    groups: list[IdentityGroup] = []
    omitted_groups = omitted_members = 0
    for key, identities in sorted(members.items()):
        if len(identities) < 2:
            continue
        if len(identities) > policy.max_members_per_group or len(groups) >= policy.max_groups:
            omitted_groups += 1
            omitted_members += len(identities)
            continue  # Never emit a truncated group pretending to retain every member.
        bases: list[Literal["exact_surface_and_role", "model_asserted_alias"]] = []
        if any(links.key(left) == key for left, _ in exact_pairs):
            bases.append("exact_surface_and_role")
        alias_ids = tuple(sorted(edge.id for edge in aliases if links.key(edge.source) == key))
        if alias_ids:
            bases.append("model_asserted_alias")
        group = IdentityGroup(
            id="identity:" + "0" * 64,
            role=by_id[identities[0]].role,
            node_ids=tuple(identities),
            bases=tuple(bases),
            alias_relation_ids=alias_ids,
            status="unresolved",
        )
        groups.append(group.model_copy(update={"id": "identity:" + group.content_digest()}))
    subject_of = {node: group.id for group in groups for node in group.node_ids}
    buckets: dict[tuple[str, str], list[GraphEdge]] = defaultdict(list)
    intervals: dict[str, tuple[date, date, bool]] = {}
    for edge in edges:
        if edge.rule in policy.exclusive_relations:
            intervals[edge.id] = _interval(edge)
            buckets[(edge.rule, subject_of.get(edge.source, edge.source))].append(edge)
    total_pairs = sum(len(bucket) * (len(bucket) - 1) // 2 for bucket in buckets.values())
    disputes: list[PlanningDispute] = []
    examined = omitted_disputes = 0
    for (rule, subject), bucket in sorted(buckets.items()):
        for left, right in combinations(sorted(bucket, key=lambda edge: edge.id), 2):
            if examined >= policy.max_pair_checks:
                break
            examined += 1
            if subject_of.get(left.target, left.target) == subject_of.get(
                right.target, right.target
            ):
                # Same candidate target; identity stays an unresolved group question.
                continue
            first_start, first_end, first_unknown = intervals[left.id]
            second_start, second_end, second_unknown = intervals[right.id]
            if first_end < second_start or second_end < first_start:
                continue
            unknown = first_unknown or second_unknown
            if unknown and policy.unknown_time_policy == "skip":
                continue
            if len(disputes) >= policy.max_disputes:
                omitted_disputes += 1
                continue
            dispute = PlanningDispute(
                id="dispute:" + "0" * 64,
                rule=rule,
                subject_reference=subject,
                left_relation_id=left.id,
                right_relation_id=right.id,
                basis="unknown_time" if unknown else "known_overlap",
                status="unresolved",
            )
            disputes.append(
                dispute.model_copy(update={"id": "dispute:" + dispute.content_digest()})
            )
    return IdentityPlanningView(
        schema="ghimera.identity-view/1",
        policy_digest=policy.content_digest(),
        scope="selected_planning_population",
        groups=tuple(groups),
        disputes=tuple(disputes),
        omitted_groups=omitted_groups,
        omitted_members=omitted_members,
        omitted_disputes=omitted_disputes,
        examined_pairs=examined,
        omitted_pairs=total_pairs - examined,
        cross_role_aliases=cross_role,
    )
