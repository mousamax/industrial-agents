"""Prototype of Synera's announced route metric vs the kit's star model, on warm-up routes.

New metric (Discord note): door-to-door walking inside each space, 15 cm off the walls (geodesic in the
inset polygon); stairs 2 m per metre climbed; lift 15 m + 1 m per metre. Start/end at the room's
representative point (assumption: the note doesn't say). Outside: entrances joined at 0 m (kit).
Score of a submitted space sequence = best length / its length under the new metric.

A prototype for Owner A to check RouteGraph against, not the scorer itself (assumptions above).

    uv run python scripts/synera/route_metric_check.py synera-kit/data/dev    # ~3 min, no API calls
"""

import itertools
import json
import math
import sys
from collections import Counter
from pathlib import Path

import networkx as nx
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points
from shapely.prepared import prep

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "synera-kit"))
from starter_agent.building_map import OUTSIDE, BuildingMap, MapError  # noqa: E402

INSET = 0.15
KINDS = {"route", "route_step_free", "route_with_closure", "tour"}


class Walk:
    """Geodesic distances between points inside one space (visibility graph on the inset polygon)."""

    def __init__(self, polygon):
        poly = Polygon(polygon)
        inset = poly.buffer(-INSET)
        if inset.is_empty or inset.geom_type != "Polygon":
            inset = poly if inset.is_empty else max(inset.geoms, key=lambda g: g.area)
        self.poly = inset
        self.cover = prep(inset.buffer(1e-6))
        rings = [inset.exterior, *inset.interiors]
        self.corners = [c for r in rings for c in list(r.coords)[:-1]]

    def snap(self, xy):
        p = Point(xy)
        return xy if self.poly.contains(p) else nearest_points(self.poly, p)[0].coords[0]

    def distances(self, points):
        pts = [self.snap(p) for p in points]
        nodes = pts + self.corners
        g = nx.Graph()
        for i, j in itertools.combinations(range(len(nodes)), 2):
            a, b = nodes[i], nodes[j]
            if a == b or self.cover.covers(LineString([a, b])):
                g.add_edge(i, j, weight=math.dist(a, b))
        out = {}
        for i in range(len(pts)):
            d = nx.single_source_dijkstra_path_length(g, i, weight="weight") if i in g else {}
            for j in range(len(pts)):
                out[i, j] = 0.0 if i == j else d.get(j, math.dist(pts[i], pts[j]))
        return out


def new_graph(m: BuildingMap, step_free: bool) -> nx.MultiGraph:
    g = nx.MultiGraph()
    ports: dict[str, list[tuple[str, tuple]]] = {s: [(s, m.anchor(s))] for s in m.spaces if s not in m.closed}
    for o in m.openings.values():
        if o["id"] in m.closed or (step_free and not o["step_free"]):
            continue
        for s in o["space_ids"]:
            if s == OUTSIDE:
                g.add_edge(OUTSIDE, "door:" + o["id"], weight=0.0, space=OUTSIDE)
            elif s in ports:
                ports[s].append(("door:" + o["id"], tuple(o["position"])))
    for s, ps in ports.items():
        d = Walk(m.spaces[s]["polygon"]).distances([xy for _, xy in ps])
        for (i, (a, _)), (j, (b, _)) in itertools.combinations(enumerate(ps), 2):
            g.add_edge(a, b, weight=d[i, j], space=s)
    elev = {f: fl["elevation_m"] for f, fl in m.floors.items()}
    for c in m.connectors.values():
        if c["id"] in m.closed or (step_free and not c["step_free"]):
            continue
        stops = sorted(zip(c["floor_ids"], c["space_ids"], strict=True), key=lambda st: elev[st[0]])
        for (fa, sa), (fb, sb) in itertools.pairwise(stops):
            if sa in ports and sb in ports:
                h = abs(elev[fb] - elev[fa])
                g.add_edge(sa, sb, weight=15.0 + h if c["kind"] == "lift" else 2.0 * h, space=None)
    return g


def sequence_length(m: BuildingMap, g: nx.MultiGraph, seq: list[str]) -> float | None:
    """Shortest walk that visits exactly this sequence of spaces (what the scorer measures)."""
    layered = nx.DiGraph()
    for u, v, data in g.edges(data=True):
        for i, s in enumerate(seq):
            if data["space"] == s:  # walking inside space i
                layered.add_edge((i, u), (i, v), weight=data["weight"])
                layered.add_edge((i, v), (i, u), weight=data["weight"])
            if data["space"] is None and i + 1 < len(seq) and {u, v} == {s, seq[i + 1]}:
                layered.add_edge((i, s), (i + 1, seq[i + 1]), weight=data["weight"])
    for i, (a, b) in enumerate(itertools.pairwise(seq)):  # crossing a door between consecutive spaces
        for o in m.openings.values():
            if set(o["space_ids"]) == {a, b}:
                layered.add_edge((i, "door:" + o["id"]), (i + 1, "door:" + o["id"]), weight=0.0)
    try:
        return nx.dijkstra_path_length(layered, (0, seq[0]), (len(seq) - 1, seq[-1]), weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def spaces_of(g, nodes):
    seq = [nodes[0]]
    for u, v in itertools.pairwise(nodes):
        edge = min(g.get_edge_data(u, v).values(), key=lambda d: d["weight"])
        for s in [edge["space"]] if edge["space"] else [u, v]:
            if s != seq[-1]:
                seq.append(s)
    return seq


def main(split: Path) -> None:
    stats, scores, worst = Counter(), [], []
    for path in sorted(split.glob("*/questions_basic.jsonl")):
        qs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        qs = [q for q in qs if q["kind"] in KINDS]
        for q in qs:
            a, kind = q["answer"], q["kind"]
            m = BuildingMap.load(path.parent)
            if "closed_element_id" in a:
                m.set_closed(a["closed_element_id"], closed=True)
            stops = a["stops"] if kind == "tour" else [a["from_space_id"], a["to_space_id"]]
            sf = kind == "route_step_free"
            try:
                kit = m.route(stops[0], stops[-1], step_free=sf, via=stops[1:-1]).space_path
            except MapError:
                stats["unreachable both"] += 1
                continue
            g = new_graph(m, sf)
            best_nodes = []
            for s, t in itertools.pairwise(stops):
                leg = nx.dijkstra_path(g, s, t, weight="weight")
                best_nodes += leg if not best_nodes else leg[1:]
            best_seq = spaces_of(g, best_nodes)
            best = sequence_length(m, g, best_seq)
            kit_len = sequence_length(m, g, kit)
            if best is None or kit_len is None:
                stats["could not measure"] += 1
                continue
            score = min(1.0, best / kit_len) if kit_len else 1.0
            scores.append(score)
            stats["same sequence" if kit == best_seq else "different sequence"] += 1
            stats["full marks (within 1%)" if kit_len <= best * 1.01 else "partial"] += 1
            worst.append((score, q["id"], kind))
    print(dict(stats))
    print(
        f"mean score of the kit's routes under the new metric: {sum(scores) / len(scores):.3f} on {len(scores)}"
    )
    for s, qid, kind in sorted(worst)[:8]:
        print(f"  {s:.3f}  {qid}  {kind}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
