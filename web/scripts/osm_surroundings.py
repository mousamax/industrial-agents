"""OpenStreetMap around the Munich Urban Colab, in the building's own metres, for the map's backdrop.

    uv run python web/scripts/osm_surroundings.py

Fetches the building's footprint (OSM way 923823559) and everything within RADIUS_M from Overpass, fits the
footprint's four corners onto data/MUCL/building.json's 100.7 x 29.8 m outline (x: south end to north end,
y: entrance facade to the back), and writes web/src/data/surroundings/munich-urban-colab.json. Run once; the
app ships the file, so the demo needs no network. Data (c) OpenStreetMap contributors, ODbL.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "web" / "src" / "data" / "surroundings" / "munich-urban-colab.json"
OVERPASS = "https://overpass-api.de/api/interpreter"
FOOTPRINT_WAY = 923823559
LENGTH_M, WIDTH_M = 100.7, 29.8
RADIUS_M = 420.0

ROADS = {
    "motorway": "major", "trunk": "major", "primary": "major", "secondary": "major", "tertiary": "major",
    "residential": "minor", "unclassified": "minor", "living_street": "minor", "service": "service",
    "pedestrian": "path", "footway": "path", "path": "path", "cycleway": "path", "steps": "path",
}  # fmt: skip
GREEN = {"grass", "meadow", "flowerbed", "allotments", "park", "garden", "playground", "pitch", "wood",
         "scrub", "grassland", "village_green", "recreation_ground"}  # fmt: skip


def overpass(query: str) -> list[dict]:
    body = urllib.parse.urlencode({"data": f"[out:json][timeout:90];{query}"}).encode()
    request = urllib.request.Request(OVERPASS, body, headers={"User-Agent": "IA-Hackathon-MUCL-demo"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)["elements"]


def main() -> None:
    footprint = overpass(f"way({FOOTPRINT_WAY});out geom;")[0]["geometry"][:-1]
    lat0 = sum(p["lat"] for p in footprint) / len(footprint)
    lon0 = sum(p["lon"] for p in footprint) / len(footprint)
    ky = 111_320.0
    kx = ky * math.cos(math.radians(lat0))

    def enu(p: dict) -> tuple[float, float]:
        return ((p["lon"] - lon0) * kx, (p["lat"] - lat0) * ky)

    # The footprint is a rectangle with extra nodes on its long sides: its corners are the 4 nodes farthest
    # from the centre. Long axis = south end -> north end (x); short axis = towards the back facade (y),
    # i.e. away from the side carrying the entrance=main node.
    points = [enu(p) for p in footprint]
    corners = sorted(points, key=lambda q: -math.hypot(*q))[:4]
    south = sorted(corners, key=lambda q: q[1])[:2]
    north = [q for q in corners if q not in south]
    mid = lambda a: ((a[0][0] + a[1][0]) / 2, (a[0][1] + a[1][1]) / 2)  # noqa: E731
    s, n = mid(south), mid(north)
    ux, uy = n[0] - s[0], n[1] - s[1]
    norm = math.hypot(ux, uy)
    ux, uy = ux / norm, uy / norm
    entrance = next(enu(p) for p in overpass(f"way({FOOTPRINT_WAY});node(w)[entrance=main];out;"))
    vx, vy = -uy, ux  # left of the long axis
    if ((entrance[0] - (s[0] + n[0]) / 2) * vx + (entrance[1] - (s[1] + n[1]) / 2) * vy) > 0:
        vx, vy = -vx, -vy  # y points away from the entrance facade
    assert ux * vy - uy * vx > 0, "x, y must be right-handed like the app's map"

    cx, cy = sum(q[0] for q in corners) / 4, sum(q[1] for q in corners) / 4

    def local(p: dict) -> list[float]:
        e, n_ = enu(p)
        e, n_ = e - cx, n_ - cy
        return [round(e * ux + n_ * uy + LENGTH_M / 2, 1), round(e * vx + n_ * vy + WIDTH_M / 2, 1)]

    r = RADIUS_M / ky
    box = f"({lat0 - r},{lon0 - r * ky / kx},{lat0 + r},{lon0 + r * ky / kx})"
    keys = ["building", "highway", "landuse", "leisure", "natural", "railway", "waterway", "amenity=parking"]
    query = "(" + "".join(f"way[{k.split('=')[0]}{'=' + k.split('=')[1] if '=' in k else ''}]{box};"
                          for k in keys) + ");out geom tags;"  # fmt: skip
    features = []
    for way in overpass(query):
        tags, geometry = way.get("tags", {}), way.get("geometry", [])
        underground = tags.get("tunnel") not in (None, "no") or tags.get("layer", "").startswith("-")
        if way["id"] == FOOTPRINT_WAY or len(geometry) < 2 or tags.get("indoor") or underground:
            continue
        closed = geometry[0] == geometry[-1]
        kind = None
        if "building" in tags and closed:
            kind = "building"
        elif tags.get("highway") in ROADS and tags.get("area") != "yes":
            kind = ROADS[tags["highway"]]
        elif tags.get("railway") == "tram":
            kind = "tram"
        elif "waterway" in tags or tags.get("natural") == "water":
            kind = "water"
        elif closed and tags.get("amenity") == "parking":
            kind = "parking"
        elif closed and (
            tags.get("landuse") in GREEN or tags.get("leisure") in GREEN or tags.get("natural") in GREEN
        ):
            kind = "green"
        if kind is None:
            continue
        pts = [local(p) for p in geometry]
        if min(math.hypot(x - LENGTH_M / 2, y - WIDTH_M / 2) for x, y in pts) > RADIUS_M:
            continue
        feature: dict = {"kind": kind, "points": pts}
        if tags.get("name") and kind in ("major", "minor"):
            feature["name"] = tags["name"]
        if kind == "building" and tags.get("building:levels", "").isdigit():
            feature["levels"] = int(tags["building:levels"])
        features.append(feature)
    order = ["green", "parking", "water", "service", "path", "minor", "major", "tram", "building"]
    features.sort(key=lambda f: order.index(f["kind"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "building_id": "munich-urban-colab",
                "attribution": "© OpenStreetMap contributors",
                "radius_m": RADIUS_M,
                "features": features,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    print(f"{len(features)} features -> {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
