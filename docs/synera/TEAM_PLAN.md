# Team plan: four builders, one repo, no collisions

Team: **Mousa, Magnus, Maximilian, Vadim**. Code freeze **Sun 13:00**. Read `README.md` (scoring),
`ROUTE_SCORING.md` (how route answers are scored) and `DATA_MODEL.md` (the data) in this folder first.

## The idea in one picture

The product is a chat (and voice) agent. The harness gives the model instructions, the building context
and tools; the tools are deterministic code, so every number is computed, never guessed.

```
 person ── text / voice ──► UI (web)  ◄── view commands (route, highlight, floor)
                              │
                     POST /buildings/{id}/chat
                              ▼
                     agent harness  (prompt, context, tool choice, asks back, "Answer:" line)
                              │ tools
        ┌─────────────────────┼──────────────────────┬─────────────────────┐
   geometry.py           routing.py / queries.py   state.py              build.py
   areas, walls,         shortest paths, tours,    closures, new doors,  Level 4: building
   stacking, 3D          reachability, errands,    moved assets, undo    from floor plans
                         what-if doors
        └─────────────────────┴──────────┬───────────┴─────────────────────┘
                                   model.py (shared, done)  ◄── synera-kit/data/*/building.json
```

The train/dev questions come with structured `inputs` and golden answers, so each question splits into two
jobs we test separately:

1. **Understand:** question text → the right tool with the right arguments (agent harness).
2. **Compute:** tool + arguments → the golden answer (domain code). `tests/building/test_golden.py` checks
   this against Synera's answers, no model needed.

## Roles

Agreed Sat 24 Oct.

| Role | Owner | Owns (only they edit) | Points it drives |
|---|---|---|---|
| **A · Graph & state** | **Magnus** | `src/app/building/routing.py`, `state.py`, `queries.py` | errand, wide delivery, what-if door (41 of 145 test questions), every route in the Sunday benchmark, Levels 2–3, EDIT/REACT |
| **B · Geometry & build** | **Vadim** | `src/app/building/geometry.py`, `build.py` | areas, shared walls, stacking, 3D distance (104 of 145 test questions), Level 4 |
| **C · Agent harness & benchmark** | **Mousa** | `src/app/agents/building_guide/`, `src/app/api/`, `BENCHMARK.md`, evals, `model.py`, `view.py`, `uv.lock` | turns all of the above into scored answers (a wrong tool call scores 0), Interaction 25 |
| **D · Product & story** | **Maximilian** | `web/`, `docs/synera/pitch/` | Interaction (voice, UX), Creativity 10, Submission demo 10, the 3-minute pitch |

Shared files (`CLAUDE.md`, this plan, `src/app/building/__init__.py`, `tests/`): small edits are fine, say so
in the chat. Each owner adds tests for their own interfaces.

## What each of us optimizes for

The scoring (`README.md`): **55** automatic benchmark, **25** interaction, **10** creativity, **10** submission
demo. Then the top four pitch on stage. One metric per person, so trade-offs are easy: when in doubt, do what
moves your number.

| Owner | Optimize for | Measured by | Don't spend time on |
|---|---|---|---|
| **Magnus** (A) | **Exact routes and graph answers on unseen buildings.** The Sunday building is mostly routes; one wrong edge cost or a missing constraint fails every question on it | `GOLDEN_SPLIT=train GOLDEN_SAMPLE=all uv run pytest tests/building -k "route or graph"` at 100%, then dev | Clever algorithms: Dijkstra on a few hundred nodes is instant. Port the kit's model exactly, then constraints, then state |
| **Vadim** (B) | **Geometry accuracy across all four sources**, because geometry is 104 of 145 test questions (72% of the automatic points). OSM overlaps and Swiss duplicate names are where points leak | `GOLDEN_SAMPLE=all uv run pytest tests/building -k geometry` on train and dev, broken down by source | Level 4 (`build.py`) before geometry is green, and before Synera tells us how it is checked |
| **Mousa** (C) | **End-to-end score: question text → right tool and arguments → correctly formatted `Answer:` line**, plus behaviour in the scripted tricky-user conversation. A correct computation behind a wrong tool call scores 0 | Official evaluator on dev (`BENCHMARK.md`), tool-choice accuracy against the train `inputs`, interaction cases in `ia eval building_guide` | Reimplementing A's or B's maths in tools: call the interfaces and report gaps to the owner |
| **Maximilian** (D) | **What the jury sees and hears:** the scripted conversation (25), Level 5 add-ins (10), the demo video and technical explanation (10), and the 3-minute pitch if we make the final | One demo that runs end to end on a building nobody prepared, by voice, with the route drawn; a demo video recorded before Sun 11:00 | Visual polish nobody sees in 3 minutes; features the demo story doesn't use |

How the numbers connect: Magnus and Vadim make each answer *correct*, Mousa makes it *scored* (asked
right, called right, formatted right), Maximilian makes it *seen*. The stage only happens if the
submission ranks top four, so the 55 come first; but the interaction and demo points (45) are the
cheapest left once the benchmark is above ~90%.

Why this split: the 55 automatic points need A and B exact, but they only count if C's agent calls them
correctly and formats the answer; D is the other 45 points and the stage. Nobody waits for anybody: A and B
test against golden answers, C tests tool choice against the question `inputs`, D builds on the scene
endpoint that already works.

### A · Graph & state (Magnus)
- **Start:** port `synera-kit/starter_agent/building_map.py` `graph()` + Dijkstra into `RouteGraph` (it
  already reproduces all 369 warm-up answers), add `min_width_m` and `closed`. Then `reachable`,
  `distances_from`, `queries.*`, then `BuildingState`.
- **Done when:** `GOLDEN_SAMPLE=all uv run pytest tests/building -k "route or graph"` is green on dev and
  train.
- **Needs from B:** `geometry.shared_wall_midpoint` (what-if door) and `rooms_by_area` (errand target).
  Until they land, use a placeholder locally and don't commit it into B's file.
- **Then:** multi-building walkways (Level 3), the forklift/logistics add-in if we pick it.

### B · Geometry & build (Vadim)
- **Start:** `rooms_by_area` and `total_area` (a few lines each, 54 test questions), `centroid_3d` /
  `straight_line_3d`, then `stacked`, then `shared_walls` (tolerances; OSM overlaps). `shared_wall_midpoint`
  early, because A needs it.
- **Done when:** `GOLDEN_SAMPLE=all uv run pytest tests/building -k geometry` is green on dev and train.
- **Then:** `build.validate`, then `build.draft_from_plans` (Claude vision, PNG → building.json), scored
  with `build.compare` against the 200 train buildings. Ask Synera how Level 4 is checked before going deep.

### C · Agent harness & benchmark (Mousa)
- **Start:** move `building_guide` tools from the kit's `BuildingMap` onto the interfaces below. Build an
  eval from train: question text → expected tool + arguments (the `inputs`), so tool-choice accuracy is
  measured before A and B finish.
- **Done when:** the official evaluator (`BENCHMARK.md`) scores dev at the level the golden tests allow,
  and the tricky-user interaction cases pass (`ia eval building_guide`).
- **Then:** the Sunday runner in whatever format Synera announces, the test-set CSV, prompt caching and
  cost, conversation memory per chat (one `BuildingState` per conversation).

### D · Product & story (Maximilian)
- **Start:** a web app (Lovable or Vite + React + three.js, your call) in `web/`: building picker
  (`GET /buildings`), floors drawn from `GET /buildings/{id}/scene`, a chat panel on
  `POST /buildings/{id}/chat` (Vercel AI SDK `useChat`), and route animation from `ShowRoute` view commands.
- **Then:** voice in/out (ElevenLabs credits; counts for Interaction), stacked isometric floors, the demo
  script, the demo video, the technical explanation, the pitch.
- **Done when:** the 3-minute demo runs end to end on a building the jury hasn't seen.

## Contracts

The code is the contract: typed stubs with docstrings in `src/app/building/`. Summary:

| Interface | Signature | Owner |
|---|---|---|
| model (done) | `load_building(id_or_dir) -> Building`; `b.resolve(text, floor=None) -> [Space]`; `b.spaces_on(floor_id, type=, room_type=)`; `b.label(space_id)`; `space.area / anchor / centroid` | C |
| routing | `RouteGraph(b, Constraints(step_free, min_width_m, closed))`; `.shortest(a, b) -> Route \| None`; `.tour(stops) -> Tour`; `.distances_from(sources) -> {space: m}`; `.reachable(start) -> {space}` | A |
| state | `BuildingState(b).apply(Close \| Reopen \| AddDoor \| MoveAsset) -> str`; `.building`; `.constraints()`; `.undo()`; `.export()` | A |
| queries | `nearest_with_asset`, `errand -> Errand(pickup, target, length)`, `deliverable_rooms`, `step_free_reachable_rooms`, `what_if_door` | A |
| geometry | `rooms_by_area`, `total_area`, `shared_walls`, `shared_wall_rooms`, `shared_wall_midpoint`, `stacked`, `centroid_3d`, `straight_line_3d` | B |
| build | `validate(raw) -> [Issue]`; `draft_from_plans(images, name=, levels=)`; `compare(pred, truth)` | B |
| view (done) | `scene(b) -> Scene`; `route_view(b, route) -> ShowRoute`; ViewCommand = `Highlight \| ShowRoute \| FocusFloor \| SceneChanged` | C + D |
| HTTP (done) | `GET /buildings`, `GET /buildings/{id}/scene`, `POST /buildings/{id}/chat` | C |

### Agent tools (C, in `src/app/agents/building_guide/tools.py`; D renders their ids)

One agent, 14 small tools it composes. Every number comes from code; the model picks tools and writes the
reply. `tests/building/test_agent_tools.py` checks each tool against every dev and train golden answer.

| Tool | Arguments | Backed by | Covers |
|---|---|---|---|
| context (not a tool) | | `overview()` | floors, room and item type vocabulary, lifts, entrances (static, so it caches) |
| `find` | `query`, `floor?` | `b.resolve`, assets | FIND, locate |
| `room_details` | `room` | `geometry.*` | area, doors and widths, shared walls, above/below, items |
| `list_rooms` | `floor`, `room_type?` | `geometry.rooms_by_area` | largest room, total area |
| `count_items` | `item_type`, `floor?` | `b.assets_of_type` | count |
| `distance_3d` | `first`, `second` | `geometry.straight_line_3d` | 3D distance |
| `route` | `start`, `destination`, `via=[]`, `step_free`, `min_width_m?` | `RouteGraph.tour` + session closures | ROUTE, step-free, tours, errands |
| `nearest` | `start`, `item_type` or `room_type`, `step_free` | `RouteGraph.distances_from` | nearest coffee, errand pickup |
| `reachable_rooms` | `start`, `floor`, `step_free`, `min_width_m?`, `closed=[]` | `RouteGraph.reachable` | deliveries, wheelchair reach |
| `what_if` | `floor`, `add_door_between?`, `close=[]` | `geometry.shared_wall_midpoint` + `distances_from` | what-if door, "what changes if X closes" |
| `calculate` | `expression` | safe arithmetic | every derived number (differences, sums, ratios) |
| `set_closed` | `element`, `closed` | `BuildingState` (Close/Reopen) | REACT |
| `add_door` / `move_item` | spaces or item + room, `confirmed` | `BuildingState` (AddDoor/MoveAsset) | EDIT, "make things move": proposes first, applies only after the person's yes in a later turn |
| `undo_last_change` | | `BuildingState.undo` | undo |

`list_rooms`, `reachable_rooms` and `what_if` take `scope`: `rooms` (default, what Synera's counts mean),
`rooms_and_halls` or `all_spaces`.

Results carry space ids for the UI: `highlight` (spaces to light up) and, for routes, `space_path` and
`element_path` per leg; draw them from `GET /buildings/{id}/scene`. Until Owner A's `RouteGraph` lands, the
tools use a stand-in port of the kit's route model (`building_guide/graph.py`); it switches over by itself.

## Rules so we don't crash into each other

1. **Only edit files your role owns.** Need a change elsewhere? Ask the owner in the team chat.
2. **Signatures are frozen.** Changing a function or dataclass in `src/app/building/` means: post the change
   in the chat, Mousa merges it, everyone pulls. Adding a new function is fine.
3. **Everyone works on `main`** with small commits. `git pull --rebase` before you push.
   `uv run pytest -q` must pass before you push (about 3 s, no API calls).
4. **Dependencies:** ask Mousa to add them, so `uv.lock` has one writer.
5. **Never edit `synera-kit/`.** Import from it. The Sunday release arrives there.
6. **Never hard-code a building, room or question.** The Sunday building is unseen.
7. **Tell your coding agent the role:** "I'm Owner B, only edit geometry.py and build.py". `CLAUDE.md` says
   the same.

## Timeline

| When | Checkpoint (10 min standup, everyone) |
|---|---|
| Sat now | Read this (roles agreed), ask Synera the open questions (`README.md`) |
| Sat 18:00 | A: routes green. B: areas + 3D green. C: tools on interfaces, tool-choice eval running. D: floors drawn, chat connected |
| Sat 22:00 | All golden kinds ≥ 90% on dev. First full dev run through the agent. Routes animated in the UI. Voice prototype |
| Sun 08:00 | Golden green on train. First test-set CSV. Level 4 prototype. Demo script written |
| Sun 11:00 | Feature freeze. Sunday building run (when Synera releases it). Record the demo video |
| Sun 13:00 | Code freeze: video, technical explanation, repo with BENCHMARK.md, answers. Submit |

## Commands

```bash
uv run ia scoreboard                                    # points per person and row -> SCOREBOARD.md
uv run ia scoreboard --run scenarios                    # re-measure one agent suite (or --run all)
uv run pytest tests/building -q -rs                     # golden scoreboard (dev sample)
GOLDEN_SAMPLE=all uv run pytest tests/building -q       # all dev questions
uv run uvicorn app.api.main:app --reload                # API for the UI on :8000
uv run ia chat building_guide                           # talk to the agent in the terminal
uv run ia eval building_guide                           # agent with the judges' scorer
```
