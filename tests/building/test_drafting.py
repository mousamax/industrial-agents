"""Level 4 by conversation: plans in, a building out, and what the plans don't say settled by answers.

    uv run pytest tests/building/test_drafting.py -q

Scored on the team board as level4-conversation (with the draft-* scenarios in scenarios.yaml).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import ModelRetry

from app.agents.building_guide import drafting
from app.agents.building_guide.drafting import (
    accept_guesses,
    create_draft,
    draft_building,
    fix_draft,
    review_draft,
)
from app.agents.building_guide.tools import Deps, list_rooms, route
from app.api import main
from app.settings import get_settings

FACTORY = get_settings().synera_kit_dir / "data" / "dev" / "proc-factory-dev-003"


@pytest.fixture(autouse=True)
def drafts_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(drafting, "drafts_dir", lambda: tmp_path / "drafts")
    monkeypatch.setattr(drafting, "_drafts", {})
    monkeypatch.setattr(main, "_live", {})


def _ctx(draft) -> SimpleNamespace:
    return SimpleNamespace(deps=Deps(draft.state))


def _png_draft():
    return create_draft(sorted(FACTORY.glob("floor_*_plain.png")), name="Hall 7")


def test_drafting_png_asks_about_room_types_doors_and_heights():
    draft = _png_draft()
    review = json.loads(review_draft(_ctx(draft)))
    assert review["floors"] == 2 and review["rooms"] > 0
    assert review["questions"] and review["questions"][0]["field"] == "room_type"  # least sure first
    assert {"width_m", "step_free", "height_m", "room_type"} <= {q["field"] for q in draft.open}
    assert all(q["highlight"] for q in draft.open if q["field"] == "room_type")
    assert (draft.folder / "building.json").exists() and (draft.folder / "plans").is_dir()


def test_drafting_one_answer_settles_a_group_and_counts_follow():
    draft = _png_draft()
    ctx = _ctx(draft)
    q = next(q for q in draft.open if q["field"] == "room_type")
    before = len(draft.open)
    out = json.loads(fix_draft(ctx, q["key"], "room_type", "Production hall"))
    assert out["changed"] == len(q["ids"]) and q["key"] in out["answered"]
    assert out["open_questions"] == before - 1
    space = draft.state.base.spaces[q["ids"][0]]
    assert space.room_type == "production_hall" and space.name.startswith("Production hall")
    saved = json.loads((draft.folder / "building.json").read_text())
    assert not any(g["element_id"] == q["ids"][0] and g["field"] == "room_type" for g in saved["guesses"])
    floor = draft.state.base.floors[space.floor_id].name
    assert "Production hall" in list_rooms(ctx, floor, "production hall")  # the agent's tools read the fix


def test_drafting_door_widths_change_forklift_routes():
    draft = _png_draft()
    ctx = _ctx(draft)
    room = next(s.id for s in draft.state.base.spaces.values() if s.type == "room")
    assert not json.loads(route(ctx, "outside", room, min_width_m=1.5))["reachable"]  # 0.9 m entrances
    fix_draft(ctx, "all doors", "width_m", "1.6 m")
    assert json.loads(route(ctx, "outside", room, min_width_m=1.5))["total_length_m"] > 0
    assert not any(q["field"] == "width_m" for q in draft.open)


def test_drafting_storey_height_restacks_floors():
    draft = _png_draft()
    fix_draft(_ctx(draft), "all floors", "height_m", "4.5")
    levels = sorted(draft.state.base.floors.values(), key=lambda f: f.level)
    assert [f.elevation_m for f in levels] == [0.0, 4.5]


def test_drafting_rejects_nonsense_and_keeps_the_building():
    draft = _png_draft()
    ctx = _ctx(draft)
    with pytest.raises(ModelRetry):
        fix_draft(ctx, "all doors", "width_m", "wide")
    with pytest.raises(ModelRetry):
        fix_draft(ctx, "all floors", "room_type", "office")  # a floor has no room type
    assert all(o.width_m == 0.9 for o in draft.state.base.openings.values() if o.kind == "door")


def test_drafting_accept_the_rest_and_reload_from_disk():
    draft = _png_draft()
    accept_guesses(_ctx(draft))
    assert not draft.open
    drafting._drafts.clear()
    again = drafting.get_draft(draft.id)
    assert again is not None and not again.open and again.state.base.name == "Hall 7"


def test_drafting_building_tool_switches_the_conversation():
    ctx = SimpleNamespace(deps=Deps(_png_draft().state))
    out = json.loads(draft_building(ctx, str(FACTORY), "Factory from SVG"))
    assert out["building_id"].startswith("factory-from-svg") and out["floors"] == 2
    assert ctx.deps.building.name == "Factory from SVG"
    with pytest.raises(ModelRetry):
        draft_building(ctx, "/nowhere/plans", "x")


def test_drafting_upload_endpoint_lists_and_serves_the_scene():
    client = TestClient(main.app)
    files = [
        ("plans", (p.name, p.read_bytes(), "image/svg+xml"))
        for p in sorted(FACTORY.glob("floor_*.svg"))
        if "_plain" not in p.name
    ]
    res = client.post("/buildings/draft", files=files, data={"name": "Uploaded factory"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["floors"] == 2 and body["rooms"] > 0 and body["seconds"] < 30
    listed = client.get("/buildings", params={"split": "draft"}).json()
    assert [b["id"] for b in listed] == [body["building_id"]]
    scene = client.get(f"/buildings/{body['building_id']}/scene").json()
    assert len(scene["floors"]) == 2
    bad = client.post("/buildings/draft", files=[("plans", ("plan.pdf", b"%PDF", "application/pdf"))])
    assert bad.status_code == 400


def test_drafting_unknown_building_has_nothing_to_review():
    from app.building.model import load_building
    from app.building.state import BuildingState

    with pytest.raises(ModelRetry):
        review_draft(SimpleNamespace(deps=Deps(BuildingState(load_building("proc-factory-dev-003")))))
