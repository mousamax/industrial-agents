"""The Synera domain core: a building you can query, route through and edit. No LLM in here.

Layers (docs/synera/DATA_MODEL.md §5) and owners (docs/synera/TEAM_PLAN.md):
    model.py     L1 typed plan + indexes + name resolution     shared, implemented
    geometry.py  L2 areas, shared walls, stacking, 3D centres   Owner B
    routing.py   L3 route graph under constraints               Owner A
    state.py     L4 closures, new doors, moved assets           Owner A
    queries.py   graph questions (errand, delivery, what-if)    Owner A
    build.py     Level 4: building from floor plans             Owner B
    view.py      UI contract: scene + view commands             Mousa + Owner D
Agent tools (src/app/agents/building_guide) call these and nothing else.
"""

from app.building.model import OUTSIDE, Building, load_building

__all__ = ["OUTSIDE", "Building", "load_building"]
