"""Owner B's Level 4 checks: `validate`, `compare` and `draft_from_plans` on the kit's real buildings.

    uv run pytest tests/building/test_build.py -q

Real buildings must validate with no errors (warnings are fine: OSM and Swiss doors drawn off the wall),
and every fault we inject must be caught. `compare` must score a building 1.0 against itself and drop on
the metric we damaged. Buildings drafted from the kit's SVG plans must validate and have the true rooms.
"""

from __future__ import annotations

import copy
import csv
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from shapely.geometry import Polygon

from app.building.build import compare, draft_from_plans, validate
from app.building.model import OUTSIDE, building_dir, load_building
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


def moved(raw: dict[str, Any], dx: float, dy: float) -> dict[str, Any]:
    out = copy.deepcopy(raw)
    for s in out["spaces"]:
        s["polygon"] = [[x + dx, y + dy] for x, y in s["polygon"]]
    for element in out["openings"] + out["assets"]:
        element["position"] = [element["position"][0] + dx, element["position"][1] + dy]
    return out


def test_compare_align_ignores_where_the_building_sits():
    truth = raw_copy(FACTORY)
    drafted = moved(truth, 1000.0, -50.0)
    assert compare(drafted, truth)["space_recall"] == 0.0
    assert all(v == pytest.approx(1.0) for v in compare(drafted, truth, align=True).values())


# ---- draft_from_plans (the kit's floor_<level>.svg) ----------------------------------------------------------


def plans(building_id: str) -> list[Path]:
    return sorted(building_dir(building_id).glob("floor_*.svg"))


@pytest.mark.parametrize("building_id", ONE_PER_SOURCE)
def test_drafted_building_validates_and_has_the_true_rooms(building_id):
    drafted = draft_from_plans(plans(building_id), name="Drafted")
    assert errors(drafted) == []
    scores = compare(drafted, raw_copy(building_id), align=True)
    assert scores["mean_iou"] > 0.99  # every matched room has the true outline
    assert scores["area_iou"] > 0.8  # below 1 only where floors sit differently (Swiss truth isn't aligned)


def test_drafted_factory_is_exact():
    truth = raw_copy(FACTORY)
    drafted = draft_from_plans(plans(FACTORY), name="Drafted factory")
    scores = compare(drafted, truth, align=True)
    for metric in (
        "space_recall",
        "space_precision",
        "area_iou",
        "ref_accuracy",
        "door_recall",
        "connector_recall",
    ):
        assert scores[metric] == pytest.approx(1.0), metric
    assert sorted((f["level"], f["name"]) for f in drafted["floors"]) == sorted(
        (f["level"], f["name"]) for f in truth["floors"]
    )
    areas = sorted(Polygon(s["polygon"]).area for s in drafted["spaces"])
    assert areas == pytest.approx(sorted(Polygon(s["polygon"]).area for s in truth["spaces"]), rel=1e-4)
    assert drafted["building"]["name"] == "Drafted factory"


def test_draft_levels_come_from_file_names_unless_given():
    files = plans(FACTORY)
    from_names = {f["level"] for f in draft_from_plans(files, name="x")["floors"]}
    assert from_names == {int(p.stem.split("_")[1]) for p in files}
    given = list(range(10, 10 + len(files)))
    assert {f["level"] for f in draft_from_plans(files, name="x", levels=given)["floors"]} == set(given)


def test_draft_rejects_what_it_cannot_read():
    with pytest.raises(ValueError):
        draft_from_plans([], name="x")
    with pytest.raises(ValueError):
        draft_from_plans(plans(FACTORY), name="x", levels=[0])
    with pytest.raises(NotImplementedError):
        draft_from_plans([building_dir(FACTORY) / "floor_0.pdf"], name="x")


def test_drafted_rooms_and_assets_get_their_types_from_the_furniture():
    # FACTORY is a dev building: the type model learned from train has never seen it.
    scores = compare(draft_from_plans(plans(FACTORY), name="x"), raw_copy(FACTORY), align=True)
    assert scores["room_type_accuracy"] > 0.95
    assert scores["asset_recall"] == pytest.approx(1.0)  # every piece of furniture, where the truth has it
    assert scores["asset_type_accuracy"] > 0.95


def test_guesses_list_every_value_the_plans_dont_state():
    drafted = draft_from_plans(plans(FACTORY), name="x")
    guesses = drafted["guesses"]
    assert errors(drafted) == []  # the extra key doesn't disturb the kit format
    assert {g["source"] for g in guesses} <= {"furniture", "footprint", "reachability", "default"}
    assert all(g["confidence"] is None or 0.0 <= g["confidence"] <= 1.0 for g in guesses)
    typed = {g["element_id"] for g in guesses if g["field"] == "room_type"}
    assert typed == {s["id"] for s in drafted["spaces"] if s["type"] in ("room", "hall", "corridor")}
    assert {g["element_id"] for g in guesses if g["field"] == "type"} == {a["id"] for a in drafted["assets"]}
    for field in ("elevation_m", "height_m"):
        assert {g["element_id"] for g in guesses if g["field"] == field} == {
            f["id"] for f in drafted["floors"]
        }
    stairs = {c["id"] for c in drafted["vertical_connectors"] if c["synthetic"]}
    assert {g["element_id"] for g in guesses if g["field"] == "vertical_connector"} == stairs


# ---- draft_from_plans from floor_<level>_plain.png (scoreboard row level4-from-images) -----------------------

RESPLAN = next(r["building_id"] for r in MANIFEST if r["split"] == "dev" and r["source"] == "resplan")


def png_plans(building_id: str) -> list[Path]:
    return sorted(building_dir(building_id).glob("floor_*_plain.png"))


def test_png_drafted_factory_matches_truth():
    drafted = draft_from_plans(png_plans(FACTORY), name="Drafted factory")
    assert errors(drafted) == []
    scores = compare(drafted, raw_copy(FACTORY), align=True)
    for metric in ("space_recall", "space_precision", "door_recall", "connector_recall"):
        assert scores[metric] == pytest.approx(1.0), metric
    assert scores["mean_iou"] > 0.97  # outlines traced to about a pixel
    assert scores["area_iou"] > 0.99


@pytest.mark.parametrize("building_id", ONE_PER_SOURCE)
def test_png_drafts_validate_and_keep_their_outlines(building_id):
    drafted = draft_from_plans(png_plans(building_id), name="Drafted")
    assert errors(drafted) == []
    scores = compare(drafted, raw_copy(building_id), align=True)
    assert scores["mean_iou"] > 0.8  # OSM rooms hidden under others and unaligned Swiss floors cost the rest
    assert scores["area_iou"] > 0.75


@pytest.mark.parametrize("building_id", [FACTORY, RESPLAN])  # default scale, and a small home scaled up
def test_png_scale_agrees_with_the_svg(building_id):
    from_png = draft_from_plans(png_plans(building_id), name="x")
    from_svg = draft_from_plans(plans(building_id), name="x")
    assert compare(from_png, from_svg, align=True)["mean_iou"] > 0.95


def test_png_metres_per_pixel_sets_the_scale():
    files = png_plans(RESPLAN)

    def total_area(metres_per_pixel: float) -> float:
        drafted = draft_from_plans(files, name="x", metres_per_pixel=metres_per_pixel)
        return sum(Polygon(s["polygon"]).area for s in drafted["spaces"])

    assert total_area(0.1) / total_area(0.05) == pytest.approx(4.0, rel=0.01)
