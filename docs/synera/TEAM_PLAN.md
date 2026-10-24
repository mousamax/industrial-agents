# Team plan: four builders, one repo, no collisions

Team: **Mousa, Magnus, Maximilian, Vadim**. Code freeze **Sun 13:00**. Read `README.md` (scoring) and
`DATA_MODEL.md` (the data) in this folder first.

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

Pick names in the first sync; suggested fits in brackets.

| Role | Owns (only they edit) | Points it drives |
|---|---|---|
| **A · Graph & state** [CS or maths] | `src/app/building/routing.py`, `state.py`, `queries.py` | errand, wide delivery, what-if door (41 of 145 test questions), every route in the Sunday benchmark, Levels 2–3, EDIT/REACT |
| **B · Geometry & build** [maths or physics] | `src/app/building/geometry.py`, `build.py` | areas, shared walls, stacking, 3D distance (104 of 145 test questions), Level 4 |
| **C · Agent harness & benchmark** [Mousa] | `src/app/agents/building_guide/`, `src/app/api/`, `BENCHMARK.md`, evals, `model.py`, `view.py` | turns all of the above into scored answers (a wrong tool call scores 0), Interaction 25 |
| **D · Product & story** [physics/aerospace or Vadim] | `web/`, `docs/synera/pitch/` | Interaction (voice, UX), Creativity 10, Submission demo 10, the 3-minute pitch |

Why this split: the 55 automatic points need A and B exact, but they only count if C's agent calls them
correctly and formats the answer; D is the other 45 points and the stage. Nobody waits for anybody: A and B
test against golden answers, C tests tool choice against the question `inputs`, D builds on the scene
endpoint that already works.

### A · Graph & state
- **Start:** port `synera-kit/starter_agent/building_map.py` `graph()` + Dijkstra into `RouteGraph` (it
  already reproduces all 369 warm-up answers), add `min_width_m` and `closed`. Then `reachable`,
  `distances_from`, `queries.*`, then `BuildingState`.
- **Done when:** `GOLDEN_SAMPLE=all uv run pytest tests/building -k "route or graph"` is green on dev and
  train.
- **Needs from B:** `geometry.shared_wall_midpoint` (what-if door) and `rooms_by_area` (errand target).
  Until they land, use a placeholder locally and don't commit it into B's file.
- **Then:** multi-building walkways (Level 3), the forklift/logistics add-in if we pick it.

### B · Geometry & build
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

### D · Product & story
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

### Agent tools (C builds them on the interfaces; D renders their `view`)

| Tool | Arguments | Backed by | Covers |
|---|---|---|---|
| `building_overview` | | model, state | context: floors, lifts, entrances, closures |
| `find` | `query`, `floor?` | `b.resolve`, assets | FIND, locate |
| `count_items` | `item_type`, `floor?` | `b.assets_of_type` | count |
| `route` | `start`, `destination`, `via=[]`, `step_free`, `min_width_m?` | `RouteGraph` + state closures | ROUTE, step-free, closures, tours |
| `rooms` | `floor`, `room_type?`, `sort="area"` | `geometry.rooms_by_area`, `total_area` | largest room, total area |
| `room_facts` | `room` | `geometry.*` | area, centre, neighbours, above/below |
| `distance_3d` | `first`, `second` | `geometry.straight_line_3d` | 3D distance |
| `reachable_rooms` | `start`, `floor`, `step_free`, `min_width_m?`, `closed=[]` | `queries.*` | deliveries, wheelchair reach |
| `errand` | `start`, `item_type`, `then_to_room_type`, `floor`, `can_use_stairs` | `queries.errand` | errand |
| `close` / `reopen` | `element` | `state` | REACT |
| `add_door` / `move_item` | spaces / item, place | `state` | EDIT, "make things move" |
| `what_if_door` | `first`, `second`, `affected_floor` | `queries.what_if_door` | what-if |

Every tool returns compact JSON for the model plus `view: [...]` for the UI, never raw polygons.

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
| Sat now | Read this, pick roles, ask Synera the open questions (`README.md`) |
| Sat 18:00 | A: routes green. B: areas + 3D green. C: tools on interfaces, tool-choice eval running. D: floors drawn, chat connected |
| Sat 22:00 | All golden kinds ≥ 90% on dev. First full dev run through the agent. Routes animated in the UI. Voice prototype |
| Sun 08:00 | Golden green on train. First test-set CSV. Level 4 prototype. Demo script written |
| Sun 11:00 | Feature freeze. Sunday building run (when Synera releases it). Record the demo video |
| Sun 13:00 | Code freeze: video, technical explanation, repo with BENCHMARK.md, answers. Submit |

## Commands

```bash
uv run pytest tests/building -q -rs                     # golden scoreboard (dev sample)
GOLDEN_SAMPLE=all uv run pytest tests/building -q       # all dev questions
uv run uvicorn app.api.main:app --reload                # API for the UI on :8000
uv run ia chat building_guide                           # talk to the agent in the terminal
uv run ia eval building_guide                           # agent with the judges' scorer
```
