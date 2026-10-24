# Route validity and the stair/lift cost rule (Owner A, Sat 24 Oct)

Magnus's notes for other agents working on routing (`src/app/building/routing.py`, Owner A). Read
`docs/synera/ROUTE_SCORING.md` first; this file records what we found and decided on top of it.

## 1. The published answers use the kit's cost model, not the Discord rule

Synera announced stairs at 2 m per metre climbed and lifts at 15 m + 1 m per metre
(`ROUTE_SCORING.md`). The ground truth they have published doesn't follow that:

- A fresh clone of `Synera-GmbH/industrial-agents-hackathon-2026` is at the same commit as our submodule
  (`cb51a6f`) and has identical data. We didn't introduce this.
- Recomputing every multi-floor route answer (`optimal_length_m`, dev + train, 367 routes) with each model:
  the kit's model (stairs 12 m per floor, lift 15 m + 6 m per floor) matches 367/367; the Discord rule
  matches 11/367. Errand distances in `questions.jsonl` also follow the kit's model.
- **Status: the committee is checking with Synera.** Until they answer, `RouteGraph` keeps the kit's
  costs, so every golden test stays green. Don't switch to the new costs on your own.

Where to switch once confirmed: the constants `STAIRS_COST_PER_FLOOR_M`, `LIFT_WAIT_COST_M` and
`LIFT_COST_PER_FLOOR_M`, plus the cost lines in `RouteGraph._build`. The cost has to use
`floor.elevation_m` instead of `floor.level`. Expect the golden route and errand tests to fail after the
switch: those answers use the kit's model. Test `test_a4_3_*` already checks the new rule and is marked
xfail until then.

To reproduce the check (free, about 1 minute): the script was a one-off in `/tmp/scripts/check_costs.py`
and isn't in the repo. It patches the kit's `BuildingMap.graph` connector weights and compares lengths
against `questions_basic.jsonl`.

## 2. `validate_route`: why Synera would score a route 0

`validate_route(b, space_path, constraints) -> list[str]` in `routing.py`. It returns the reasons a space
sequence is invalid under Synera's rule, or `[]` if it's valid. It checks only the space sequence, because
that's all Synera scores:

- Each step crosses a real door, opening, stair or lift that the constraints allow. A stair or lift may
  skip floors. If stairs and a lift join the same two spaces, the step is step-free.
- No unknown or closed space.
- No production bay passed through; ending in one is fine.

New constants, our reading until Synera answers the open questions in `ROUTE_SCORING.md`:

- `PRODUCTION_BAYS = {production_zone, assembly_zone, additive_zone}`. Warehouse and logistics zones stay
  passable as forklift space.
- `FORKLIFT = Constraints(step_free=True, min_width_m=3.0)`. Aisle-only isn't checked: the data has no
  aisle type.

`RouteGraph` and `validate_route` share one rule for which elements the constraints allow
(`_usable(element, constraints)`). Add a new constraint there so the two can't diverge. The validator
looks connections up in a per-building table (`_connections`): about 10 µs per route.

Measure it (free, about 5 s): `uv run python scripts/synera/validate_routes.py dev --show 10` (or `train`).

| Invalid routes | dev | train |
|---|---|---|
| Synera's published routes | 0 / 252 | 0 / 1,205 |
| Our routes for the same questions | 0 / 252 | 0 / 1,205 |
| Ours, outside → every room, walking | 24 / 5,144 | 9 / 13,150 |
| Ours, outside → every room, forklift | 21 / 134 | 6 / 99 |

Tests: `test_a4_6_validator_accepts_golden_routes` and `test_a4_6_validator_rejects_invalid_routes` (to-do
item 4.6).

## 3. What's still open (Owner A)

1. **`RouteGraph` doesn't apply the production-bay rule.** Every invalid route in the sweep passes through
   a bay. Test 4.1 passes only because the published questions never route through one. Next step: make
   bays destination-only (enter a bay only when it's the goal), then rerun `validate_routes.py` and aim
   for 0 invalid.
2. **Point `scripts/synera/route_metric_check.py` at our `RouteGraph`.** It currently scores the kit's
   `BuildingMap.route`, so its 88% is the kit's number, not ours.
3. **4.4 door-to-door walking, 15 cm off the walls.** This needs Vadim's in-room walking distance.
4. **4.3** waits for the committee (section 1).

## 4. Scoreboard rows we looked at

- `synera-route-rule` (94%): the only misses are the `test_a4_3_*` xfails, which wait for the cost
  decision. The row doesn't measure the bay problem yet.
- `sunday-find-and-route` (`route_with_closure` 28/29): not a routing problem. `RouteGraph` matches
  every published closure route. The miss is in the agent (Mousa); his run report has the question id.
