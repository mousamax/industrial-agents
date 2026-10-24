# Synera track: Talk to Your Building

Team: Mousa, Magnus, Maximilian, Vadim. Sources: `challenges/Synera/` (brief PDF, kickoff transcript) and the
kit, which is the `synera-kit/` git submodule (Synera-GmbH/industrial-agents-hackathon-2026).

## The challenge in one paragraph

Give any building a map you can talk to. Feed it a floor plan, then anyone can ask: **find** ("where is
the 3D printer?"), **route** ("lobby to the CNC mill on floor 2 without stairs"), **edit** ("add a door
between 2.03 and 2.04"), **react** ("the east lift is closed, what changes?"). The agent is the interaction
layer; the core is deterministic: a graph of rooms, doors, stairs and lifts plus shortest paths. The
kickoff talk framed it for industry: where is the material delivered yesterday, forklift routes around
blocked aisles, keeping the map current as machines move.

## How we get on stage (read this before deciding anything)

There are no pitch rooms. Synera ranks **all online submissions** and picks **four finalists**. Only
finalists pitch (3 min + 1 min Q&A). So the stage comes second; the submission decides whether we get
there.

| Part | Points | What it means for us |
|---|---|---|
| Does it work | **55** | Automatic: the 145-question test CSV, the Sunday unseen-building benchmark, plus hidden checks on levels 1–4 (answers, valid routes, route length, buildings built from plans) |
| Interaction | 25 | A scripted conversation with tricky users, rated on a rubric: asks only what it needs, shows what it understood, gives followable directions. **Voice counts.** |
| Creativity | 10 | Level 5 add-ins, anything they didn't think of |
| Submission demo | 10 | Demo video + short technical explanation |

**Deliverables by code freeze (Sun 13:00):** demo video, technical explanation, repo with `BENCHMARK.md`,
the test-set CSV, and our Sunday-building answers.

### Where the 55 points are

The scored test questions (`synera-kit/data/test/questions.csv`, 145) by kind:

| Kind | Count | Needs |
|---|---|---|
| `floor_area_total` | 33 | polygon areas, filter by room type and floor |
| `shared_walls_count` | 26 | polygon geometry: ≥1 m of outline within 0.35 m |
| `largest_room` | 21 | polygon areas |
| `errand` | 20 | nearest-item search + routing (carry constraints) |
| `wide_delivery_count` | 18 | door widths + step-free reachability |
| `straight_line_3d` | 15 | centroids + floor elevations |
| `room_stacked` | 9 | polygon overlap between floors |
| `what_if_door` | 3 | graph edit + nearest-entrance distances across a floor |

Sources: 66 procedural, 32 OSM, 30 Swiss Dwellings, 17 ResPlan. **54 of 145 (37%) need nothing but room
areas.** The kit's starter tools score under 5% on purpose: they expose search and routing, not geometry.
Every kind has an exact definition in `synera-kit/README.md` §4, and train/dev answers to check against.

## What is already wired up

- `synera-kit/`: their kit as a submodule. **Sunday release: `git submodule update --remote synera-kit`.**
- `src/app/agents/building_guide/`: their starter agent ported to our stack (same tools, same prompt). It
  gets `ia chat`, `ia ui`, the API, Langfuse traces and `ia eval` with the judges' scorer
  (`SyneraScore`). It's a baseline to start from, not a decision about the design.
- `docs/synera/BASELINE.md`: floor benchmark results and failure analysis on `train` (`evals/baselines/train_floor_per_kind_1.jsonl`) and `dev` (`evals/baselines/building_guide.json`).
- Their `BuildingMap` (exact route model) and `benchmark.score` are importable from our code.
- `BENCHMARK.md`: the official run command (format still to confirm, see questions below).

```bash
git submodule update --init                       # once after cloning (~300 MB with data)
uv sync
uv run ia chat building_guide                     # talk to proc-factory-dev-003 (DEFAULT_BUILDING)
uv run ia run building_guide '{"building": "proc-campus-dev-000", "question": "Where is the library?"}'
uv run ia eval building_guide                     # 9 scored kinds + 1 interaction case, judges' scorer
cd synera-kit && uv run --project .. python -m starter_agent.evaluate data/dev --oracle   # free: route model check
```

## Questions to ask Synera (their AI person is on site both days)

Answers to these change what we build, so ask early:

1. **Sunday format.** The PDF says `answers.json` with node-id routes and length in metres; the kit README
   says a CSV of full replies with an `Answer:` line. Which one for the Sunday building, and when exactly is
   it released? Is there a time limit on our run?
2. **Level 4 check.** "Correct buildings built from plans" is scored automatically. What is the input
   (PNG only? SVG? a partial `building.json`?) and how is the result compared?
3. **Interaction rubric.** Who runs the scripted conversation, on what (our deployed app, the video, a
   text endpoint)? Text or voice? Can we see one example of a "tricky user"?
4. **Multi-building (level 3).** The data has one building per folder. How should walkways between
   buildings be represented, and is it tested?
5. **Real users.** Of the personas (new employee, wheelchair user, service technician, facility manager,
   forklift driver, logistics), whose problem matters most to Synera's customers? What do they do today?
6. **Fit with Synera.** Would they want this as a node or workflow in their platform? What would make them
   say "we'd use that" rather than "nice demo"?
7. **Models and cost.** Any limits on models or spend for the benchmark run?

## Discovery → value proposition → journeys → build

Suggested order for the solution session (time-box it, ~60–90 min):

1. **Problem and user.** Pick one primary persona and one painful moment (e.g. a technician with a call
   about press line 4, or a facility manager closing a lift). What does it cost them today?
2. **Value proposition.** One sentence: for <user> who <pain>, our <product> lets them <outcome>, unlike
   <today>.
3. **User journey.** The 3-minute demo story, step by step, that shows the value. It must include the four
   question types and at least one tricky-user moment (vague request, wrong room number, change of mind).
4. **Then build**, protecting the 55 points first.

How we split the work (four people, interfaces, file ownership, timeline): [TEAM_PLAN.md](TEAM_PLAN.md).
