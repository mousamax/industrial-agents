"""Level 3: several buildings joined by outdoor walkways (a site), routed as one.

A site is a folder with site.json: the buildings (kit ids or folders) and walkways, each joining an entrance
of one building to an entrance of another with a length. Inside each building the judges' route model
applies unchanged; between buildings the only way is a walkway (there is still no free outside link).
Expected lengths here are built independently: leg in A + walkway + leg in B, from single-building graphs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.building.model import OUTSIDE, load_building
from app.building.routing import WALKING, Constraints, RouteGraph

SITE = Path(__file__).parents[2] / "evals" / "datasets" / "sites" / "demo-campus"
CAMPUS, FACTORY = "proc-campus-dev-000", "proc-factory-dev-003"
W1, W2 = "demo-campus-W1", "demo-campus-W2"
LOBBY_DOOR, CORRIDOR_DOOR = f"{CAMPUS}-F0-O0028", f"{CAMPUS}-F0-O0029"
OFFICE_DOOR, AISLE_DOOR = f"{FACTORY}-F0-O0054", f"{FACTORY}-F0-O0020"


def space(b, label: str) -> str:
    (hit,) = b.resolve(label)
    return hit.id


def to_door(building_id: str, start: str, door: str, c: Constraints = WALKING) -> float:
    """Walk inside one building from a space to one entrance (its door node), on its own graph."""
    b = load_building(building_id)
    g = RouteGraph(b, c)
    dist, _, _ = g._dijkstra([start], {f"door:{door}"})
    return dist[f"door:{door}"]


@pytest.fixture(scope="module")
def site():
    return load_building(SITE)


def test_level3_site_loads_every_building(site):
    campus, factory = load_building(CAMPUS), load_building(FACTORY)
    assert len(site.spaces) == len(campus.spaces) + len(factory.spaces)
    assert set(site.walkways) == {W1, W2}
    assert site.walkways[W1].opening_ids == (LOBBY_DOOR, OFFICE_DOOR)
    # floors keep their ids; names say which building, so "Ground floor" isn't ambiguous across buildings
    assert len({f.name for f in site.floors.values()}) == len(site.floors)


def test_level3_route_crosses_by_the_shortest_walkway(site):
    a, z = space(site, "Lab 3.06"), space(site, "Kitchen 0.28")
    route = RouteGraph(site).shortest(a, z)
    assert route is not None
    via_w1 = to_door(CAMPUS, a, LOBBY_DOOR) + 120.0 + to_door(FACTORY, z, OFFICE_DOOR)
    via_w2 = to_door(CAMPUS, a, CORRIDOR_DOOR) + 150.0 + to_door(FACTORY, z, AISLE_DOOR)
    assert route.length_m == pytest.approx(min(via_w1, via_w2), abs=0.02)
    assert (W1 if via_w1 < via_w2 else W2) in route.element_path
    assert route.space_path[0] == a and route.space_path[-1] == z


def test_level3_step_free_avoids_the_stepped_entrance(site):
    a, z = space(site, "Corridor 0.01"), space(site, "Main aisle H-B5")
    walking = RouteGraph(site).shortest(a, z)
    step_free = RouteGraph(site, Constraints(step_free=True)).shortest(a, z)
    assert W2 in walking.element_path  # right next door on foot
    assert (
        step_free is not None and W1 in step_free.element_path and CORRIDOR_DOOR not in step_free.element_path
    )


def test_level3_closed_or_narrow_walkways_are_avoided(site):
    a, z = space(site, "Lab 3.06"), space(site, "Kitchen 0.28")
    both_closed = RouteGraph(site, WALKING.with_closed(W1, W2)).shortest(a, z)
    assert both_closed is None, "no free way round the outside, so no route between the buildings"
    wide = RouteGraph(site, Constraints(min_width_m=3.0)).shortest(a, z)
    assert wide is None or W1 not in wide.element_path  # the covered walkway is 2.5 m wide


def test_level3_outside_still_means_any_entrance(site):
    """Walking out of the site: every space that can leave its own building still can, and no farther."""
    on_site = RouteGraph(site).distances_from([OUTSIDE])
    for building_id in (CAMPUS, FACTORY):
        alone = RouteGraph(load_building(building_id)).distances_from([OUTSIDE])
        for sid, d in alone.items():
            assert on_site[sid] == pytest.approx(d, abs=0.01), sid


def test_level3_single_buildings_are_unchanged():
    b = load_building(CAMPUS)
    assert not b.walkways


def test_level3_walkways_close_and_reopen_in_session_state(site):
    from app.building.state import BuildingState, Close, Reopen

    state = BuildingState(site)
    assert "walkway" in state.apply(Close(W1))
    a, z = space(site, "Lab 3.06"), space(site, "Kitchen 0.28")
    assert RouteGraph(state.building, state.constraints(Constraints(step_free=True))).shortest(a, z) is None
    state.apply(Reopen(W1))
    assert (
        RouteGraph(state.building, state.constraints(Constraints(step_free=True))).shortest(a, z) is not None
    )
