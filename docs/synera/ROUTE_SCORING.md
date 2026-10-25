# How Synera scores route answers

> **Update (kit 1.1.0, Sat 24 Oct evening):** Synera recomputed every expected answer with this rule
> (door to door 15 cm off the walls, stairs 2 m per metre climbed, lift 15 m per ride + 1 m per metre, no
> route round the outside). `RouteGraph` now implements it and reproduces every train and dev answer. The
> notes below about the old kit model are history.

Announced by Synera in the hackathon channel on Sat 2026-10-24. This is the rule the automatic check uses
for every route answer; build and test routing against it.

## Building model

- A building is a set of spaces (rooms, corridors, aisles, stairwells, lifts) joined by doors, openings,
  stairs and lifts.
- Walking distance is measured inside each space, door to door, keeping about 15 cm off the walls. Paths
  go around corners, never through walls.
- Stairs: 2 m of walking per metre climbed.
- Lift: 15 m (waiting and doors) plus 1 m per metre of height.
- Furniture, equipment and machines are not obstacles. Distances use room and aisle outlines only.
- A route to a machine or other item ends in the room or bay that holds it, not at its exact position.

## Rules for some questions

- **Without stairs:** lifts, ramps and doors only.
- **Forklift:** forklift aisles and openings only (3 m or wider). Pedestrian walkways don't count.
- **Closed or blocked** areas must be avoided.
- **Production bays** are destinations: a route may end in one, but never pass through one as a shortcut.
  **Clarified by Synera on Sun 25 Oct:** a "bay" is any space marked `"through": false` in `building.json`,
  never a room type. No kit building sets the flag, so production, assembly, warehouse and logistics zones
  are crossed like any other space (the expected hall-to-office routes go through them). Delivery and
  reachability counts follow the same rule. `RouteGraph` honours the flag wherever it appears
  (`Space.through`); `data/extra_tricky/*-xt-nothrough` are kit factories with the flag set, for testing.
- **No route** under the rules (e.g. upstairs without stairs and no lift): the correct answer is to say so.

## Scoring

- The answer is the ordered list of spaces the route passes through.
- Each step must go through a real door, opening, stair or lift, and the rules above must hold.
- Synera measures the route with the distance rule above. Our own distance numbers don't affect the score;
  only the sequence of spaces counts.
- Score = best possible length ÷ our length. Within 1% of the best gets full marks. An invalid route
  scores 0.

## What this means for us

- **Validity first.** A wrong step (no real connection, a stair on a step-free route, a narrow door for a
  forklift, a production bay passed through) costs everything; a slightly longer route costs a little.
  Honestly saying "no route" is a scored answer.
- **The kit's cost model is not this rule.** `synera-kit/starter_agent/building_map.py` measures straight
  lines from each space's representative point to its doors, charges stairs 12 m per floor and lifts
  15 m + 6 m per floor, has no width filter and no production-bay rule. It reproduces the dev/train golden
  answers, but space sequences picked with it can differ from the shortest under Synera's rule (wall-hugging
  door-to-door paths, cost per metre of height). `RouteGraph` should rank paths by Synera's rule; check
  against the golden tests that it still agrees with them.
- **Route output** is the space sequence (`Route.space_path`); the length we report is for the user, not the
  score.

## How much the kit's model loses (measured)

`scripts/synera/route_metric_check.py` prototypes Synera's rule: a visibility graph inside each space's
outline inset by 15 cm, door to door, plus the new stair and lift costs. It then scores the space
sequences the kit's model picks for every dev warm-up route (route, step-free, closure, tour):

| | dev warm-up routes |
|---|---|
| Same space sequence as the best under Synera's rule | 182 / 212 |
| Full marks (within 1%) | 187 / 212 (88%) |
| Mean score (best ÷ ours) | 0.966 |
| Worst | 0.35 |

The worst case (`proc-campus-dev-001-Q05`, Toilet 0.16 → Kitchen 1.03): the kit's model takes Staircase 2
(74.6 m under the new rule) instead of Staircase 1 next to the toilet (26.3 m), because it measures every corridor crossing through the corridor's
centre point. Under that model, a staircase next to your door costs the same as one at the far end. Long
corridors and campuses lose the most. So: **the port is a start; ranking by Synera's rule is worth up to
about 12% of the route points**, and the golden route lengths (`questions_basic.jsonl`) are the kit's
model, so `test_route*` checks the port, not the score.

Assumptions in the prototype (ask Synera, see below): a route starts and ends at the room's representative
point; walking outside between entrances costs 0 m; a stair or lift is entered at the stairwell's
representative point.

## Open questions for Synera

1. Where does a route start and end inside the first and last room: a fixed point (which?), the nearest
   door, or the item's position for "to the machine" questions?
2. Is walking outside between entrances measured, and how?
3. ~~Answered Sun 25 Oct: bays are `"through": false` spaces, see above.~~ Which spaces are **forklift aisles** (the data has 112 corridors named "aisle", no aisle type) and which
   are **production bays** (`production_zone`, `assembly_zone`, `additive_zone`, `warehouse_zone`,
   `logistics_zone`?)? Can a pedestrian route pass through a bay, or only a forklift one?
4. "Without stairs: lifts, ramps and doors only." Is there ramp data, or are ramps just openings marked
   `step_free`?
5. The exact format for the ordered list of spaces in the answer: ids, refs or names? Does it include
   `outside`, and repeated lift or stair spaces per floor?
