"""Level 4 by conversation: draft a building from floor plans, then settle what the plans don't say by talking.

`app.building.build.draft_from_plans` (Owner B) turns plans into a kit-format building.json in seconds and
lists every value it had to guess (room types from the furniture or the area, item types from footprints,
staircases added so every floor can be reached, default door widths and storey heights). Here that list
becomes a short set of questions, grouped so one answer settles many rooms ("the ground floor is all
production halls"), most uncertain first. The agent asks them (`review_draft`), applies the answers
(`fix_draft`, `accept_guesses`) and the draft is usable at once: every other tool routes and counts on it,
the map shows it, and each change is saved to data/private/drafts/<id>/building.json.

    draft = create_draft([Path("floor_0.svg"), Path("floor_1.svg")], name="Hall 7")
    draft.state  # a BuildingState like any other: the API serves it, the agent talks about it
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, get_args

from pydantic_ai import ModelRetry, RunContext

from app.building.build import draft_from_plans, validate
from app.building.model import Building, SpaceType, parse_building
from app.building.state import BuildingState
from app.settings import get_settings

from .tools import Deps, _item_of, _json, _key, element_of, floor_of

CHECK_BELOW = 0.9
"""Guesses this sure or surer are taken as they are; the rest become questions."""
SHOW_QUESTIONS = 2
"""Questions per review_draft call: enough to keep going, few enough to ask one at a time."""

FIELDS: dict[str, set[str]] = {
    "spaces": {"name", "ref", "room_type", "type"},
    "openings": {"width_m", "step_free", "kind"},
    "floors": {"name", "height_m", "elevation_m"},
    "assets": {"name", "type", "category"},
    "vertical_connectors": {"name", "kind", "step_free", "remove"},
    "building": {"name"},
}
NUMBER_FIELDS = {"width_m", "height_m", "elevation_m"}
FLAG_FIELDS = {"step_free", "remove"}
TYPE_FIELDS = {"room_type", "type", "kind", "category"}


def drafts_dir() -> Path:
    """Drafts may come from a partner's real plans: they live in the gitignored data/private/."""
    return get_settings().data_dir / "private" / "drafts"


class Draft:
    """A building drafted from plans, the questions about it, and the live state the app and agent use."""

    def __init__(self, raw: dict[str, Any], questions: list[dict[str, Any]], folder: Path) -> None:
        self.raw = raw
        self.questions = questions
        self.folder = folder
        self.state = BuildingState(parse_building(raw))

    @property
    def id(self) -> str:
        return self.raw["building"]["id"]

    @property
    def open(self) -> list[dict[str, Any]]:
        return [q for q in self.questions if q["status"] == "open"]

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "building.json").write_text(json.dumps(self.raw, ensure_ascii=False, indent=1))
        (self.folder / "questions.json").write_text(json.dumps(self.questions, ensure_ascii=False, indent=1))

    def rebuild(self) -> None:
        """Swap the corrected plan in under the live state: closures and moves already recorded stay."""
        self.state.base = parse_building(self.raw)
        self.state._views.clear()  # views are cached per edit, not per base
        self.save()

    @classmethod
    def load(cls, folder: Path) -> Draft:
        raw = json.loads((folder / "building.json").read_text(encoding="utf-8"))
        questions = json.loads((folder / "questions.json").read_text(encoding="utf-8"))
        return cls(raw, questions, folder)


_drafts: dict[str, Draft] = {}


def get_draft(building_id: str) -> Draft | None:
    """A draft by id: made in this process, or saved earlier in data/private/drafts/."""
    if building_id not in _drafts and (drafts_dir() / building_id / "building.json").exists():
        _drafts[building_id] = Draft.load(drafts_dir() / building_id)
    return _drafts.get(building_id)


def list_drafts() -> list[Draft]:
    root = drafts_dir()
    ids = sorted(p.parent.name for p in root.glob("*/building.json")) if root.exists() else []
    return [d for i in ids if (d := get_draft(i))]


def draft_of(state: BuildingState) -> Draft | None:
    """The draft behind a conversation's state, if the building is one."""
    draft = get_draft(state.base.id)
    return draft if draft is not None and draft.state is state else None


def create_draft(
    plans: list[Path], *, name: str, levels: list[int] | None = None, root: Path | None = None
) -> Draft:
    """Plans (the kit's floor_<level>.svg or .png) -> a saved draft with its questions. Seconds, not days.
    `root`: where to save it (default data/private/drafts/; evals pass a temporary folder)."""
    raw = draft_from_plans(plans, name=name, levels=levels)
    errors = [i for i in validate(raw) if i.severity == "error"]
    if errors:
        raise ValueError("the plans gave an invalid building: " + "; ".join(i.message for i in errors[:5]))
    base_id = raw["building"]["id"]
    building_id, n = base_id, 1
    while get_draft(building_id) is not None:
        n += 1
        building_id = f"{base_id}-{n}"
    if building_id != base_id:
        raw = json.loads(json.dumps(raw).replace(f'"{base_id}-', f'"{building_id}-'))
        raw["building"]["id"] = building_id
    folder = (root or drafts_dir()) / building_id
    (folder / "plans").mkdir(parents=True, exist_ok=True)
    for p in plans:
        if p.resolve().parent != (folder / "plans").resolve():
            shutil.copy(p, folder / "plans" / p.name)
    raw["building"]["drafted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    draft = Draft(raw, _questions(raw), folder)
    draft.save()
    _drafts[building_id] = draft
    return draft


# ---- the questions: the draft's guesses, grouped, most uncertain first -----------------------------------


def _title(value: str) -> str:
    return str(value).replace("_", " ")


def _questions(raw: dict[str, Any]) -> list[dict[str, Any]]:
    spaces = {s["id"]: s for s in raw["spaces"]}
    assets = {a["id"]: a for a in raw["assets"]}
    floors = {f["id"]: f for f in raw["floors"]}
    connectors = {c["id"]: c for c in raw["vertical_connectors"]}
    out: list[dict[str, Any]] = []

    def add(about: str, field: str, value: Any, ids: list[str], source: str, sure: float | None, where=()):
        out.append({
            "key": f"q{len(out) + 1}", "about": about, "field": field, "guess": value, "ids": ids,
            "source": source, "confidence": sure, "highlight": sorted(set(where)), "status": "open",
        })  # fmt: skip

    guesses = raw.get("guesses", [])
    for g in guesses:
        if g["source"] == "reachability" and (c := connectors.get(g["element_id"])):
            joined = ", ".join(spaces[s]["name"] or s for s in c["space_ids"])
            add(
                f"No stairs or lift drawn joins these floors, so a {c['kind']} was added between {joined}. "
                f"Is there one there (remove it if not)?",
                "remove", False, [c["id"]], "reachability", None, c["space_ids"],
            )  # fmt: skip
    unsure = [g for g in guesses if g["confidence"] is not None and g["confidence"] < CHECK_BELOW]
    rooms: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for g in unsure:
        if g["field"] == "room_type" and g["element_id"] in spaces:
            rooms[(spaces[g["element_id"]]["floor_id"], g["value"])].append(g)
        elif g["field"] == "type" and g["element_id"] in assets:
            items[g["value"]].append(g)
    groups = [(min(x["confidence"] for x in gs), "room", key, gs) for key, gs in rooms.items()] + [
        (min(x["confidence"] for x in gs), "item", key, gs) for key, gs in items.items()
    ]
    for sure, kind, key, gs in sorted(groups, key=lambda t: (t[0], -len(t[3]))):
        ids = [g["element_id"] for g in gs]
        if kind == "room":
            floor_id, value = key
            add(
                f"{len(ids)} room(s) on {floors[floor_id]['name']} look like {_title(value)} "
                f"({sure:.0%} sure), from {gs[0]['source']}. Is that what they are?",
                "room_type", value, ids, gs[0]["source"], round(sure, 2), ids,
            )  # fmt: skip
        else:
            add(
                f"{len(ids)} item(s) look like {_title(key)} from their size ({sure:.0%} sure). Right?",
                "type", key, ids, "footprint", round(sure, 2), [assets[i]["space_id"] for i in ids],
            )  # fmt: skip
    doors = [o["id"] for o in raw["openings"] if o["kind"] in ("door", "entrance")]
    by_field = {g["field"]: g for g in guesses if g["element_id"] is None}
    if doors and "openings.width_m" in by_field:
        add(
            f"Door widths aren't in the plans: all {len(doors)} doors are taken as "
            f"{by_field['openings.width_m']['value']} m wide. Any wider (for forklifts, beds, pallets)?",
            "width_m", by_field["openings.width_m"]["value"], doors, "default", None,
        )  # fmt: skip
    if doors and "openings.step_free" in by_field:
        add(
            "The plans don't show steps or thresholds: every door is taken as step-free. Any with a step?",
            "step_free", True, doors, "default", None,
        )  # fmt: skip
    heights = {g["value"] for g in guesses if g["field"] == "height_m"}
    if heights:
        add(
            f"Storey heights aren't in the plans: every floor is taken as {', '.join(map(str, heights))} m "
            "high (this sets stair and lift lengths).",
            "height_m", next(iter(heights)), sorted(floors), "default", None,
        )  # fmt: skip

    return out


# ---- applying answers ----------------------------------------------------------------------------------


def _collection(raw: dict[str, Any], element_id: str) -> str | None:
    for name in ("spaces", "openings", "floors", "assets", "vertical_connectors"):
        if any(e["id"] == element_id for e in raw[name]):
            return name
    return None


def _targets(draft: Draft, target: str, field: str) -> list[str]:
    """Element ids for what the person named: a question key (q3), "all doors", "all floors", "all rooms
    on <floor>", "the building", or one room, door, floor, staircase, lift or item by name or id."""
    b: Building = draft.state.base
    text = target.strip()
    low = re.sub(r"^(the|all( the)?|every)\s+", "", text.casefold())
    if q := next((q for q in draft.questions if q["key"] == text.casefold()), None):
        return q["ids"]
    if low in ("building", "whole building") or (
        field == "name" and low == draft.raw["building"]["name"].casefold()
    ):
        return ["building"]
    if low in ("doors", "door", "doors and entrances"):
        return [o.id for o in b.openings.values() if o.kind in ("door", "entrance")]
    if low in ("entrances", "entrance doors"):
        return [o.id for o in b.openings.values() if o.kind == "entrance"]
    if low in ("floors", "storeys", "levels"):
        return list(b.floors)
    if m := re.fullmatch(r"(?:rooms|spaces) (?:on|of) (.+)", low):
        floor_id = floor_of(b, m[1]).id
        return [s.id for s in b.spaces.values() if s.floor_id == floor_id and s.type == "room"]
    for name in ("spaces", "openings", "floors", "assets", "vertical_connectors"):
        if any(e["id"] == text for e in draft.raw[name]):
            return [text]
    if field in FIELDS["floors"] and field not in FIELDS["spaces"] | FIELDS["assets"]:
        return [floor_of(b, text).id]
    if field in ("width_m", "step_free") and text in b.openings:
        return [text]
    if field in FIELDS["assets"] and (asset := _item_of(b, text)):
        return [asset.id]
    found = element_of(b, text)
    return [found]


def _value(field: str, value: str) -> Any:
    if field in NUMBER_FIELDS:
        numbers = re.findall(r"-?\d+(?:[.,]\d+)?", value)
        if not numbers:
            raise ModelRetry(f"{field} needs a number in metres, got {value!r}.")
        metres = float(numbers[0].replace(",", "."))
        return metres / 100 if "cm" in value and metres > 10 else metres
    if field in FLAG_FIELDS:
        word = value.strip().casefold()
        if word in ("true", "yes", "1", "y"):
            return True
        if word in ("false", "no", "0", "n"):
            return False
        raise ModelRetry(f"{field} is yes or no, got {value!r}.")
    if field in TYPE_FIELDS:
        return _key(value)
    return value.strip()


def _restack(raw: dict[str, Any]) -> None:
    """Elevations follow the storey heights: ground floor at 0, each floor on top of the one below."""
    floors = sorted(raw["floors"], key=lambda f: f["level"])
    ground = next((i for i, f in enumerate(floors) if f["level"] >= 0), len(floors))
    for i in range(ground, len(floors)):
        floors[i]["elevation_m"] = (
            0.0 if i == ground else floors[i - 1]["elevation_m"] + floors[i - 1]["height_m"]
        )
    for i in range(ground - 1, -1, -1):
        floors[i]["elevation_m"] = floors[i + 1]["elevation_m"] - floors[i]["height_m"]


def _set(raw: dict[str, Any], element_id: str, field: str, value: Any) -> str:
    """One field of one element; returns what changed, in words."""
    if element_id == "building":
        old, raw["building"]["name"] = raw["building"]["name"], value
        return f"building renamed {old!r} -> {value!r}"
    name = _collection(raw, element_id)
    if name is None:
        raise ModelRetry(f"No element {element_id!r} in this draft.")
    if field not in FIELDS[name]:
        kind = name.rstrip("s").replace("_", " ")
        raise ModelRetry(f"A {kind} has no {field!r} to set; it has: {', '.join(sorted(FIELDS[name]))}.")
    if name == "vertical_connectors" and field == "remove":
        if value:
            raw[name] = [c for c in raw[name] if c["id"] != element_id]
            return f"{element_id} removed"
        return f"{element_id} kept"
    element = next(e for e in raw[name] if e["id"] == element_id)
    if name == "spaces" and field == "type" and value not in get_args(SpaceType):
        raise ModelRetry(
            f"A space's type is one of {', '.join(get_args(SpaceType))}; set room_type for what it is."
        )
    old = element[field]
    element[field] = value
    if (
        name == "spaces"
        and field == "room_type"
        and element["name"].casefold().startswith(_title(old).casefold())
    ):
        element["name"] = _title(value).capitalize() + element["name"][len(_title(old)) :]  # "Bedroom 0-003"
    if name == "assets" and field == "type" and element["name"].casefold().startswith(_title(old).casefold()):
        element["name"] = _title(value).capitalize() + element["name"][len(_title(old)) :]
    return f"{element.get('name') or element_id}: {field} {old} -> {value}"


def _settle(draft: Draft, ids: set[str], field: str) -> list[str]:
    """Close the questions an answer covers; drop the matching guesses from the saved building."""
    settled = []
    for q in draft.open:
        if q["field"] == field and set(q["ids"]) <= ids:
            q["status"] = "answered"
            settled.append(q["key"])
    draft.raw["guesses"] = [
        g
        for g in draft.raw.get("guesses", [])
        if not (g["element_id"] in ids and g["field"].split(".")[-1] == field)
        and not (g["element_id"] is None and g["field"] in (f"openings.{field}",) and ids >= _door_ids(draft))
    ]
    return settled


def _door_ids(draft: Draft) -> set[str]:
    return {o["id"] for o in draft.raw["openings"] if o["kind"] in ("door", "entrance")}


def _draft(ctx: RunContext[Deps]) -> Draft:
    draft = draft_of(ctx.deps.state)
    if draft is None:
        raise ModelRetry("This building wasn't drafted from plans in this app; there is nothing to review.")
    return draft


def _status(draft: Draft) -> dict[str, Any]:
    b = draft.state.base
    return {
        "building_id": draft.id,
        "name": b.name,
        "floors": len(b.floors),
        "rooms": sum(s.type == "room" for s in b.spaces.values()),
        "spaces": len(b.spaces),
        "doors": len(b.openings),
        "stairs_and_lifts": len(b.connectors),
        "items": len(b.assets),
        "open_questions": len(draft.open),
    }


# ---- tools ---------------------------------------------------------------------------------------------


def draft_building(ctx: RunContext[Deps], plans: str, name: str) -> str:
    """Create a new building from floor plans already on the server, and switch this conversation to it.
    `plans`: a folder holding floor_<level>.svg (or .png) files, or file paths separated by commas. `name`:
    what the person calls the building. Returns what was drafted and how many questions are open; then
    walk the person through them with review_draft. (Plans uploaded in the app are drafted there already.)"""
    paths: list[Path] = []
    for part in [p.strip() for p in plans.split(",") if p.strip()]:
        p = Path(part).expanduser()
        if p.is_dir():
            found = sorted(f for f in p.glob("floor_*.svg") if "_plain" not in f.name) or sorted(
                p.glob("*.png")
            )
            paths += found
        elif p.exists():
            paths.append(p)
        else:
            raise ModelRetry(f"No file or folder {part!r} on the server. Ask the person where the plans are.")
    if not paths:
        raise ModelRetry(f"No floor plans (floor_<level>.svg or .png) in {plans!r}.")
    try:
        draft = create_draft(paths, name=name)
    except (ValueError, NotImplementedError) as e:
        raise ModelRetry(f"Couldn't draft from {plans!r}: {e}") from None
    ctx.deps.state = draft.state
    ctx.deps.pending = None
    ctx.deps.here = None
    return _json({**_status(draft), "plans": [p.name for p in paths], "next": "review_draft"})


def review_draft(ctx: RunContext[Deps]) -> str:
    """What the drafted building's plans didn't say: the next open questions, most uncertain first, each
    with a `key`, what was guessed and the rooms to `highlight`. Ask the person about the first one or two,
    in plain words (never the keys or ids), then record each answer with fix_draft or accept_guesses.
    Also gives the draft's size (floors, rooms, doors, stairs and lifts, items)."""
    draft = _draft(ctx)
    shown = [
        {k: q[k] for k in ("key", "about", "field", "guess", "confidence")}
        | {"elements": len(q["ids"]), "highlight": q["highlight"][:40]}
        for q in draft.open[:SHOW_QUESTIONS]
    ]
    first = draft.open[0]["highlight"][:40] if draft.open else []
    more = max(0, len(draft.open) - SHOW_QUESTIONS)
    return _json({**_status(draft), "questions": shown, "more_after_these": more, "highlight": first})


def fix_draft(ctx: RunContext[Deps], target: str, field: str, value: str) -> str:
    """Correct the drafted building with what the person says, and save it. Applies at once (the person
    is telling you how their building is), routes and the map follow.
    `target`: a question key from review_draft (q3) to answer that whole group, "all doors", "entrances",
    "all floors", "rooms on <floor>", "the building", or one room, door, floor, staircase, lift or item.
    `field` and `value`: rooms: room_type (what it is: production_hall, kitchen, office, ...), name, ref,
    type (room|corridor|hall|stair|lift|outdoor); doors: width_m, step_free (yes/no); floors: height_m
    (storey height, elevations follow), name; items: type, name; stairs/lifts: remove (yes), kind, name;
    the building: name."""
    draft = _draft(ctx)
    field = _key(field)
    ids = _targets(draft, target, field)
    before = copy.deepcopy(draft.raw)
    parsed = _value(field, value)
    changed = [_set(draft.raw, i, field, parsed) for i in ids]
    if field == "height_m":
        _restack(draft.raw)
    errors = [i.message for i in validate(draft.raw) if i.severity == "error"]
    if errors:
        draft.raw = before
        raise ModelRetry(f"That would break the building: {'; '.join(errors[:3])}. Nothing was changed.")
    settled = _settle(draft, set(ids), field)
    draft.rebuild()
    return _json({
        "changed": len(changed), "examples": changed[:5], "answered": settled,
        "highlight": [i for i in ids if i in draft.state.base.spaces][:40],
        "open_questions": len(draft.open),
    })  # fmt: skip


def accept_guesses(ctx: RunContext[Deps], keys: list[str] | None = None) -> str:
    """The person says guesses are right ("yes, those are offices", "the rest is fine"): close those
    questions (keys from review_draft) as they are; no keys = every open question. Saved."""
    draft = _draft(ctx)
    wanted = {k.casefold() for k in keys} if keys else None
    accepted = []
    for q in draft.open:
        if wanted is None or q["key"] in wanted:
            q["status"] = "accepted"
            accepted.append(q["key"])
    if wanted and not accepted:
        raise ModelRetry(f"No open question {sorted(wanted)}; open: {[q['key'] for q in draft.open]}.")
    draft.save()
    return _json({"accepted": accepted, "open_questions": len(draft.open)})


DRAFT_TOOLS = [draft_building, review_draft, fix_draft, accept_guesses]
