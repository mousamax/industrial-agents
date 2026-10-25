"""Graph questions: answers that need routing or reachability on top of RouteGraph.

OWNER: Owner A, graph & state. Interface frozen: change signatures only after telling the team.

Backs errand (20 test questions), wide_delivery_count (18), what_if_door (3), step_free_reachable_count
(0 in test, 2 in train) and the Sunday route questions. Arguments mirror the `inputs` of the train/dev
questions, so tests/building/test_golden.py checks them directly against the golden answers. Which space
types a "room" count includes is defined by those answers: let the tests tell you, then write it down here.

Measured on every dev + train answer (80 wide_delivery, 2 step_free_reachable, 4 what_if_door, 93 errand):
- A "room" in the counts is a space with `type == "room"`: halls, corridors, stairs, lifts and outdoor
  spaces don't count (counting halls too breaks 19 of 80 wide_delivery answers).
- errand: the pickup is the asset holder with the shortest walking route from start (any floor; the start
  itself if it holds one), the target is geometry.rooms_by_area(...)[0], the length is both legs summed.
- what_if_door: a 0.9 m step-free door at geometry.shared_wall_midpoint; rooms that were unreachable
  before and reachable after count as gaining more than min_gain_m.
Still true under kit 1.1.0's route model (door-to-door walking, per-metre stairs and lifts, no outdoor
shortcut): every train and dev answer reproduces with RouteGraph.
"""

from __future__ import annotations

import dataclasses
import itertools
import math
from dataclasses import dataclass

from app.building import geometry
from app.building.model import OUTSIDE, Building, Opening
from app.building.routing import WALKING, Constraints, RouteGraph


@dataclass(frozen=True)
class Errand:
    pickup_space_id: str  # golden `via`: the room with the item, nearest to start
    target_space_id: str  # golden `target`: the largest room_type room on floor_id
    length_m: float  # start -> pickup -> target


def _rooms_on(b: Building, floor_id: str, space_ids: set[str] | dict[str, float]) -> list[str]:
    """Space ids from `space_ids` that are rooms (type "room") on floor_id, in building order."""
    return [s.id for s in b.spaces_on(floor_id, type="room") if s.id in space_ids]


def nearest_with_asset(
    b: Building, start: str, asset_type: str, constraints: Constraints = WALKING
) -> tuple[str, float] | None:
    """The space holding an `asset_type` with the shortest route from start: (space_id, metres)."""
    holders = {a.space_id for a in b.assets_of_type(asset_type)}
    distances = RouteGraph(b, constraints).distances_from([start])
    reached = [(distances[sid], sid) for sid in holders if sid in distances]
    if not reached:
        return None
    metres, space_id = min(reached)
    return space_id, round(metres, 2)


def errand(
    b: Building,
    start: str,
    asset_type: str,
    floor_id: str,
    room_type: str,
    constraints: Constraints = WALKING,
) -> Errand | None:
    """'Grab a <asset_type> from the room that has one closest to me, then carry it to the largest
    <room_type> on <floor>.' Uses geometry.rooms_by_area for the target."""
    pickup = nearest_with_asset(b, start, asset_type, constraints)
    ranked = geometry.rooms_by_area(b, floor_id, room_type)
    if pickup is None or not ranked:
        return None
    pickup_id, first_leg = pickup
    target_id = ranked[0][0].id
    second_leg = RouteGraph(b, constraints).shortest(pickup_id, target_id)
    if second_leg is None:
        return None
    return Errand(pickup_id, target_id, round(first_leg + second_leg.length_m, 2))


def deliverable_rooms(b: Building, start: str, floor_id: str, min_width_m: float) -> list[str]:
    """wide_delivery_count: rooms on floor_id reachable from the entrance space `start` with no stairs
    or steps and every opening >= min_width_m."""
    graph = RouteGraph(b, Constraints(step_free=True, min_width_m=min_width_m))
    return _rooms_on(b, floor_id, graph.reachable(start))


def step_free_reachable_rooms(b: Building, start: str, floor_id: str, closed: list[str]) -> list[str]:
    """step_free_reachable_count: rooms on floor_id a wheelchair user reaches from `start` with `closed` out."""
    graph = RouteGraph(b, Constraints(step_free=True, closed=frozenset(closed)))
    return _rooms_on(b, floor_id, graph.reachable(start))


def what_if_door(
    b: Building, first: str, second: str, floor_id: str, *, min_gain_m: float = 1.0
) -> list[str]:
    """Rooms on floor_id whose walk to the nearest entrance gets shorter by more than min_gain_m when a
    door is added in the middle of the wall first/second share (geometry.shared_wall_midpoint)."""
    midpoint = geometry.shared_wall_midpoint(b, first, second)
    if midpoint is None:
        raise ValueError(f"{b.label(first)} and {b.label(second)} don't share a wall")
    door = Opening(
        id=f"{b.spaces[first].floor_id}-O-whatif",
        floor_id=b.spaces[first].floor_id,
        space_ids=(first, second),
        kind="door",
        position=midpoint,
        width_m=0.9,
        step_free=True,
        synthetic=True,
    )
    with_door = dataclasses.replace(b, openings={**b.openings, door.id: door})
    before = RouteGraph(b).distances_from([OUTSIDE])
    after = RouteGraph(with_door).distances_from([OUTSIDE])
    gained = {sid for sid, d in after.items() if before.get(sid, math.inf) - d > min_gain_m}
    return _rooms_on(b, floor_id, gained)


# ---- visiting order: a small (generalised) travelling-salesman ------------------------------------------

VISIT_EXACT_MAX_GROUPS = 12
"""Exact (DP over covered-groups mask x current room) up to this many groups; 2-opt local search above.
Measured: 12 groups x 100 candidates searches in about 0.2 s; each extra group doubles it."""
VISIT_MAX_CANDIDATES = 100
"""Candidate rooms kept in all. Each costs one Dijkstra, about 3.5 us per space (0.5-1 ms on campuses and
factories, 4-6 ms on the 1,623-space OSM building), so the cap also shrinks with the building:
VISIT_MATRIX_BUDGET space-visits keeps the distance matrix near 0.4 s. Groups with more holders are pruned to
the rooms closest to the trip's fixed points (start, end, rooms that must be visited)."""
VISIT_MATRIX_BUDGET = 100_000
VISIT_MIN_CANDIDATES = 24


@dataclass(frozen=True)
class VisitPlan:
    """The shortest trip that visits one room of every group, in the best order."""

    start: str
    end: str | None  # the fixed end (start again for a round trip); None = ends at the last stop
    stops: list[str]  # rooms visited, in order, without start and end
    covers: list[list[int]]  # for each stop, the groups (indices into the input) it settles
    legs_m: list[float]  # one per leg: start -> stops... -> end
    total_m: float
    exact: bool  # proven shortest (False: local search above VISIT_EXACT_MAX_GROUPS, or candidates pruned)
    greedy_total_m: float | None  # nearest-next-stop trip, for comparison
    pruned_groups: list[int]  # groups whose candidates were cut to those closest to the trip's fixed points
    start_covers: list[int]  # groups already settled by the start (or end) room itself
    unreachable_groups: list[int] = dataclasses.field(default_factory=list)


def plan_visits(
    b: Building,
    start: str,
    groups: list[list[str]],
    *,
    end: str | None = None,
    return_to_start: bool = False,
    constraints: Constraints = WALKING,
    graph: RouteGraph | None = None,
    max_candidates: int = VISIT_MAX_CANDIDATES,
    exact_max_groups: int = VISIT_EXACT_MAX_GROUPS,
) -> VisitPlan | None:
    """Visit one room from each group, in whichever order and from whichever rooms make the walk shortest
    (a generalised TSP). A fixed room to visit is a group of one; "a room with a coffee machine" is the group
    of all its holders. One room may settle several groups (a makerspace with a 3D printer and a laser cutter).
    The trip starts at `start` and ends at `end`, back at start (`return_to_start`), or wherever the last
    stop is. Legs are shortest routes under `constraints` (production bays only as stops, closures avoided).
    Returns a plan with `unreachable_groups` set (and no stops) when some group has no reachable room, and
    None when the end can't be reached at all."""
    import numpy as np

    graph = graph or RouteGraph(b, constraints)
    if return_to_start:
        end = start
    n_groups = len(groups)
    full = (1 << n_groups) - 1
    from_start = graph.distances_from([start])
    member_sets = [set(members) for members in groups]

    def cover(space_id: str | None) -> int:
        return sum(1 << g for g, members in enumerate(member_sets) if space_id in members) if space_id else 0

    fixed_cover = cover(start) | cover(end)
    to_end = graph.distances_from([end]) if end is not None else None
    if to_end is not None and end != start and end not in from_start:
        return None
    unreachable = [
        g
        for g, members in enumerate(groups)
        if not fixed_cover >> g & 1
        and not any(m in from_start and (to_end is None or m in to_end) for m in members)
    ]
    if unreachable:
        return VisitPlan(start, end, [], [], [], math.inf, True, None, [], _bits(fixed_cover), unreachable)

    # Candidates: rooms that settle a group not settled by start/end, pruned by distance to the fixed points.
    open_groups = [g for g in range(n_groups) if not fixed_cover >> g & 1]
    anchors = [from_start] + ([to_end] if to_end is not None else [])
    anchors += [graph.distances_from([groups[g][0]]) for g in open_groups if len(set(groups[g])) == 1]
    cap = min(max_candidates, max(VISIT_MIN_CANDIDATES, VISIT_MATRIX_BUDGET // max(1, len(b.spaces))))
    per_group = max(1, cap // max(1, len(open_groups)))
    chosen: dict[str, None] = {}
    pruned = []
    for g in open_groups:
        reach = [m for m in dict.fromkeys(groups[g]) if m in from_start and (to_end is None or m in to_end)]
        ranked = sorted(reach, key=lambda m: min(a.get(m, math.inf) for a in anchors))
        if len(ranked) > per_group:
            pruned.append(g)
        chosen.update(dict.fromkeys(ranked[:per_group]))
    nodes = list(chosen)
    covers = np.array([cover(m) & ~fixed_cover for m in nodes], dtype=np.int64)
    rows = [graph.distances_from([m]) for m in nodes]
    dist = np.array([[row.get(m, math.inf) for m in nodes] for row in rows])
    d_start = np.array([from_start.get(m, math.inf) for m in nodes])
    d_end = np.array([to_end.get(m, math.inf) for m in nodes]) if to_end is not None else np.zeros(len(nodes))
    need = full & ~fixed_cover

    def total(order: list[int]) -> float:
        if not order:
            return 0.0 if end is None else float(from_start.get(end, math.inf) if end != start else 0.0)
        legs = d_start[order[0]] + sum(dist[a, z] for a, z in itertools.pairwise(order))
        return float(legs + d_end[order[-1]])

    greedy = _greedy_visits(dist, d_start, covers, need)
    if need == 0:
        order, exact = [], True
    elif len(open_groups) <= exact_max_groups:
        order, exact = _exact_visits(dist, d_start, d_end, covers, need), True
    else:
        order, exact = _improve_visits(greedy, dist, d_start, d_end, covers, need), False
    stops = [nodes[i] for i in order]
    legs = (
        [float(d_start[order[0]])] + [float(dist[a, z]) for a, z in itertools.pairwise(order)]
        if order
        else []
    )
    if order and end is not None:
        legs.append(float(d_end[order[-1]]))
    elif not order and end is not None and end != start:
        legs = [float(from_start[end])]
    return VisitPlan(
        start=start,
        end=end,
        stops=stops,
        covers=[_bits(int(covers[i])) for i in order],
        legs_m=[round(x, 2) for x in legs],
        total_m=round(total(order), 2),
        exact=exact and not pruned,
        greedy_total_m=round(total(greedy), 2) if greedy is not None else None,
        pruned_groups=pruned,
        start_covers=_bits(fixed_cover & full),
    )


def _bits(mask: int) -> list[int]:
    return [g for g in range(mask.bit_length()) if mask >> g & 1]


def _exact_visits(dist, d_start, d_end, covers, need: int) -> list[int]:
    """Held-Karp over (groups settled, room we're in): dp[mask, v] = shortest walk from start that settles
    `mask` and stands in v. A room is only worth visiting if it settles something new, so masks only grow
    and increasing numeric order is a valid processing order. O(2^G * N^2), vectorised over rooms."""
    import numpy as np

    n = len(covers)
    size = need + 1
    dp = np.full((size, n), math.inf)
    back = np.full((size, n, 2), -1, dtype=np.int64)  # (previous mask, previous room)
    useful = np.flatnonzero(covers != 0)
    dp[covers[useful], useful] = d_start[useful]
    idx = np.arange(n)
    for mask in range(1, size):
        if mask & ~need or not np.isfinite(dp[mask]).any():
            continue
        step = dp[mask][:, None] + dist  # [u, v]
        best_u = step.argmin(axis=0)
        best = step[best_u, idx]
        new = mask | covers
        v = idx[(new != mask) & (best < dp[new, idx])]
        dp[new[v], v] = best[v]
        back[new[v], v, 0] = mask
        back[new[v], v, 1] = best_u[v]
    final = dp[need] + d_end
    v = int(final.argmin())
    if not math.isfinite(final[v]):
        return []
    order, mask = [], need
    while v >= 0:
        order.append(v)
        mask, v = (int(x) for x in back[mask, v])
    return order[::-1]


def _greedy_visits(dist, d_start, covers, need: int) -> list[int] | None:
    """Nearest next room that settles something still open (what a person does without planning)."""
    order: list[int] = []
    settled, here = 0, None
    while settled != need:
        gains = (covers & ~settled) != 0
        reach = d_start if here is None else dist[here]
        options = [(reach[v], v) for v in range(len(covers)) if gains[v] and math.isfinite(reach[v])]
        if not options:
            return None
        _, here = min(options)
        order.append(here)
        settled |= int(covers[here])
    return order


def _improve_visits(order, dist, d_start, d_end, covers, need: int) -> list[int]:
    """Local search from the greedy trip until nothing improves: 2-opt reversals, swapping a stop for
    another room, dropping a stop that's no longer needed (the classic 2-opt / Or-opt family)."""
    if order is None:
        return []

    def cost(o: list[int]) -> float:
        if not o:
            return math.inf
        return float(d_start[o[0]] + sum(dist[a, z] for a, z in itertools.pairwise(o)) + d_end[o[-1]])

    def settles(o: list[int]) -> bool:
        m = 0
        for v in o:
            m |= int(covers[v])
        return m & need == need

    best, best_cost = list(order), cost(order)
    improved = True
    while improved:
        improved = False
        candidates = []
        for i, j in itertools.combinations(range(len(best)), 2):
            candidates.append(best[:i] + best[i : j + 1][::-1] + best[j + 1 :])
        for i in range(len(best)):
            candidates.append(best[:i] + best[i + 1 :])
            rest = best[:i] + best[i + 1 :]
            candidates.extend(rest[:j] + [best[i]] + rest[j:] for j in range(len(rest) + 1) if j != i)
            for v in range(len(covers)):
                if v not in best:
                    candidates.append(best[:i] + [v] + best[i + 1 :])
        for c in candidates:
            c_cost = cost(c)
            if c_cost < best_cost - 1e-9 and settles(c):
                best, best_cost, improved = c, c_cost, True
                break
    return best
