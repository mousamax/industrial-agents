# Synera Track — Baseline Benchmark Results

This document records the floor benchmark results for the unmodified starter agent (`synera-kit/starter_agent`) and its port onto our stack (`src/app/agents/building_guide/`), before adding geometry and reachability tools.

## 1. Summary Across Splits & Question Sets

| Run | Question Set | Split | Cases | Score | Cost (USD) | Artifact / Log |
|---|---|---|---|---|---|---|
| `starter_agent.evaluate --oracle` | Warm-up (`questions_basic.jsonl`) | `train` | 1,761 | **1,761 / 1,761 (100%)** | \$0.00 | Deterministic tool check |
| `starter_agent.evaluate --oracle` | Warm-up (`questions_basic.jsonl`) | `dev` | 369 | **369 / 369 (100%)** | \$0.00 | Deterministic tool check |
| `benchmark.evaluate --per-kind 1` | Scored Benchmark (`questions.jsonl`) | `train` | 9 (1 per kind, seed=7) | **1 / 9 (11.1%)** | \$3.21 | `evals/baselines/train_floor_per_kind_1.jsonl` |
| `uv run ia eval building_guide --save-baseline` | Scored Benchmark + Interaction (`evals.yaml`) | `dev` | 9 benchmark + 1 interaction | **3 / 9 (33.3%)** benchmark, **1 / 1** interaction | \$1.87 | `evals/baselines/building_guide.json` |

---

## 2. Warm-Up Questions (`questions_basic.jsonl`) — Oracle Verification

Commands run:
```bash
cd synera-kit
uv run --project .. python -m starter_agent.evaluate data/train --oracle
uv run --project .. python -m starter_agent.evaluate data/dev --oracle
```

| Warm-up Kind | `train` (200 buildings) | `dev` (40 buildings) | Notes |
|---|---|---|---|
| `locate` | 453 / 453 | 100 / 100 | Exact asset lookup |
| `count` | 199 / 199 | 40 / 40 | Asset count by type and floor |
| `route` | 600 / 600 | 120 / 120 | Shortest path matches to `≤ 1%` |
| `route_step_free` | 200 / 200 | 40 / 40 | Step-free filter matches ground truth |
| `route_with_closure` | 109 / 109 | 29 / 29 | Closure + rerouting matches ground truth |
| `tour` | 200 / 200 | 40 / 40 | Multi-stop tour matches ground truth |
| **Total** | **1,761 / 1,761 (100%)** | **369 / 369 (100%)** | Confirms `BuildingMap` route cost model is exact |

---

## 3. Scored Benchmark on `data/train` (`--per-kind 1`)

Command run:
```bash
cd synera-kit
uv run --project .. --env-file ../.env python -m benchmark.evaluate data/train --per-kind 1 --name train_floor_per_kind_1
```
Saved in repo at: `evals/baselines/train_floor_per_kind_1.jsonl`

* **Model**: `claude-opus-5-5` (`effort="medium"`)
* **Total Tokens**: `605,637` input (`175,832` cache read, `18,608` cache write), `33,174` output
* **Total Cost**: **\$3.21** (\$0.36/question average)

| Question ID | Kind | Result | Agent `Answer:` Line | Expected | Root Cause |
|---|---|---|---|---|---|
| `swiss-18196-H03` | `errand` | **OK** *(lucky)* | `44.31` | `44.58` (±1.0 m) | Guessed `Bedroom 0.05` (full-size bed) instead of actual largest bedroom `Bedroom 0.01`, but route distances (`44.31 m` vs. `44.58 m`) fell within the `±1.0 m` tolerance. |
| `resplan-13024-H02` | `floor_area_total` | **BAD** | `unknown. The tools don't give room sizes...` | `49.2` (±5%) | Tools do not expose polygon areas. |
| `swiss-2084-H01` | `largest_room` | **BAD** | `Unknown – my tools don't include room areas...` | `5.02`, `8.12 m²` | Tools do not expose polygon areas. |
| `osm-way13507441-H05` | `room_stacked` | **BAD** | `Unknown – my building data can't show which room is directly above...` | `1.11` (`Gemeindebibliothek Ottobrunn`) | Tools do not expose 2D polygon coordinates to compute floor-to-floor intersection. |
| `proc-factory-train-006-H05` | `shared_walls_count` | **BAD** | `cannot be determined with the available tools` | `6` | Tools do not expose polygon boundaries or shared wall lengths. |
| `osm-way58216509-H03` | `step_free_reachable_count` | **BAD** | `81` | `64` | Closed Lift 2 and brute-forced routes across Floor 2, but counted corridors, stairwells, and lift landings (`81` spaces) instead of just rooms (`64`). |
| `proc-campus-train-011-H05` | `straight_line_3d` | **BAD** | `unknown. My tools don't provide room centroids or floor heights...` | `43.67` (±2%) | Tools do not expose polygon centroids or floor `elevation_m`. |
| `osm-way58216509-H07` | `what_if_door` | **BAD** | *(empty string `""`)* | `40` | Hit `MAX_TOOL_ITERATIONS = 20` while trying to brute-force routes room-by-room on a 180-space floor. |
| `swiss-1444-H02` | `wide_delivery_count` | **BAD** | `cannot be determined with the available data (door widths are not provided)` | `4` | Tools do not expose opening `width_m`. |

---

## 4. `building_guide` Baseline on `data/dev` (`ia eval building_guide`)

Command run:
```bash
uv run ia eval building_guide --save-baseline
```
Saved in repo at: `evals/baselines/building_guide.json`

* **Model**: `anthropic:claude-opus-5-5` (`Thinking("medium")`, `Caching()`)
* **Average per case**: `18.7` requests, `221,529` input tokens, `7,171` output tokens, `46.4s` duration, **\$0.187** cost (**\$1.87** total for 10 cases).

| Case (`evals.yaml`) | Kind | Result | Agent `Answer:` vs. Expected | Reqs / Time / Cost | Failure / Brute-Force Analysis |
|---|---|---|---|---|---|
| `errand-proc-factory-dev-007-H04` | `errand` | **FAIL** | Got `46.62`, expected `37.91` | 14 reqs / 36s / \$0.14 | Found nearest filing cabinet (`0.24`), but guessed largest server room was `0.27` instead of `0.15`. |
| `floor_area_total-proc-campus-dev-002-H01` | `floor_area_total` | **FAIL** | Got `"Not available..."`, expected `174.3` | 10 reqs / 12s / \$0.04 | Missing room area tool. |
| `largest_room-proc-factory-dev-008-H01` | `largest_room` | **FAIL** | Got `"Unknown..."`, expected `1.10, 22.4` | 12 reqs / 12s / \$0.05 | Missing room area tool. |
| `straight_line_3d-proc-campus-dev-013-H03` | `straight_line_3d` | **FAIL** | Got `"not available..."`, expected `20.34` | 10 reqs / 30s / \$0.06 | Missing room centroid & floor elevation tool. |
| `wide_delivery_count-proc-factory-dev-005-H03` | `wide_delivery_count` | **FAIL** | Got `55`, expected `12` | 16 reqs / 38s / \$0.15 | Missing door `width_m` filter and counted non-room spaces. |
| `step_free_reachable_count-osm-way22744230-H03` | `step_free_reachable_count` | **FAIL** | Got `38`, expected `30` | 23 reqs / 74s / \$0.32 | Counted corridors, stairwells, and lift landings alongside rooms. |
| `room_stacked-osm-way22744230-H05` | `room_stacked` | **PASS** *(heuristic)* | Got `Generic room N1318` | 28 reqs / 70s / \$0.22 | Made 28 tool calls comparing distances to stairs/lifts on Floor 2 vs. Floor 1 and matching room numbers (`N2324` → `N1318`). |
| `shared_walls_count-proc-factory-dev-004-H03` | `shared_walls_count` | **PASS** *(hack)* | Got `4` | 23 reqs / 69s / \$0.27 | Called `add_door` in a loop against every room on the floor to see which threw `MapError("do not share a wall")`, mutating `building_edited.json`. |
| `what_if_door-osm-way27917482-H08` | `what_if_door` | **PASS** *(brute force)* | Got `6` | 47 reqs / 118s / \$0.61 | Made 47 LLM turns (1.05M input tokens) manually testing routes around Lift 1 before and after adding the door. |
| `interaction-vague-first-day` | *Interaction* | **PASS** | Listed kitchens + entrances | 4 reqs / 5s / \$0.01 | Passed `LLMJudge` rubric. |

---

## 5. Key Technical Findings from the Baseline Run

1. **Dynamic System Instructions Invalidate Anthropic Prompt Cache**:
   In `src/app/agents/building_guide/agent.py`, `@agent.instructions` calls `ctx.deps.map.overview()` on every model request. Whenever the agent calls `set_closed`, `overview()["closed"]` changes mid-run, mutating the system prompt prefix between turns. This triggers `AnthropicStaleThinkingBlockWarning` (dropping thinking blocks and retrying) and invalidates the prompt cache.
   * **Fix**: Keep the system prompt byte-stable across turns within a run (or only render the initial static overview in `@agent.instructions`, since `set_closed` already returns `all_closed` in its tool response).
2. **Deterministic Geometry & Graph Tools Will Cut Cost ~10× and Reach ~100% Accuracy**:
   Adding deterministic tools for polygon areas, shared walls (`≥ 1 m` within `0.35 m`), 3D centroids, vertical polygon intersection, door-width/step-free reachability (filtering `type == "room"`), nearest-item errands, and non-mutating what-if door simulations will reduce tool calls from `19–47` per question down to `1–2`.
