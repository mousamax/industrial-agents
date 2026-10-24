"""Owner B's walking distances inside a space (Synera's route rule: door to door, 15 cm off the walls).

    uv run pytest tests/building/test_walking.py -q

Small made-up rooms check the geometry exactly; one real corridor checks the case that made the kit's
centre-point model pick the far staircase (docs/synera/ROUTE_SCORING.md, proc-campus-dev-001-Q05).
"""

from __future__ import annotations

import math

import pytest

from app.building import geometry
from app.building.model import Building, load_building, parse_building
from app.settings import get_settings


def one_room(polygon: list[list[float]], doors: list[list[float]]) -> Building:
    """A building with one room S and entrances D0, D1, ... at the given positions."""
    return parse_building({
        "building": {"id": "t", "name": "Test", "source": "test"},
        "floors": [{"id": "F0", "level": 0, "name": "Ground floor", "elevation_m": 0.0, "height_m": 3.0}],
        "spaces": [{"id": "S", "floor_id": "F0", "type": "room", "room_type": "office", "name": "Office 0.01",
                    "ref": "0.01", "polygon": polygon, "synthetic": False}],
        "openings": [{"id": f"D{i}", "floor_id": "F0", "space_ids": ["S", "outside"], "kind": "entrance",
                      "position": xy, "width_m": 0.9, "step_free": True, "synthetic": False}
                     for i, xy in enumerate(doors)],
        "vertical_connectors": [], "assets": [],
    })  # fmt: skip


RECTANGLE = [[0, 0], [10, 0], [10, 4], [0, 4]]
L_SHAPE = [[0, 0], [10, 0], [10, 10], [8, 10], [8, 2], [0, 2]]  # inner corner at (8, 2)


def test_walking_straight_room_is_a_straight_line():
    b = one_room(RECTANGLE, [[0, 2], [10, 2]])
    # doors sit on the walls; the walk starts and ends 15 cm in
    assert geometry.walking_distance(b, "S", (0, 2), (10, 2)) == pytest.approx(9.7)
    assert geometry.walking_distance(b, "S", (2, 2), (7, 2)) == pytest.approx(5.0)


def test_walking_l_shaped_room_goes_round_the_inner_corner():
    b = one_room(L_SHAPE, [[0, 1], [9, 10]])
    start, end = (0.15, 1.0), (9.0, 9.85)
    walked = geometry.walking_distance(b, "S", (0, 1), (9, 10))
    corner = (8 + 0.15 / math.sqrt(2), 2 - 0.15 / math.sqrt(2))  # 15 cm off the inner corner
    assert walked > math.dist(start, end) + 0.5  # the straight line would cross the walls
    assert walked == pytest.approx(math.dist(start, corner) + math.dist(corner, end), abs=0.02)


def test_walking_keeps_off_the_walls():
    b = one_room(RECTANGLE, [[0, 0.05], [10, 0.05]])
    # a door next to a corner: the walk runs 15 cm off both walls, from (0.15, 0.15) to (9.85, 0.15)
    assert geometry.walking_distance(b, "S", (0, 0.05), (10, 0.05)) == pytest.approx(9.7)
    assert geometry.walking_distance(b, "S", (0, 0.05), (10, 0.05), inset_m=0.5) == pytest.approx(9.0)


def test_walking_space_too_narrow_to_inset_uses_its_outline():
    b = one_room([[0, 0], [10, 0], [10, 0.2], [0, 0.2]], [[0, 0.1], [10, 0.1]])
    assert geometry.walking_distance(b, "S", (0, 0.1), (10, 0.1)) == pytest.approx(10.0)


def test_walking_door_to_door_lists_every_pair_both_ways_and_is_cached():
    b = one_room(L_SHAPE, [[0, 1], [9, 10], [10, 5]])
    walks = geometry.door_to_door(b, "S")
    ports = {"S", "D0", "D1", "D2"}  # the space id stands for its start point
    assert set(walks) == {(a, z) for a in ports for z in ports if a != z}
    assert all(walks[a, z] == walks[z, a] for a, z in walks)
    assert walks["D0", "D1"] == pytest.approx(geometry.walking_distance(b, "S", (0, 1), (9, 10)))
    assert geometry.door_to_door(b, "S") is walks


@pytest.mark.skipif(
    not (get_settings().synera_kit_dir / "data" / "dev").exists(), reason="synera-kit data missing"
)
def test_walking_puts_the_staircase_by_the_door_first():
    # proc-campus-dev-001-Q05: from Toilet 0.16 the kit's model picks Staircase 2 because it measures every
    # corridor crossing through the corridor's centre point. Walking door to door, Staircase 1 is next door.
    b = load_building("proc-campus-dev-001")
    toilet = b.resolve("Toilet 0.16")[0]
    door = b.openings_of(toilet.id)[0]
    corridor = door.other_side(toilet.id)
    walks = geometry.door_to_door(b, corridor)

    def to_stairs(name: str) -> float:
        stairs = b.resolve(name, floor=toilet.floor_id)[0]
        return walks[door.id, next(o.id for o in b.openings_of(stairs.id) if corridor in o.space_ids)]

    assert to_stairs("Staircase 1") < 5.0 < 20.0 < to_stairs("Staircase 2")
