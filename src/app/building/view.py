"""The UI contract: what the frontend draws, and the view commands the agent sends it.

OWNER: Mousa (agent harness) and Owner D (product/UI) together; change only by agreement.

- `GET /buildings/{id}/scene` returns `Scene`: everything needed to draw 2D floors or 3D stacked floors.
  Implemented, so the UI can start now on real data.
- Agent tools return their data plus `view: list[ViewCommand]`. The chat stream
  (`POST /buildings/{id}/chat`, Vercel AI SDK protocol) carries tool results, so the UI reads `view`
  from each tool result and animates it: highlight a room, draw a route, switch floor, reload after an edit.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.building.model import OUTSIDE, Building
from app.building.routing import Route

XY = tuple[float, float]


class SceneFloor(BaseModel):
    id: str
    level: int
    name: str
    elevation_m: float
    height_m: float


class SceneSpace(BaseModel):
    id: str
    floor_id: str
    type: str
    room_type: str
    name: str
    ref: str
    polygon: list[XY]
    anchor: XY


class SceneOpening(BaseModel):
    id: str
    floor_id: str
    kind: str
    position: XY
    width_m: float
    step_free: bool
    space_ids: list[str]


class SceneConnector(BaseModel):
    id: str
    kind: str
    name: str
    stops: list[tuple[str, str]]  # (floor_id, space_id) by level
    step_free: bool


class SceneAsset(BaseModel):
    id: str
    name: str
    type: str
    category: str
    space_id: str
    floor_id: str
    position: XY
    width_m: float
    depth_m: float
    rotation_deg: float


class Scene(BaseModel):
    id: str
    name: str
    source: str
    bounds: tuple[float, float, float, float]  # min_x, min_y, max_x, max_y over all floors, metres
    floors: list[SceneFloor]
    spaces: list[SceneSpace]
    openings: list[SceneOpening]
    connectors: list[SceneConnector]
    assets: list[SceneAsset]


class Highlight(BaseModel):
    kind: Literal["highlight"] = "highlight"
    ids: list[str]  # space, asset, opening or connector ids
    label: str | None = None


class RouteSegment(BaseModel):
    floor_id: str
    points: list[XY]  # polyline in building metres


class ShowRoute(BaseModel):
    kind: Literal["route"] = "route"
    segments: list[RouteSegment]  # one per stretch on a floor, in walking order
    length_m: float
    space_ids: list[str]
    steps: list[str] = []  # human directions, if the tool produced them


class FocusFloor(BaseModel):
    kind: Literal["focus_floor"] = "focus_floor"
    floor_id: str


class SceneChanged(BaseModel):
    kind: Literal["scene_changed"] = "scene_changed"
    reason: str  # "door added between 2.03 and 2.04": the UI refetches the scene


ViewCommand = Highlight | ShowRoute | FocusFloor | SceneChanged


def scene(b: Building) -> Scene:
    xs = [x for s in b.spaces.values() for x, _ in s.polygon]
    ys = [y for s in b.spaces.values() for _, y in s.polygon]
    return Scene(
        id=b.id,
        name=b.name,
        source=b.source,
        bounds=(min(xs), min(ys), max(xs), max(ys)),
        floors=[SceneFloor(**vars(f)) for f in b.floors_by_level()],
        spaces=[
            SceneSpace(
                id=s.id,
                floor_id=s.floor_id,
                type=s.type,
                room_type=s.room_type,
                name=s.name,
                ref=s.ref,
                polygon=list(s.polygon),
                anchor=s.anchor,
            )
            for s in b.spaces.values()
        ],
        openings=[
            SceneOpening(
                id=o.id,
                floor_id=o.floor_id,
                kind=o.kind,
                position=o.position,
                width_m=o.width_m,
                step_free=o.step_free,
                space_ids=list(o.space_ids),
            )
            for o in b.openings.values()
        ],
        connectors=[
            SceneConnector(id=c.id, kind=c.kind, name=c.name, stops=list(c.stops), step_free=c.step_free)
            for c in b.connectors.values()
        ],
        assets=[
            SceneAsset(
                id=a.id,
                name=a.name,
                type=a.type,
                category=a.category,
                space_id=a.space_id,
                floor_id=a.floor_id,
                position=a.position,
                width_m=a.width_m,
                depth_m=a.depth_m,
                rotation_deg=a.rotation_deg,
            )
            for a in b.assets.values()
        ],
    )


def route_view(b: Building, route: Route, steps: list[str] | None = None) -> ShowRoute:
    """Turn a Route's node_path into per-floor polylines (anchors and door positions)."""
    segments: list[RouteSegment] = []
    for node in route.node_path:
        if node == OUTSIDE:
            continue
        if node.startswith("door:"):
            opening = b.openings[node.removeprefix("door:")]
            floor_id, point = opening.floor_id, opening.position
        else:
            space = b.spaces[node]
            floor_id, point = space.floor_id, space.anchor
        if not segments or segments[-1].floor_id != floor_id:
            segments.append(RouteSegment(floor_id=floor_id, points=[]))
        segments[-1].points.append(point)
    return ShowRoute(
        segments=segments, length_m=route.length_m, space_ids=route.space_path, steps=steps or []
    )
