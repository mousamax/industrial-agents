"""Generate the hand-designed multi-building plans in data/multi_building/ (kit building.json schema).

Run: uv run python data/multi_building/_build.py

- xmb-bridge-works: two halls on one plan, joined on the ground floor by an open yard (an `outdoor` space)
  and on the first floor by a pedestrian bridge. The kit's route rule has no free way round the outside, so
  "going outside" between the halls means crossing the yard space, door to door like any other space.
- xmb-u-wing: a U-shaped institute building whose courtyard (`outdoor`) cuts across the U on the ground
  floor; the inside way goes round the base of the U.
- xmb-tour-campus: a Level 3 site (site.json) of three kit buildings and three outdoor walkways.

Every id keeps the building id as prefix, like the kit. Geometry is in metres; rectangles are (x0, y0, x1, y1).
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent


class Plan:
    def __init__(self, building_id: str, name: str, floors: list[tuple[int, str, float, float]]) -> None:
        self.id = building_id
        self.raw: dict = {
            "building": {
                "id": building_id,
                "name": name,
                "source": "procedural",
                "source_ref": {"generator": "hand-designed", "script": "data/multi_building/_build.py"},
                "license": "CC0-1.0",
                "attribution": "Hand-designed test plan for the Synera track (CC0)",
                "split": "dev",
                "units": "m",
            },
            "floors": [
                {"id": self.floor(level), "level": level, "name": fname, "elevation_m": elev, "height_m": h}
                for level, fname, elev, h in floors
            ],
            "spaces": [],
            "openings": [],
            "vertical_connectors": [],
            "assets": [],
        }
        self._by_name: dict[tuple[int, str], str] = {}

    def floor(self, level: int) -> str:
        return f"{self.id}-F{level}"

    def space(self, level: int, type_: str, room_type: str, name: str, ref: str, rect: tuple) -> str:
        n = sum(s["floor_id"] == self.floor(level) for s in self.raw["spaces"]) + 1
        sid = f"{self.floor(level)}-S{n:03d}"
        x0, y0, x1, y1 = rect
        self.raw["spaces"].append(
            {
                "id": sid,
                "floor_id": self.floor(level),
                "type": type_,
                "room_type": room_type,
                "name": name,
                "ref": ref,
                "polygon": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                "synthetic": False,
            }
        )
        self._by_name[(level, ref)] = sid
        return sid

    def door(self, level: int, a: str, b: str, at: tuple, width: float, step_free: bool = True, kind=None):
        n = sum(o["floor_id"] == self.floor(level) for o in self.raw["openings"]) + 1
        ids = [self._by_name[(level, r)] if r != "outside" else "outside" for r in (a, b)]
        kind = kind or ("entrance" if "outside" in ids else "door")
        self.raw["openings"].append(
            {
                "id": f"{self.floor(level)}-O{n:04d}",
                "floor_id": self.floor(level),
                "space_ids": ids,
                "kind": kind,
                "position": list(at),
                "width_m": width,
                "step_free": step_free,
                "synthetic": False,
            }
        )

    def connector(self, kind: str, name: str, ref: str, levels: list[int]) -> None:
        """`ref` names the stop on each floor, with {lv} for the level ("STW{lv}")."""
        n = len(self.raw["vertical_connectors"]) + 1
        self.raw["vertical_connectors"].append(
            {
                "id": f"{self.id}-V{n:02d}",
                "kind": kind,
                "name": name,
                "floor_ids": [self.floor(lv) for lv in levels],
                "space_ids": [self._by_name[(lv, ref.format(lv=lv))] for lv in levels],
                "step_free": kind == "lift",
                "synthetic": False,
            }
        )

    def asset(self, level: int, ref: str, type_: str, name: str, at: tuple) -> None:
        n = len(self.raw["assets"]) + 1
        self.raw["assets"].append(
            {
                "id": f"{self.id}-A{n:05d}",
                "name": name,
                "category": "equipment",
                "type": type_,
                "space_id": self._by_name[(level, ref)],
                "floor_id": self.floor(level),
                "position": list(at),
                "footprint": {"width_m": 0.6, "depth_m": 0.6, "rotation_deg": 0.0},
                "source": "generated",
            }
        )

    def write(self) -> None:
        out = HERE / self.id
        out.mkdir(exist_ok=True)
        (out / "building.json").write_text(json.dumps(self.raw, indent=1, ensure_ascii=False) + "\n")


def bridge_works() -> Plan:
    """Hall A (x 0..40) and Hall B (x 60..100); the yard (x 40..60) between them on the ground floor, the
    bridge (x 40..60, y 20..23) on the first floor. Each hall: a goods hall or mezzanine store (y 0..20), a
    corridor (y 20..23) and a row of rooms (y 23..30) with a staircase and a freight lift. Hall A's staircase
    is at its far end, Hall B's at the yard end, so which floor you change on decides yard or bridge."""
    p = Plan(
        "xmb-bridge-works",
        "Werk Nord (Halle A + Halle B)",
        [(0, "Ground floor", 0.0, 4.5), (1, "First floor", 4.5, 4.5)],
    )

    def hall(side: str, x0: float, row: dict[str, tuple[float, float]], ped_door_x: float) -> None:
        """`row`: x range of office, stair, lift and lab along y 23..30; the goods hall spans x0..x0+40."""
        names = {0: {"office": ("room", "office", f"Office {side}0.1", f"{side}0.1"),
                     "lab": ("room", "lab", f"Lab {side}0.4", f"{side}0.4")},
                 1: {"office": ("room", "office", f"Office {side}1.1", f"{side}1.1"),
                     "lab": ("room", "meeting_room", f"Meeting room {side}1.4", f"{side}1.4")}}  # fmt: skip
        for level in (0, 1):
            big = ("hall", "warehouse_zone", f"Goods hall {side}") if level == 0 else (
                "hall", "storage", f"Mezzanine store {side}1")  # fmt: skip
            p.space(level, big[0], big[1], big[2], f"{side}-G{level}", (x0, 0, x0 + 40, 20))
            c = f"{side}-C{level}"
            p.space(level, "corridor", "corridor", f"Corridor {side}{level}", c, (x0, 20, x0 + 40, 23))
            refs = {"stair": f"{side}-ST{level}", "lift": f"{side}-L{level}"}
            for key, (a, b) in row.items():
                if key == "stair":
                    p.space(level, "stair", "stair", f"Staircase {side}", refs[key], (a, 23, b, 30))
                elif key == "lift":
                    p.space(level, "lift", "lift", f"Freight lift {side}", refs[key], (a, 23, b, 30))
                else:
                    t, rt, nm, ref = names[level][key]
                    p.space(level, t, rt, nm, ref, (a, 23, b, 30))
                    refs[key] = ref
            mid = {k: (a + b) / 2 for k, (a, b) in row.items()}
            p.door(level, c, refs["office"], (mid["office"], 23), 1.0)
            p.door(level, c, refs["stair"], (mid["stair"], 23), 1.2)
            p.door(level, c, refs["lift"], (mid["lift"], 23), 3.5)
            p.door(level, c, refs["lab"], (mid["lab"], 23), 1.0)
            p.door(
                level, f"{side}-G{level}", c, (mid["lift"], 20), 4.0, kind="opening"
            )  # forklift way to the lift
            if level == 0:
                p.door(level, f"{side}-G0", c, (ped_door_x, 20), 1.0)  # pedestrian door to the goods hall
        p.connector("stairs", f"Staircase {side}", f"{side}-ST{{lv}}", [0, 1])
        p.connector("lift", f"Freight lift {side}", f"{side}-L{{lv}}", [0, 1])
        outer = x0 if x0 == 0 else x0 + 40
        p.door(0, f"{side}-G0", "outside", (outer, 10), 4.5)  # loading dock to the street

    hall("A", 0, {"office": (0, 10), "stair": (10, 14), "lift": (14, 20), "lab": (20, 40)}, ped_door_x=5)
    hall("B", 60, {"stair": (60, 64), "lab": (64, 80), "lift": (80, 86), "office": (86, 100)}, ped_door_x=95)
    p.space(0, "outdoor", "yard", "Yard", "YARD", (40, 0, 60, 30))
    p.space(1, "corridor", "bridge", "Bridge A-B", "BRIDGE", (40, 20, 60, 23))
    # Yard: truck gates from the goods halls (wide, level), pedestrian doors from the corridors.
    p.door(0, "A-G0", "YARD", (40, 3), 4.5, kind="opening")
    p.door(0, "B-G0", "YARD", (60, 3), 4.5, kind="opening")
    p.door(0, "A-C0", "YARD", (40, 21.5), 1.0, step_free=False)  # two steps down into the yard
    p.door(0, "B-C0", "YARD", (60, 21.5), 1.0)
    p.door(0, "YARD", "outside", (50, 30), 6.0)  # yard gate to the street
    # Bridge: a pedestrian link, 1.4 m doors at both ends.
    p.door(1, "A-C1", "BRIDGE", (40, 21.5), 1.4)
    p.door(1, "B-C1", "BRIDGE", (60, 21.5), 1.4)
    p.asset(0, "B0.4", "coffee_machine", "Coffee machine B0.4", (62, 28))
    p.asset(1, "A1.4", "projector", "Projector A1.4", (30, 27))
    return p


def u_wing() -> Plan:
    """A U open to the north: west wing (x 0..12), base (y 0..12), east wing (x 40..52), 50 m deep. The
    courtyard (x 12..40, y 12..50) has doors from both wing corridors on the ground floor only."""
    p = Plan(
        "xmb-u-wing",
        "Institut U-Bau",
        [(0, "Ground floor", 0.0, 3.5), (1, "First floor", 3.5, 3.5)],
    )
    for lv in (0, 1):
        p.space(lv, "corridor", "corridor", f"West corridor {lv}", f"WC{lv}", (9, 12, 12, 50))
        p.space(lv, "corridor", "corridor", f"East corridor {lv}", f"EC{lv}", (40, 12, 43, 50))
        p.space(lv, "room", "seminar_room", f"Seminar room W{lv}.1", f"W{lv}.1", (0, 41, 9, 50))
        p.space(lv, "stair", "stair", "Staircase W", f"STW{lv}", (0, 35, 9, 41))
        p.space(lv, "room", "office", f"Office W{lv}.3", f"W{lv}.3", (0, 12, 9, 35))
        p.space(lv, "room", "seminar_room", f"Seminar room E{lv}.1", f"E{lv}.1", (43, 41, 52, 50))
        p.space(lv, "stair", "stair", "Staircase E", f"STE{lv}", (43, 35, 52, 41))
        p.space(lv, "room", "office", f"Office E{lv}.3", f"E{lv}.3", (43, 12, 52, 35))
        p.space(lv, "room", "office", f"Office S{lv}.1", f"S{lv}.1", (0, 0, 9, 12))
        p.space(lv, "room", "kitchen" if lv == 0 else "library", "Kitchen S0.2" if lv == 0 else "Library S1.2",
                f"S{lv}.2", (9, 0, 26, 9))  # fmt: skip
        p.space(lv, "lift", "lift", "Lift", f"LIFT{lv}", (26, 0, 30, 9))
        p.space(lv, "room", "lab", f"Lab S{lv}.4", f"S{lv}.4", (30, 0, 43, 9))
        p.space(lv, "room", "office", f"Office S{lv}.5", f"S{lv}.5", (43, 0, 52, 12))
        if lv == 0:  # a fire door splits the ground-floor base corridor
            p.space(lv, "corridor", "corridor", "South corridor 0 west", "SCW0", (9, 9, 26, 12))
            p.space(lv, "corridor", "corridor", "South corridor 0 east", "SCE0", (26, 9, 43, 12))
            p.door(0, "SCW0", "SCE0", (26, 10.5), 0.9)
            scw, sce = "SCW0", "SCE0"
        else:
            p.space(lv, "corridor", "corridor", "South corridor 1", "SC1", (9, 9, 43, 12))
            scw = sce = "SC1"
        p.door(lv, f"WC{lv}", scw, (10.5, 12), 3.0, kind="opening")
        p.door(lv, f"EC{lv}", sce, (41.5, 12), 3.0, kind="opening")
        for ref, at, side in ((f"W{lv}.1", (9, 45), "W"), (f"STW{lv}", (9, 38), "W"), (f"W{lv}.3", (9, 24), "W"),
                              (f"E{lv}.1", (43, 45), "E"), (f"STE{lv}", (43, 38), "E"), (f"E{lv}.3", (43, 24), "E")):  # fmt: skip
            p.door(lv, f"{side}C{lv}", ref, at, 1.2)
        p.door(lv, scw, f"S{lv}.1", (9, 10.5), 1.2)
        p.door(lv, scw, f"S{lv}.2", (17, 9), 1.2)
        p.door(lv, sce, f"LIFT{lv}", (28, 9), 1.2)
        p.door(lv, sce, f"S{lv}.4", (36, 9), 1.2)
        p.door(lv, sce, f"S{lv}.5", (43, 10.5), 1.2)
    p.space(0, "outdoor", "courtyard", "Courtyard", "COURT", (12, 12, 40, 50))
    p.door(0, "WC0", "COURT", (12, 45), 1.0, step_free=False)  # old door with a step
    p.door(0, "WC0", "COURT", (12, 20), 1.2)  # ramp door
    p.door(0, "EC0", "COURT", (40, 45), 1.8)
    p.door(0, "WC0", "outside", (10.5, 50), 1.8)  # main entrance at the tip of the west wing
    p.door(0, "COURT", "outside", (26, 50), 4.0)  # courtyard open to the street
    p.connector("stairs", "Staircase W", "STW{lv}", [0, 1])
    p.connector("stairs", "Staircase E", "STE{lv}", [0, 1])
    p.connector("lift", "Lift", "LIFT{lv}", [0, 1])
    p.asset(0, "S0.2", "coffee_machine", "Coffee machine S0.2", (12, 3))
    p.asset(1, "E1.3", "printer", "Printer E1.3", (50, 20))
    return p


def tour_campus() -> None:
    """Level 3 site: Hörsaalzentrum 024 and Laborgebäude 025 (campus) either side of Produktion West 29. The
    direct path between the campus buildings joins their stepped side entrances; the step-free way goes
    through the factory. Produktion West 29 because its office door and hall gates are joined without
    passing a production bay (in most kit factories they aren't). Lengths are ours."""
    site = {
        "site": {
            "id": "xmb-tour-campus",
            "name": "Tour campus: Hörsaalzentrum 024, Produktion West 29, Laborgebäude 025",
            "note": "Hand-designed Level 3 test site for multi-stop tours (data/multi_building/README.md).",
        },
        "buildings": ["proc-campus-dev-000", "proc-factory-dev-005", "proc-campus-dev-001"],
        "walkways": [
            {"id": "xmb-tour-campus-W1", "name": "Covered walkway", "from": "proc-campus-dev-000-F0-O0028",
             "to": "proc-factory-dev-005-F0-O0063", "length_m": 120.0, "step_free": True, "width_m": 2.5},
            {"id": "xmb-tour-campus-W2", "name": "Factory road", "from": "proc-factory-dev-005-F0-O0021",
             "to": "proc-campus-dev-001-F0-O0022", "length_m": 90.0, "step_free": True, "width_m": 6.0},
            {"id": "xmb-tour-campus-W3", "name": "Garden path", "from": "proc-campus-dev-000-F0-O0029",
             "to": "proc-campus-dev-001-F0-O0023", "length_m": 140.0, "step_free": True, "width_m": 1.5},
        ],
    }  # fmt: skip
    out = HERE / "xmb-tour-campus"
    out.mkdir(exist_ok=True)
    (out / "site.json").write_text(json.dumps(site, indent=1, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    from app.building.build import validate

    for plan in (bridge_works(), u_wing()):
        issues = validate(plan.raw)
        for issue in issues:
            print(plan.id, issue)
        plan.write()
    tour_campus()
    print(
        "wrote",
        ", ".join(sorted(p.name for p in HERE.iterdir() if p.is_dir() and not p.name.startswith("_"))),
    )
