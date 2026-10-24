# How Synera scores route answers

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
