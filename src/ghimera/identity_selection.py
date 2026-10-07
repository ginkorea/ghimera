"""Atomic evidence bundles for explicitly requested identity-first planning.

This selector owns only one population's candidate closure and ordering. It
does not resolve identity, change a graph, enlarge budgets or invent evidence.
The caller remains the owner of admission under the serialized context limits.
"""

from dataclasses import dataclass

from ghimera.graph_types import GraphEdge, GraphNode
from ghimera.identity_planning import build_identity_view
from ghimera.identity_planning_types import IdentityPlanningConfig


@dataclass(frozen=True)
class SelectionUnit:
    node_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]


class IdentitySelection:
    def __init__(
        self,
        policy: IdentityPlanningConfig,
        nodes: tuple[GraphNode, ...],
        edges: tuple[GraphEdge, ...],
    ) -> None:
        self._view = build_identity_view(policy, nodes, edges)
        self._edges = {edge.id: edge for edge in edges}
        self._ranks = {node.id: rank for rank, node in enumerate(nodes)}
        self._groups = {node: group for group in self._view.groups for node in group.node_ids}

    def close(self, unit: SelectionUnit) -> SelectionUnit:
        """Keep endpoints, whole bounded candidates and their original alias claims."""
        nodes = dict.fromkeys(unit.node_ids)
        edges = dict.fromkeys(unit.relation_ids)
        for identity in edges:
            edge = self._edges[identity]
            nodes.update(dict.fromkeys((edge.source, edge.target)))
        for identity in tuple(nodes):
            group = self._groups.get(identity)
            if group is not None:
                nodes.update(dict.fromkeys(group.node_ids))
                edges.update(dict.fromkeys(group.alias_relation_ids))
        return SelectionUnit(tuple(nodes), tuple(edges))

    def questions(self) -> tuple[SelectionUnit, ...]:
        """Smallest complete bundles first; ties prefer the newest observed member."""
        units = [
            SelectionUnit(group.node_ids, group.alias_relation_ids) for group in self._view.groups
        ]
        units.extend(
            self.close(SelectionUnit((), (dispute.left_relation_id, dispute.right_relation_id)))
            for dispute in self._view.disputes
        )
        return tuple(
            sorted(
                set(units),
                key=lambda unit: (
                    len(unit.node_ids),
                    -max(self._ranks[node] for node in unit.node_ids),
                    len(unit.relation_ids),
                    unit.node_ids,
                    unit.relation_ids,
                ),
            )
        )
