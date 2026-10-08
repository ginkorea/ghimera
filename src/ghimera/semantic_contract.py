"""A bounded model-input view derived only from the existing native graph recipe.

This is not a second ontology owner or proof of source entailment. The projector
still owns role/citation/span admission; review never establishes corroboration.
"""

from typing import TYPE_CHECKING, Literal

from pydantic import Field

from ghimera.graph_types import Digest, GraphRecord, GraphRelation, GraphRole
from ghimera.semantic_types import SemanticConfig

if TYPE_CHECKING:
    from ghimera.config import GhimeraConfig


class SemanticGraphContract(GraphRecord):
    schema_version: Literal["ghimera.semantic-graph-contract/1"] = Field(alias="schema")
    graph_config_sha256: Digest
    semantic_policy_sha256: Digest
    roles: tuple[GraphRole, ...]
    mention_rule: GraphRelation
    relations: tuple[GraphRelation, ...]


def build_graph_contract(
    config: "GhimeraConfig", policy: SemanticConfig
) -> SemanticGraphContract | None:
    """Same exact projection view for model prompt, reservation and explicit replay."""
    if policy.schema_version not in {"ghimera.semantics/5", "ghimera.semantics/6"}:
        return None
    from ghimera.config import GhimeraConfig

    # Reuse the owning cross-policy admission, including defensive model_copy
    # validation. No artifact/network/model contact occurs during config parsing.
    config = GhimeraConfig.model_validate(config.model_dump())
    policy = SemanticConfig.model_validate(policy.model_dump())
    if config.semantics != policy or config.graph is None:
        raise ValueError("graph-bound extraction requires its exact configured semantic policy")
    graph = config.graph
    roles = {role.name: role for role in graph.roles}
    relations = {relation.name: relation for relation in graph.relations}
    return SemanticGraphContract(
        schema="ghimera.semantic-graph-contract/1",
        graph_config_sha256=graph.content_digest(),
        semantic_policy_sha256=policy.content_digest(),
        roles=tuple(roles[name] for name in policy.entity_roles),
        mention_rule=relations[policy.mention_rule],
        relations=tuple(relations[name] for name in policy.relation_rules),
    )
