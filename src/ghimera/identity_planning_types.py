"""Unresolved identity and competing-assertion questions, never factual merges."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ghimera.graph_types import Count, Digest, GraphRecord, Name, Positive, Text


class IdentityPlanningConfig(GraphRecord):
    schema_version: Literal["ghimera.identity-planning/1"] = Field(alias="schema")
    candidate_strategy: Literal["exact_surface_and_role"]
    alias_rules: tuple[Name, ...]
    exclusive_relations: tuple[Name, ...]
    unknown_time_policy: Literal["report_possible", "skip"]
    max_groups: Positive
    max_members_per_group: Annotated[int, Field(strict=True, ge=2)]
    max_disputes: Positive
    max_pair_checks: Positive

    @model_validator(mode="after")
    def distinct(self) -> "IdentityPlanningConfig":
        if (
            len(set(self.alias_rules)) != len(self.alias_rules)
            or len(set(self.exclusive_relations)) != len(self.exclusive_relations)
            or set(self.alias_rules) & set(self.exclusive_relations)
        ):
            raise ValueError("identity rules must be distinct and cannot also be exclusive")
        return self


class IdentityGroup(GraphRecord):
    id: Annotated[str, Field(pattern=r"^identity:[0-9a-f]{64}$")]
    role: Name
    node_ids: Annotated[tuple[Text, ...], Field(min_length=2)]
    bases: Annotated[
        tuple[Literal["exact_surface_and_role", "model_asserted_alias"], ...], Field(min_length=1)
    ]
    alias_relation_ids: tuple[Text, ...]
    status: Literal["unresolved"]

    @model_validator(mode="after")
    def complete(self) -> "IdentityGroup":
        if (
            len(set(self.node_ids)) != len(self.node_ids)
            or len(set(self.bases)) != len(self.bases)
            or len(set(self.alias_relation_ids)) != len(self.alias_relation_ids)
            or bool(self.alias_relation_ids) != ("model_asserted_alias" in self.bases)
        ):
            raise ValueError("identity candidate must retain distinct members and alias basis")
        return self


class PlanningDispute(GraphRecord):
    id: Annotated[str, Field(pattern=r"^dispute:[0-9a-f]{64}$")]
    rule: Name
    subject_reference: Text
    left_relation_id: Text
    right_relation_id: Text
    basis: Literal["known_overlap", "unknown_time"]
    status: Literal["unresolved"]

    @model_validator(mode="after")
    def distinct(self) -> "PlanningDispute":
        if self.left_relation_id == self.right_relation_id:
            raise ValueError("competing assertions require two distinct original claims")
        return self


class IdentityPlanningView(GraphRecord):
    schema_version: Literal["ghimera.identity-view/1"] = Field(alias="schema")
    policy_digest: Digest
    scope: Literal["selected_planning_population"]
    groups: tuple[IdentityGroup, ...]
    disputes: tuple[PlanningDispute, ...]
    omitted_groups: Count
    omitted_members: Count
    omitted_disputes: Count
    examined_pairs: Count
    omitted_pairs: Count
    cross_role_aliases: Count

    @model_validator(mode="after")
    def distinct(self) -> "IdentityPlanningView":
        if len({g.id for g in self.groups}) != len(self.groups) or len(
            {d.id for d in self.disputes}
        ) != len(self.disputes):
            raise ValueError("identity/dispute references must be distinct")
        members = [identity for group in self.groups for identity in group.node_ids]
        if len(set(members)) != len(members):
            raise ValueError("one observed mention cannot appear in multiple candidate groups")
        return self

    @property
    def references(self) -> frozenset[str]:
        return frozenset(g.id for g in self.groups) | frozenset(d.id for d in self.disputes)
