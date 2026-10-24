# Green tests are not points: where the score is now, and what's left

Written Sat 24 Oct, evening. Read this before you tell your Claude Code "make the tests pass".

## Where we stand

| What we measure | Result | Says |
|---|---|---|
| Golden tests, every question, dev + train (`tests/building/test_golden.py`) | **2,118 / 2,118** | Our maths reproduces Synera's **published** answers |
| Agent end to end with the judges' scorer, dev (Mousa's run, role C) | **138 / 138** | The agent reads the question and calls the right tool |
| Same, train | **515 / 517** | |

So the **145 test-set questions are close to solved**. They are one part of the 55 automatic points. The
rest is scored on things none of our tests check:

| Automatic check (55 points in total) | Scored by | Our tests check it? |
|---|---|---|
| Test-set CSV (145 questions) | `benchmark/score.py` | **Yes**, ~100% |
| **Sunday building**: one unseen building + questions, "including the best routes" | Synera's route rule (`ROUTE_SCORING.md`) | **No**: golden route lengths use the kit's model, not this rule |
| **Hidden checks, levels 1–4**: valid routes, route length vs best, buildings built from plans | Same rule; an unpublished plan comparison | **No** |

Measured with Synera's announced rule (`scripts/synera/route_metric_check.py`), the routes our graph picks
get full marks on **88%** of dev routes, with a worst case of 0.35. Forklift routes, production bays and
"no route" answers aren't checked at all yet. **That's the gap, and it's Magnus's and Vadim's.** The
remaining 45 points (interaction, creativity, demo) are Mousa's and Maximilian's.

## The trap with Claude Code

"Make `tests/building` pass" is done. If you ask for it again, Claude will polish what already scores. Your
job is the part Claude can't do alone: **decide what the real target is, write a check that measures it,
then let Claude iterate against that check.** In practice:

1. **Write the target down first**, in your own words, in the prompt: "Synera scores a route as the
   sequence of spaces, measured door to door 15 cm off the walls, stairs 2 m per metre climbed…". Point
   Claude at `docs/synera/ROUTE_SCORING.md`, not at the golden tests.
2. **Build the measurement before the fix.** Ask Claude for a script or test that reports the score you
   care about (mean best ÷ ours, % within 1%, invalid count) on dev. Then ask it to improve that number.
3. **Read the worst five cases yourself.** Draw them and look: ask Claude for a plot of the floor and both
   routes (the kit's `starter_agent/viewer.py` shows how), or use the web app once it draws floors.
   Your judgment on "is this the route a person would take" is the thing Claude lacks.
4. **Hold out a split.** Tune on train, report on dev. The Sunday building is unseen; anything that only
   works because it knows the 280 buildings scores 0 there.
5. **Ask Synera when the rule is ambiguous** (open questions at the end of `ROUTE_SCORING.md`). Don't let
   Claude guess. Write the assumption down where the code makes it.
6. **Keep the golden tests green** while you change the cost model. They check the port and the queries;
   you add new checks alongside them, you don't replace them.

## Magnus (A · graph & state): make every route valid and the best under Synera's rule

The port matches the kit; the kit isn't the scorer. Think about, in order:

1. **Validity (an invalid route scores 0).** A `validate_route(b, space_path, constraints)` that checks
   every step crosses a real door, opening, stair or lift, and that every rule holds: no stairs when step
   free, ≥ 3 m openings and aisles only for forklifts, no closed element, no production bay passed
   through. Run it on every route we output, including the agent's.
2. **Rank by Synera's distance.** Walk door to door inside each space, along the outline inset by 15 cm,
   around corners. Stairs 2 m per metre of height (use `floor.elevation_m`), lift 15 m + 1 m per metre.
   The prototype in `scripts/synera/route_metric_check.py` shows the idea (a visibility graph per space)
   and is the yardstick. Ask Vadim for the in-room walking distance as a geometry function, so it's
   exact and shared.
3. **New constraints.** A forklift mode (`min_width_m=3.0` plus aisles only) and "production bays are
   destinations only". Both need a data decision (which spaces are aisles and which are bays): look at the
   factories, then confirm with Synera.
4. **"No route" is an answer.** Return `None` cleanly and let the agent say so.
5. **Unseen buildings.** OSM overlaps, connectors that skip levels, campuses with outside walking: run the
   check per source and look at the worst ones.

**Your number:** % of dev routes within 1% of the best under Synera's rule, with 0 invalid. Today 88%,
target 100%. Keep `GOLDEN_SAMPLE=all uv run pytest tests/building -k "route or graph"` green as you go.

## Vadim (B · geometry & build): the in-room distance, and Level 4 from real plans

Geometry passes every published question. The open points are elsewhere:

1. **Walking distance inside a space** (Magnus needs it): `geometry.walk_distance(b, space_id, a, b)`,
   the shortest path between two points inside the outline inset by 15 cm. It's a geometry problem
   (non-convex rooms, holes, OSM self-overlaps, doors that sit slightly off the wall). Test it on
   L-shaped corridors and factory aisles, and keep it fast: Magnus calls it for every pair of doors.
2. **Level 4 from the input Synera will actually give.** `draft_from_plans` reads the kit's SVGs, which
   already encode the true building. The brief says "feed it a floor plan", and each building ships
   `floor_<n>_plain.png`. Find out (question 2 in `README.md`) whether the check uses the SVG, the PNG or
   both. If it's the PNG: rooms, doors and stairs from pixels (classic vision plus Claude vision), scored
   with `build.compare` on the 200 train buildings.
3. **Room types the plans don't show** (23% right today). These matter for "nearest kitchen" questions
   on a drafted building. Infer them from shape, size, position and neighbours, or ask in the
   conversation ("is this room a kitchen?"), which also earns interaction points.
4. **Robustness on unseen buildings.** Run the geometry check per source (procedural, OSM, Swiss,
   ResPlan) and look at the closest margins, not only the failures. The Sunday building could be any
   source.

**Your number:** `build.compare` on train from the input Synera confirms (rooms, doors, connectors,
room types), plus `walk_distance` agreeing with the prototype on dev.

## Mousa (C) and Maximilian (D)

- **Mousa:** the agent already scores the test set. Next: output routes as the space sequence Synera
  asks for, the Sunday runner in their format, tricky-user interaction cases, and the `Answer:` format on
  question kinds we haven't seen.
- **Maximilian:** interaction (25), creativity (10) and demo (10) are the 45 points no test measures.
  The demo video is due before Sun 11:00.

## How to run the evaluations (everyone)

All need `ANTHROPIC_API_KEY` in `.env` except the first two.

```bash
uv run pytest -q                                                    # everything, no API calls, seconds
GOLDEN_SPLIT=train GOLDEN_SAMPLE=all uv run pytest tests/building -q  # golden: published answers, free
uv run python scripts/synera/route_metric_check.py synera-kit/data/dev  # routes under Synera's rule, free, ~3 min

# the agent end to end with the judges' scorer (credits: ~$0.10-0.25 per question)
cd synera-kit && uv run --project .. --env-file ../.env python -m benchmark.evaluate data/dev \
  --per-kind 3 --agent app.agents.building_guide.benchmark:answer
```

Mousa's parallel runner (`uv run python -m app.agents.building_guide.bench dev`, same scorer, much
faster) lands with the next role C push; `BENCHMARK.md` will list it.

| Command | Tells you | Doesn't tell you |
|---|---|---|
| `pytest tests/building` | Our maths matches the published answers | Anything about the Sunday route rule or Level 4 |
| `route_metric_check.py` | How close our routes are to Synera's best | Validity rules (forklift, bays) until Magnus adds them |
| `benchmark.evaluate` / `bench` | The agent's real score on known question kinds | Unseen question kinds, the Sunday format |
