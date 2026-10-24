"""Second pass: connectivity, overlaps, floor alignment, widths by space pair, question inputs."""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
from shapely.geometry import Polygon
from shapely.strtree import STRtree

DATA = Path(sys.argv[1])
files = sorted(DATA.glob("*/*/building.json"))
out = defaultdict(Counter)
ex = defaultdict(list)


def note(key, value, example=None):
    out[key][value] += 1
    if example is not None and len(ex[(key, value)]) < 2:
        ex[(key, value)].append(example)


for f in files:
    b = json.loads(f.read_text())
    bid, src = b["building"]["id"], b["building"]["source"]
    spaces = {s["id"]: s for s in b["spaces"]}
    floors = {fl["id"]: fl for fl in b["floors"]}
    # connectivity from outside, walking and step-free
    for step_free in (False, True):
        g = nx.Graph()
        g.add_nodes_from(spaces)
        g.add_node("outside")
        for o in b["openings"]:
            if step_free and not o["step_free"]:
                continue
            a, c = o["space_ids"]
            g.add_edge(a, c)
        for v in b["vertical_connectors"]:
            if step_free and not v["step_free"]:
                continue
            for a, c in zip(v["space_ids"], v["space_ids"][1:]):
                g.add_edge(a, c)
        reach = nx.node_connected_component(g, "outside")
        unreachable = [s for s in spaces if s not in reach]
        frac = len(unreachable) / len(spaces)
        bucket = "0" if not unreachable else ("<5%" if frac < 0.05 else ("<25%" if frac < 0.25 else ">=25%"))
        note(
            f"unreachable from outside ({'step-free' if step_free else 'walking'}) [{src}]",
            bucket,
            (bid, len(unreachable), len(spaces)) if unreachable else None,
        )
        if not step_free:
            for s in unreachable:
                note("unreachable space types", spaces[s]["type"])
    # overlaps on the same floor
    by_floor = defaultdict(list)
    for s in b["spaces"]:
        by_floor[s["floor_id"]].append(s)
    for fid, ss in by_floor.items():
        polys = [Polygon(s["polygon"]) for s in ss]
        tree = STRtree(polys)
        for i, p in enumerate(polys):
            for j in tree.query(p):
                if j <= i:
                    continue
                inter = p.intersection(polys[j]).area
                if inter > 1.0:
                    pair = tuple(sorted((ss[i]["type"], ss[int(j)]["type"])))
                    note(
                        f"overlap >1 m2 [{src}]", pair, (bid, ss[i]["id"], ss[int(j)]["id"], round(inter, 1))
                    )
    # floor alignment: overlap of floor footprints with the floor below
    order = sorted(floors.values(), key=lambda fl: fl["level"])
    from shapely.ops import unary_union

    foot = {fid: unary_union([Polygon(s["polygon"]) for s in ss]) for fid, ss in by_floor.items()}
    for lo, hi in zip(order, order[1:]):
        a, c = foot.get(lo["id"]), foot.get(hi["id"])
        if a is None or c is None or a.is_empty or c.is_empty:
            continue
        iou = a.intersection(c).area / min(a.area, c.area)
        note(
            f"floor-to-floor footprint overlap [{src}]",
            "<50%" if iou < 0.5 else (">=90%" if iou >= 0.9 else "50-90%"),
            (bid, lo["level"], hi["level"], round(iou, 2)) if iou < 0.5 else None,
        )
    # elevation vs level
    for fl in b["floors"]:
        note(
            f"elevation_m / level [{src}]",
            "consistent" if fl["level"] == 0 or abs(fl["elevation_m"]) > 0 else "zero at level != 0",
        )
    # widths by space pair type and opening kind
    for o in b["openings"]:
        types = tuple(sorted(spaces[s]["type"] if s in spaces else "OUTSIDE" for s in o["space_ids"]))
        note(f"opening kind by space pair [{src}]", (o["kind"],) + types)
        w = o["width_m"]
        wb = (
            "<0.8"
            if w < 0.8
            else "0.8-1.09"
            if w < 1.1
            else "1.1-1.19"
            if w < 1.2
            else "1.2-1.59"
            if w < 1.6
            else "1.6-2.9"
            if w < 3
            else ">=3"
        )
        note("width bucket by kind", (o["kind"], wb))
    # connectors per building and lift presence per source
    kinds = Counter(v["kind"] for v in b["vertical_connectors"])
    note(f"connectors [{src}]", (kinds.get("stairs", 0) > 0, kinds.get("lift", 0) > 0))
    # walkable surface: halls in procedural names
    for s in b["spaces"]:
        if s["type"] == "hall":
            note("hall names (first word)", s["name"].split()[0])
    for a in b["assets"]:
        if a["category"] == "machine":
            note("machine types", a["type"])

# question inputs and skills
skills = Counter()
inputs = defaultdict(Counter)
basic = Counter()
for qf in DATA.glob("*/*/questions.jsonl"):
    for line in qf.read_text().splitlines():
        q = json.loads(line)
        skills[(q["kind"], q.get("skill"))] += 1
        for k in q.get("inputs", {}):
            inputs[q["kind"]][k] += 1
for qf in DATA.glob("*/*/questions_basic.jsonl"):
    for line in qf.read_text().splitlines():
        basic[json.loads(line)["kind"]] += 1

for key, c in out.items():
    print(f"\n{key}: {c.most_common(25)}")
    for (k2, v), e in ex.items():
        if k2 == key:
            print("   e.g.", v, e)
print("\nskills:", skills.most_common())
print("\ninputs per kind:")
for k, c in inputs.items():
    print(" ", k, dict(c))
print("\nwarm-up kinds:", dict(basic))
