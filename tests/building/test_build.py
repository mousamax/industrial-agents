"""Owner B's Level 4 checks: `validate` and `compare` on the kit's real buildings and on broken copies.

    uv run pytest tests/building/test_build.py -q

Real buildings must validate with no errors (warnings are fine: OSM and Swiss doors drawn off the wall),
and every fault we inject must be caught. `compare` must score a building 1.0 against itself and drop on
the metric we damaged.
"""

from __future__ import annotations

import copy
import csv
from collections.abc import Callable
from typing import Any

import pytest

from app.building.build import compare, validate
from app.building.model import OUTSIDE, load_building
from app.settings import get_settings

DATA = get_settings().synera_kit_dir / "data"

if not (DATA / "manifest.csv").exists():
    pytest.skip("synera-kit data missing: git submodule update --init", allow_module_level=True)

with (DATA / "manifest.csv").open(encoding="utf-8") as f:
    MANIFEST = list(csv.DictReader(f))
DEV = [row["building_id"] for row in MANIFEST if row["split"] == "dev"]
ONE_PER_SOURCE = sorted(
    {row["source"]: row["building_id"] for row in MANIFEST if row["split"] == "dev"}.values()
)
FACTORY = next(r["building_id"] for r in MANIFEST if r["split"] == "dev" and r["source"] == "procedural")


def raw_copy(building_id: str) -> dict[str, Any]:
    """A private copy of the building.json: load_building's raw is cached and shared, never mutate it."""
    return copy.deepcopy(load_building(building_id).raw)


def errors(raw: dict[str, Any]) -> list[tuple[str | None, str]]:
    return [(i.element_id, i.message) for i in validate(raw) if i.severity == "error"]


# ---- validate ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("building_id", DEV)
def test_real_buildings_validate_without_errors(building_id):
    assert errors(raw_copy(building_id)) == []


def _duplicate_id(raw):
    raw["assets"][0]["id"] = raw["spaces"][0]["id"]
    return raw["spaces"][0]["id"], "used 2 times"


def _unknown_space(raw):
    raw["openings"][0]["space_ids"][0] = "no-such-space"
    return raw["openings"][0]["id"], "unknown space"


def _across_floors(raw):
    door = next(o for o in raw["openings"] if OUTSIDE not in o["space_ids"])
    other_floor = next(s for s in raw["spaces"] if s["floor_id"] != door["floor_id"])
    door["space_ids"][1] = other_floor["id"]
    return door["id"], "on another floor"


def _connector_twice_on_a_floor(raw):
    stairs = raw["vertical_connectors"][0]
    stairs["floor_ids"][1] = stairs["floor_ids"][0]
    return stairs["id"], "stops 2 times"


def _unreachable_room(raw):
    in_connector = {sid for c in raw["vertical_connectors"] for sid in c["space_ids"]}
    room = next(s for s in raw["spaces"] if s["type"] == "room" and s["id"] not in in_connector)
    raw["openings"] = [o for o in raw["openings"] if room["id"] not in o["space_ids"]]
    return room["id"], "not reachable"


def _no_entrance(raw):
    raw["openings"] = [o for o in raw["openings"] if OUTSIDE not in o["space_ids"]]
    return None, "no entrance"


def _bow_tie(raw):
    raw["spaces"][0]["polygon"] = [[0, 0], [1, 1], [1, 0], [0, 1]]
    return raw["spaces"][0]["id"], "self-intersecting"


def _missing_field(raw):
    del raw["spaces"][0]["type"]
    return raw["spaces"][0]["id"], "missing ['type']"


FAULTS: dict[str, Callable[[dict[str, Any]], tuple[str | None, str]]] = {
    "duplicate id": _duplicate_id,
    "opening to unknown space": _unknown_space,
    "opening across floors": _across_floors,
    "connector stops twice on a floor": _connector_twice_on_a_floor,
    "unreachable room": _unreachable_room,
    "no entrance": _no_entrance,
    "self-intersecting polygon": _bow_tie,
    "missing field": _missing_field,
}


@pytest.mark.parametrize("fault", FAULTS)
def test_validate_catches(fault):
    raw = raw_copy(FACTORY)
    element_id, expected = FAULTS[fault](raw)
    found = errors(raw)
    assert any(eid == element_id and expected in message for eid, message in found), found


def test_door_off_the_wall_is_a_warning():
    raw = raw_copy(FACTORY)
    door = raw["openings"][0]
    door["position"] = [door["position"][0] + 50, door["position"][1]]
    issues = [i for i in validate(raw) if i.element_id == door["id"]]
    assert issues and all(i.severity == "warning" and "from the wall" in i.message for i in issues)


# ---- compare ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("building_id", ONE_PER_SOURCE)
def test_compare_identity_is_perfect(building_id):
    raw = raw_copy(building_id)
    assert all(v == pytest.approx(1.0) for v in compare(raw, raw).values())


def test_compare_drops_with_missing_doors():
    truth = raw_copy(FACTORY)
    drafted = copy.deepcopy(truth)
    drafted["openings"] = []
    scores = compare(drafted, truth)
    assert scores["door_recall"] == 0.0
    assert scores["space_recall"] == 1.0
    assert scores["score"] < 1.0


def test_compare_drops_with_missing_rooms():
    truth = raw_copy(FACTORY)
    drafted = copy.deepcopy(truth)
    drafted["spaces"] = drafted["spaces"][::2]
    scores = compare(drafted, truth)
    assert scores["space_precision"] == 1.0
    assert scores["space_recall"] == pytest.approx(0.5, abs=0.02)
    assert scores["area_iou"] < 0.9


def test_compare_drops_when_drawn_elsewhere():
    truth = raw_copy(FACTORY)
    drafted = copy.deepcopy(truth)
    for s in drafted["spaces"]:
        s["polygon"] = [[x + 1000, y] for x, y in s["polygon"]]
    scores = compare(drafted, truth)
    assert scores["space_recall"] == 0.0
    assert scores["area_iou"] == 0.0
