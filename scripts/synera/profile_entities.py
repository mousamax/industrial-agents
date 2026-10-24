"""Profile every entity type and attribute across all Synera kit buildings."""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from shapely.geometry import Polygon
from shapely.validation import explain_validity

DATA = Path(sys.argv[1])
files = sorted(DATA.glob("*/*/building.json"))
print("buildings:", len(files))

keys = defaultdict(Counter)  # entity -> key -> count
values = defaultdict(Counter)  # (entity, key) -> value -> count
by_source = defaultdict(Counter)
num = defaultdict(list)  # numeric distributions
issues = Counter()
examples = defaultdict(list)


def note(issue, example):
    issues[issue] += 1
    if len(examples[issue]) < 3:
        examples[issue].append(example)


CATEGORICAL = {
    "spaces": ["type", "room_type", "synthetic"],
    "openings": ["kind", "width_m", "step_free", "synthetic"],
    "vertical_connectors": ["kind", "step_free", "synthetic"],
    "assets": ["category", "source"],
    "floors": ["level"],
}

for f in files:
    b = json.loads(f.read_text())
    bid = b["building"]["id"]
    src = b["building"]["source"]
    by_source[src]["buildings"] += 1
    for k in b:
        keys["top"][k] += 1
    for k in b["building"]:
        keys["building"][k] += 1
    floors = {fl["id"]: fl for fl in b["floors"]}
    spaces = {s["id"]: s for s in b["spaces"]}
    num[f"floors/building [{src}]"].append(len(floors))
    for ent in ("floors", "spaces", "openings", "vertical_connectors", "assets"):
        by_source[src][ent] += len(b[ent])
        for item in b[ent]:
            for k, v in item.items():
                keys[ent][k] += 1
            for k in CATEGORICAL.get(ent, []):
                if k in item:
                    values[(ent, k)][item[k]] += 1
    for fl in b["floors"]:
        num["floor height_m"].append(fl.get("height_m"))
        values[("floors", "elevation per level")][(fl["level"], fl.get("elevation_m"))] += 0
    # spaces
    space_doors = Counter()
    for o in b["openings"]:
        sids = o["space_ids"]
        values[("openings", "n_space_ids")][len(sids)] += 1
        for sid in sids:
            if sid != "outside":
                space_doors[sid] += 1
                if sid not in spaces:
                    note("opening references unknown space", (bid, o["id"], sid))
                elif spaces[sid]["floor_id"] != o["floor_id"]:
                    note("opening joins space on another floor", (bid, o["id"]))
        if "outside" in sids:
            values[("openings", "outside kind")][o["kind"]] += 1
        if len(set(sids)) < len(sids):
            note("opening joins a space to itself", (bid, o["id"]))
        num["opening width_m"].append(o.get("width_m"))
        if o["kind"] == "opening" and o.get("width_m") is not None:
            num["width of kind=opening"].append(o["width_m"])
        # is the door position near both spaces' outlines?
        from shapely.geometry import Point

        p = Point(o["position"])
        for sid in sids:
            if sid in spaces:
                d = Polygon(spaces[sid]["polygon"]).exterior.distance(p)
                num["door dist to space outline"].append(d)
                if d > 1.0:
                    note("door position >1 m from a space outline it joins", (bid, o["id"], round(d, 2)))
    for s in b["spaces"]:
        poly = Polygon(s["polygon"])
        values[("spaces", "type x room_type")][(s["type"], s.get("room_type"))] += 1
        values[("spaces", f"type [{src}]")][s["type"]] += 1
        num[f"area {s['type']}"].append(poly.area)
        num["polygon vertices"].append(len(s["polygon"]))
        if not poly.is_valid:
            note("invalid polygon", (bid, s["id"], explain_validity(poly)[:60]))
        if poly.area < 0.5:
            note("space area < 0.5 m2", (bid, s["id"], s["type"], round(poly.area, 3)))
        if not poly.contains(poly.representative_point()):
            note("anchor outside polygon", (bid, s["id"]))
        if not poly.contains(poly.centroid):
            note("centroid outside polygon (concave room)", (bid, s["id"], s["type"]))
        if space_doors[s["id"]] == 0:
            stop = any(s["id"] in c["space_ids"] for c in b["vertical_connectors"])
            note(
                f"space with no openings ({'connector stop' if stop else 'isolated'})",
                (bid, s["id"], s["type"]),
            )
        if s.get("ref") is None:
            note("space without ref", (bid, s["id"], s["type"]))
        if s["floor_id"] not in floors:
            note("space on unknown floor", (bid, s["id"]))
    refs = Counter(s.get("ref") for s in b["spaces"] if s.get("ref"))
    for r, c in refs.items():
        if c > 1:
            note(f"duplicate ref [{src}]", (bid, r, c))
    names = Counter(s["name"] for s in b["spaces"])
    for r, c in names.items():
        if c > 1:
            note(f"duplicate space name [{src}]", (bid, r, c))
    # connectors
    levels = {fid: fl["level"] for fid, fl in floors.items()}
    for c in b["vertical_connectors"]:
        n = len(c["floor_ids"])
        values[("vertical_connectors", "n_stops")][n] += 1
        lv = sorted(levels[x] for x in c["floor_ids"])
        if any(b2 - a > 1 for a, b2 in zip(lv, lv[1:])):
            note("connector skips a level", (bid, c["id"], lv))
        stop_types = tuple(sorted({spaces[x]["type"] for x in c["space_ids"] if x in spaces}))
        values[("vertical_connectors", f"stop space types [{c['kind']}]")][stop_types] += 1
        if c["kind"] == "lift" and not c["step_free"]:
            note("lift not step_free", (bid, c["id"]))
        if c["kind"] == "stairs" and c["step_free"]:
            note("stairs marked step_free", (bid, c["id"]))
    # entrances
    entr = [o for o in b["openings"] if "outside" in o["space_ids"]]
    num["entrances/building"].append(len(entr))
    if not entr:
        note(f"building without entrance [{src}]", bid)
    if entr and not any(o["step_free"] for o in entr):
        note("no step-free entrance", bid)
    # floors aligned? compare bbox centres between floors
    # assets
    for a in b["assets"]:
        values[("assets", "category x type")][(a["category"], a["type"])] += 1
        if a["space_id"] not in spaces:
            note("asset in unknown space", (bid, a["id"]))
        else:
            from shapely.geometry import Point

            if not Polygon(spaces[a["space_id"]]["polygon"]).buffer(0.5).contains(Point(a["position"])):
                note("asset position outside its space", (bid, a["id"]))
    # step-free door kinds
    for o in b["openings"]:
        values[("openings", f"kind x step_free [{src}]")][(o["kind"], o["step_free"])] += 1


def pct(xs, qs=(0, 0.1, 0.5, 0.9, 1)):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return "n/a"
    return (
        " ".join(f"p{int(q * 100)}={xs[min(int(q * (len(xs) - 1)), len(xs) - 1)]:.2f}" for q in qs)
        + f" (n={len(xs)})"
    )


print("\n== keys per entity (count of items having the key)")
for ent, c in keys.items():
    print(ent, dict(c))
print("\n== per source totals")
for s, c in by_source.items():
    print(s, dict(c))
print("\n== categorical values")
for (ent, k), c in sorted(values.items(), key=lambda kv: str(kv[0])):
    if k == "elevation per level":
        continue
    top = c.most_common(45)
    print(f"{ent}.{k}: {top}{' ...' if len(c) > 45 else ''}  [distinct={len(c)}]")
print("\n== numeric")
for k, xs in num.items():
    print(f"{k}: {pct(xs)}")
print("\n== issues")
for k, v in issues.most_common():
    print(f"{v:6d}  {k}  e.g. {examples[k]}")
