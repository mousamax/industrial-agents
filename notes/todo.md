# Owner A · Graph & state (Magnus): to-do

Owns `src/app/building/routing.py`, `state.py`, `queries.py`. Plan: `docs/synera/TEAM_PLAN.md`. Scoring rule for
routes: `docs/synera/ROUTE_SCORING.md`.

Each item names the tests that check it. `uv run pytest tests/building -q` prints this list at the end with
each item's real status (read from this file: keep the `**id**` and the backticked test names when editing).
`GOLDEN_SAMPLE=all` runs every question instead of 6 per kind; `GOLDEN_SPLIT=train` uses train.

## 1. `routing.py`: `RouteGraph`
- [ ] **1.1** `__init__`: port the kit's `BuildingMap.graph()` into a `MultiGraph` (space anchors, opening nodes, `outside`, stairs 12 m per floor, lift 15 m + 6 m per floor) · tests: `test_a1_1_graph_builds`
- [ ] **1.2** `step_free`: drop every opening or connector with `step_free` False · tests: `test_route_step_free`, `test_a1_2_step_free_never_uses_steps`
- [ ] **1.3** `closed`: drop closed openings, connectors and spaces · tests: `test_route_with_closure`, `test_a1_3_closed_element_never_used`
- [ ] **1.4** `min_width_m`: drop openings narrower than the minimum width (new; the kit doesn't have it) · tests: `test_a1_4_min_width_respected`
- [ ] **1.5** `shortest(start, goal) -> Route | None`: Dijkstra, filling `space_path`, `element_path`, `length_m`; `None` when unreachable · tests: `test_route`
- [ ] **1.6** `tour(stops) -> Tour | None`: legs in the given order, summed · tests: `test_route_tour`
- [ ] **1.7** `distances_from(sources) -> dict[space, m]`: multi-source Dijkstra · tests: `test_a1_7_distances_from_matches_shortest`
- [ ] **1.8** `reachable(start) -> {space}` · tests: `test_a1_8_reachable_matches_shortest`

## 2. `queries.py`
- [ ] **2.1** `nearest_with_asset(b, start, asset_type)`: the building block for `errand` · tests: `test_a2_1_nearest_with_asset`
- [ ] **2.2** `errand(b, start, asset_type, floor_id, room_type) -> Errand`: needs B's `rooms_by_area` · tests: `test_graph_errand`
- [ ] **2.3** `deliverable_rooms(b, start, floor_id, min_width_m)` · tests: `test_graph_wide_delivery_count`
- [ ] **2.4** `step_free_reachable_rooms(b, start, floor_id, closed)` · tests: `test_graph_step_free_reachable_count`
- [ ] **2.5** `what_if_door(b, first, second, floor_id)`: needs B's `shared_wall_midpoint` (local placeholder until it lands; don't commit it into B's file) · tests: `test_graph_what_if_door`

## 3. `state.py`: `BuildingState`
- [ ] **3.1** `apply(Close | Reopen)`, `ValueError` for unknown ids · tests: `test_a3_1_apply_close_reopen`
- [ ] **3.2** `apply(AddDoor | MoveAsset)` and `building` with the edits applied, base untouched · tests: `test_a3_2_building_has_structural_edits`
- [ ] **3.3** `constraints(base)`: closures folded in, so routing respects them · tests: `test_a3_3_constraints_drive_routing`
- [ ] **3.4** `undo() -> Edit | None` · tests: `test_a3_4_undo`
- [ ] **3.5** `export() -> dict`: kit-format building.json with the edits · tests: `test_a3_5_export_round_trips`

## 4. Synera's scoring rule (`docs/synera/ROUTE_SCORING.md`)
- [ ] **4.1** Spaces marked `"through": false` are destinations only: never pass through one (Synera, Sun 25 Oct: room type never decides it) · tests: `test_a4_1_no_shortcut_through_a_no_through_space`
- [ ] **4.2** Forklift: openings 3 m or wider only · tests: `test_a4_2_forklift_uses_wide_openings_only`
- [ ] **4.3** Stairs 2 m per metre climbed, lift 15 m + 1 m per metre (marked xfail until adopted, so it never blocks a push) · tests: `test_a4_3_stairs_and_lift_cost_per_metre`
- [ ] **4.4** Door-to-door distance about 15 cm off the walls. No automatic test: check a few routes by hand.
- [ ] **4.5** After 4.3/4.4, re-run the golden tests and see how many still agree. They were computed with the kit's model, so a difference doesn't automatically mean a bug.
- [ ] **4.6** `validate_route(b, space_path, constraints)`: why Synera would score a route 0 (no real connection, step, closed, narrow, through a bay); `scripts/synera/validate_routes.py dev` counts them · tests: `test_a4_6_validator_accepts_golden_routes`, `test_a4_6_validator_rejects_invalid_routes`

## 5. Later
- [ ] **5.1** Multi-building walkways (Level 3), the forklift/logistics add-in if the team picks it.

## Done when
- Dev: `GOLDEN_SAMPLE=all uv run pytest tests/building -q` shows every item with tests as done.
- Train: the same with `GOLDEN_SPLIT=train` in front.
- Before each push: `uv run pytest -q` passes.
