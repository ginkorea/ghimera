"""Legacy root facade; all objects belong to the single ghimera implementation.

Nested imports must migrate to ghimera. This facade does not load duplicate
submodules, install import hooks, or change saved configuration/result schemas.
"""

from ghimera import Collector, GhimeraConfig, Goal, GoalLoop, Harvest, Scope

ChimeraConfig = GhimeraConfig

__all__ = ["ChimeraConfig", "Collector", "Goal", "GoalLoop", "Harvest", "Scope"]
