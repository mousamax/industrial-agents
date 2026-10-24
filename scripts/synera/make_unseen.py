"""Make the unseen-kinds suites: question kinds Synera's sets don't have, with answers computed by our code.

They stand in for what the hidden checks and the Sunday building might ask beyond the 8 known templates:
derived numbers (differences, counts within a distance), other scopes (rooms and halls), comparisons
(which floor has most), route properties (narrowest door), varied ways of naming places and floors.

- evals/datasets/unseen_dev.jsonl: dev buildings. Read failures here and improve the agent.
- evals/datasets/unseen_holdout.jsonl: test buildings (their plans, no answers needed). Only ever look at
  the score, never at single failures, so it stays an honest estimate of how we do on unseen questions.

    uv run python scripts/synera/make_unseen.py --per-kind 8
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Callable
from typing import Annotated

import typer

from app.agents.building_guide.bench import DATASETS
from app.agents.building_guide.graph import route_graph
from app.agents.building_guide.tools import _label, is_room
from app.building import geometry
from app.building.model import OUTSIDE, Building, Floor, Space, load_building
from app.building.routing import WALKING, Constraints
from app.settings import get_settings

NUMBER = "Finish with a line 'Answer: <number>'."
ROOM = "Finish with a line 'Answer: <room name or number>'."
FLOOR = "Finish with a line 'Answer: <floor name>'."


def num(value: float, rel: float = 0.0, abs_: float = 0.0) -> dict:
    return {"type": "number", "value": round(value, 2), "relative_tolerance": rel, "absolute_tolerance": abs_}


def space_answer(b: Building, right: Space, wrong: list[Space]) -> dict:
    def names(s: Space) -> list[str]:
        return [
            n for n in {s.name, s.ref} if n and sum(x.ref == n or x.name == n for x in b.spaces.values()) == 1
        ]

    return {
        "type": "space",
        "accept": names(right) or [s.name for s in [right]],
        "reject": [n for w in wrong for n in names(w)],
    }


class Gen:
    """Phrasing helpers: places and floors named in varied, but always unambiguous, ways."""

    def __init__(self, b: Building, rng: random.Random) -> None:
        self.b, self.rng = b, rng
        refs = Counter(s.ref for s in b.spaces.values() if s.ref)
        labels = Counter(_label(b, s.id) for s in b.spaces.values())
        self.unique_ref = {
            s.id for s in b.spaces.values() if s.ref and refs[s.ref] == 1 and s.ref not in s.name.split()
        }
        self.unique_label = {s.id for s in b.spaces.values() if labels[_label(b, s.id)] == 1}

    def nameable(self, s: Space) -> bool:
        return s.id in self.unique_label

    def place(self, s: Space) -> str:
        label = _label(self.b, s.id)
        styles = [label, label.casefold()]
        if s.ref and sum(x.ref == s.ref for x in self.b.spaces.values()) == 1:
            styles.append(f"room {s.ref}")
        return self.rng.choice(styles)

    def floor(self, f: Floor) -> str:
        options = [f.name, f.name.casefold(), f"level {f.level}"]
        if f.level == 0:
            options.append("the ground floor")
        return self.rng.choice(options)


def rooms_on(b: Building, f: Floor, g: Gen) -> list[Space]:
    return [s for s in b.spaces_on(f.id) if is_room(s) and g.nameable(s)]


# ---- kinds: each returns (question, answer) or None when this building can't pose it well ---------------

Kind = Callable[[Building, Gen], tuple[str, dict] | None]


def count_rooms_of_type(b: Building, g: Gen):
    f = g.rng.choice(b.floors_by_level())
    types = Counter(s.room_type for s in b.spaces_on(f.id) if is_room(s))
    types = [t for t, n in types.items() if n >= 2 and t != "generic_room"]
    if not types:
        return None
    t = g.rng.choice(types)
    n = sum(1 for s in b.spaces_on(f.id) if is_room(s) and s.room_type == t)
    q = g.rng.choice(
        [
            f"How many {t.replace('_', ' ')}s are there on {g.floor(f)}?",
            f"Count the {t.replace('_', ' ')} rooms on {g.floor(f)} for me.",
        ]
    )
    return f"{q} {NUMBER}", num(n)


def area_difference(b: Building, g: Gen):
    f = g.rng.choice(b.floors_by_level())
    rooms = rooms_on(b, f, g)
    if len(rooms) < 2:
        return None
    a, c = sorted(g.rng.sample(rooms, 2), key=lambda s: -s.area)
    if a.area - c.area < 2:
        return None
    q = g.rng.choice(
        [
            f"By how many square metres is {g.place(a)} bigger than {g.place(c)}?",
            f"What's the difference in floor area between {g.place(a)} and {g.place(c)}, in m²?",
        ]
    )
    return f"{q} {NUMBER}", num(a.area - c.area, 0.02, 0.1)


def area_rooms_and_halls(b: Building, g: Gen):
    floors = [f for f in b.floors_by_level() if any(s.type == "hall" for s in b.spaces_on(f.id))]
    if not floors:
        return None
    f = g.rng.choice(floors)
    total = sum(
        s.area
        for s in b.spaces_on(f.id)
        if s.type in ("room", "hall") and s.room_type not in geometry.NOT_ROOMS
    )
    q = f"What is the combined floor area of all rooms and halls on {g.floor(f)}, in m²?"
    return f"{q} {NUMBER}", num(total, 0.05)


def floor_with_most(b: Building, g: Gen):
    if len(b.floors) < 2:
        return None
    counts = {f.id: Counter(s.room_type for s in b.spaces_on(f.id) if is_room(s)) for f in b.floors.values()}
    types = {t for c in counts.values() for t in c if t != "generic_room"}
    good = []
    for t in types:
        ranked = sorted(((c[t], fid) for fid, c in counts.items()), reverse=True)
        if ranked[0][0] >= 2 and ranked[0][0] > ranked[1][0]:
            good.append((t, ranked[0][1]))
    if not good:
        return None
    t, fid = g.rng.choice(sorted(good))
    others = [f.name for f in b.floors.values() if f.id != fid]
    q = f"Which floor has the most {t.replace('_', ' ')}s?"
    return f"{q} {FLOOR}", {"type": "space", "accept": [b.floors[fid].name], "reject": others}


def route_difference(b: Building, g: Gen):
    rooms = [s for f in b.floors.values() for s in rooms_on(b, f, g)]
    walk, wheel = route_graph(b, WALKING), route_graph(b, Constraints(step_free=True))
    for _ in range(25):
        a, c = g.rng.sample(rooms, 2)
        r0, r1 = walk.shortest(a.id, c.id), wheel.shortest(a.id, c.id)
        if r0 and r1 and r1.length_m - r0.length_m > 3:
            q = (
                f"I normally take the stairs from {g.place(a)} to {g.place(c)}. How many metres longer is it if "
                "I have to avoid stairs and steps?"
            )
            return f"{q} {NUMBER}", num(r1.length_m - r0.length_m, 0.01, 0.5)
    return None


def narrowest_door(b: Building, g: Gen):
    rooms = [s for f in b.floors.values() for s in rooms_on(b, f, g)]
    walk = route_graph(b, WALKING)
    for _ in range(25):
        a, c = g.rng.sample(rooms, 2)
        r = walk.shortest(a.id, c.id)
        widths = (
            [b.openings[e].width_m for e in dict.fromkeys(r.element_path) if e in b.openings] if r else []
        )
        if len(widths) >= 2:
            q = f"How wide is the narrowest door or opening on the shortest walk from {g.place(a)} to {g.place(c)}, in metres?"
            return f"{q} {NUMBER}", num(min(widths), 0.0, 0.01)
    return None


def nearest_room_type(b: Building, g: Gen):
    rooms = [s for f in b.floors.values() for s in rooms_on(b, f, g)]
    types = [t for t, n in Counter(s.room_type for s in rooms).items() if n >= 2 and t != "generic_room"]
    if not types or not rooms:
        return None
    walk = route_graph(b, WALKING)
    for _ in range(15):
        t, start = g.rng.choice(types), g.rng.choice(rooms)
        d = walk.distances_from([start.id])
        ranked = sorted(
            ((d[s.id], s) for s in rooms if s.room_type == t and s.id != start.id and s.id in d),
            key=lambda x: x[0],
        )
        if len(ranked) >= 2 and ranked[1][0] - ranked[0][0] > 3:
            q = f"I'm in {g.place(start)}. Which {t.replace('_', ' ')} is the shortest walk away?"
            return f"{q} {ROOM}", space_answer(b, ranked[0][1], [ranked[1][1]])
    return None


def within_distance_count(b: Building, g: Gen):
    f = g.rng.choice(b.floors_by_level())
    rooms = rooms_on(b, f, g)
    if len(rooms) < 4:
        return None
    start = g.rng.choice(rooms)
    d = route_graph(b, WALKING).distances_from([start.id])
    walks = sorted(d[s.id] for s in b.spaces_on(f.id) if is_room(s) and s.id in d and s.id != start.id)
    for limit in (20, 30, 40, 50, 75):
        if all(abs(w - limit) > 1.5 for w in walks) and 0 < sum(w <= limit for w in walks) < len(walks):
            n = sum(w <= limit for w in walks)
            q = f"Starting from {g.place(start)}, how many other rooms on that floor can I reach within {limit} m of walking?"
            return f"{q} {NUMBER}", num(n)
    return None


def items_in_room(b: Building, g: Gen):
    options = [
        (s, t, n)
        for s in b.spaces.values()
        if g.nameable(s)
        for t, n in Counter(a.type for a in b.assets_in(s.id)).items()
        if n >= 2
    ]
    if not options:
        return None
    s, t, n = g.rng.choice(options)
    q = f"How many {t.replace('_', ' ')} items are in {g.place(s)}?"
    return f"{q} {NUMBER}", num(n)


def exit_distance(b: Building, g: Gen):
    rooms = [s for f in b.floors.values() for s in rooms_on(b, f, g)]
    if not rooms:
        return None
    s = g.rng.choice(rooms)
    r = route_graph(b, WALKING).shortest(s.id, OUTSIDE)
    if r is None:
        return None
    q = g.rng.choice(
        [
            f"Fire drill: what's the shortest walk from {g.place(s)} out of the building, in metres?",
            f"How far do I have to walk from {g.place(s)} to get outside?",
        ]
    )
    return f"{q} {NUMBER}", num(r.length_m, 0.01, 0.5)


def door_count(b: Building, g: Gen):
    rooms = [s for f in b.floors.values() for s in rooms_on(b, f, g) if len(b.openings_of(s.id)) >= 2]
    if not rooms:
        return None
    s = g.rng.choice(rooms)
    q = f"How many doors or openings does {g.place(s)} have?"
    return f"{q} {NUMBER}", num(len(b.openings_of(s.id)))


def round_trip(b: Building, g: Gen):
    rooms = [s for f in b.floors.values() for s in rooms_on(b, f, g)]
    if len(rooms) < 2:
        return None
    a, c = g.rng.sample(rooms, 2)
    t = route_graph(b, WALKING).tour([a.id, c.id, a.id])
    if t is None:
        return None
    q = f"I walk from {g.place(a)} to {g.place(c)} and straight back. How far is that in total, in metres?"
    return f"{q} {NUMBER}", num(t.length_m, 0.01, 0.5)


KINDS: dict[str, Kind] = {
    f.__name__: f
    for f in [
        count_rooms_of_type,
        area_difference,
        area_rooms_and_halls,
        floor_with_most,
        route_difference,
        narrowest_door,
        nearest_room_type,
        within_distance_count,
        items_in_room,
        exit_distance,
        door_count,
        round_trip,
    ]
}


def make(split: str, per_kind: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    data = get_settings().synera_kit_dir / "data" / split
    buildings = sorted(p.parent.name for p in data.glob("*/building.json"))
    rows = []
    for kind, fn in KINDS.items():
        made, tries = 0, 0
        order = buildings[:]
        rng.shuffle(order)
        while made < per_kind and tries < len(order) * 2:
            bid = order[tries % len(order)]
            tries += 1
            b = load_building(bid)
            got = fn(b, Gen(b, rng))
            if got:
                question, answer = got
                rows.append(
                    {
                        "id": f"{bid}-U{len(rows):03d}",
                        "building_id": bid,
                        "kind": kind,
                        "question": question,
                        "answer": answer,
                    }
                )
                made += 1
    return rows


def main(per_kind: Annotated[int, typer.Option()] = 8, seed: Annotated[int, typer.Option()] = 23) -> None:
    DATASETS.mkdir(parents=True, exist_ok=True)
    for name, split, s in (("unseen_dev", "dev", seed), ("unseen_holdout", "test", seed + 1)):
        rows = make(split, per_kind, s)
        out = DATASETS / f"{name}.jsonl"
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        print(
            f"{len(rows)} questions ({Counter(r['kind'] for r in rows)}) -> {out.relative_to(DATASETS.parents[1])}"
        )


if __name__ == "__main__":
    typer.run(main)
