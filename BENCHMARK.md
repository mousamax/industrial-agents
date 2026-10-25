# BENCHMARK.md

How Synera runs our agent on unseen buildings and questions (Track 01). One command, no manual steps.

> **Status:** wired to `building_guide` (one agent, typed tools on `src/app/building/`). The Sunday format
> is still to confirm with Synera. Routes are scored as the ordered list of spaces
> (`docs/synera/ROUTE_SCORING.md`); the route tool already returns `space_path`. Update this file once
> they publish the schema.

## Setup

```bash
git clone --recurse-submodules <this repo> && cd industrial-agents
uv sync
cp .env.example .env    # add ANTHROPIC_API_KEY
```

## Test-set submission (145 questions → CSV)

```bash
cd synera-kit
uv run --project .. python -m benchmark.evaluate data/test \
  --agent app.agents.building_guide.benchmark:answer --submission-csv ../submission/team_answers.csv
```

## Faster: parallel runner (same agent, same scorer)

```bash
uv run python -m app.agents.building_guide.bench test --submission submission/team_answers.csv
uv run python -m app.agents.building_guide.bench dev                       # score per kind, all 138
uv run python -m app.agents.building_guide.bench dev --per-kind 3 --model balanced --thinking medium
```

Each run saves replies and tool calls to `evals/reports/bench-<label>.jsonl`.

## Self-check on dev (same scorer the judges use)

```bash
cd synera-kit
uv run --project .. python -m benchmark.evaluate data/dev --per-kind 3 \
  --agent app.agents.building_guide.benchmark:answer
```

## Sunday building

A new building folder (kit format, anywhere on disk) plus `questions.json` in, `answers.json` out. One
command, no manual steps:

```bash
uv run python -m app.agents.building_guide.sunday <building_dir> <questions.json> <answers.json>
# e.g. uv run python -m app.agents.building_guide.sunday data/sunday/building data/sunday/questions.json answers.json
# --concurrency 8 (default): questions in flight
# --check: then check every answer the way Synera scores it (crash, no Answer line, invalid route); exit 1 on problems
# --csv answers.csv: also write the kit's CSV format (question_id, full reply)
```

On Sunday: run it with `--check --csv submission/answers.csv` and output `submission/answers.json`, fix or
re-run anything `--check` lists, commit both before code freeze.

**Rehearsal** (the same command on a kit building with known answers, scored by kind; uses the model):

```bash
uv run python scripts/synera/sunday_rehearsal.py --limit 3     # mock run: 3 questions spread over kinds
uv run python scripts/synera/sunday_rehearsal.py               # every question of the building
uv run python scripts/synera/sunday_rehearsal.py --building proc-factory-dev-003 --suite warmup
```

It writes `evals/reports/sunday-rehearsal/<building>/` (questions.json, answers.json/.csv, summary.json).

`questions.json` is a list (or `{"questions": [...]}`) of `{"id", "text", "constraints"?}`; constraints
such as `{"step_free": true}` are passed on to the agent. `answers.json` is a list with one record per
question id, in input order:

```json
{"id": "...", "reply": "<full reply>", "answer": "<text after the last 'Answer:'>",
 "item_id": "<asset id>|null", "space_id": "<space id>|null", "count": 3,
 "reachable": true, "route": ["<space id>", "..."], "elements": ["<door/stair/lift id>", "..."],
 "length_m": 42.17, "stops": ["<space id>", "..."]}
```

`route` is the ordered list of spaces (what `docs/synera/ROUTE_SCORING.md` scores; a tour's legs joined),
`elements` the doors, stairs and lifts used, `stops` a tour's stops (null otherwise); unreachable gives `reachable: false` and null route/length.
The fields come from the agent's own tool results, never from its prose. A question that crashes still
gets a record with `"error"`. **Provisional:** Synera hasn't published their schema yet; once they do,
change `empty_record`/`to_record` in `src/app/agents/building_guide/sunday.py` (the only place it lives).
Plumbing test (no model): `uv run pytest tests/building/test_sunday.py -q`.

## If the Sunday release has several buildings (Level 3)

Put a `site.json` next to them and point the runner at the site folder: every tool, route and answer then
works across buildings, joined only by the walkways you list (there is no free way round the outside).

```json
{
  "site": {"id": "sunday-site", "name": "…"},
  "buildings": ["<building folder or kit id>", "…"],
  "walkways": [{"id": "W1", "name": "Covered walkway", "from": "<entrance opening id>", "to": "<entrance opening id>",
                "length_m": 120.0, "step_free": true, "width_m": 2.5}]
}
```

Without `length_m` a walkway is the straight line between its two entrance doors, which is only right when
the buildings share one coordinate frame. Example and tests: `evals/datasets/sites/demo-campus`,
`tests/building/test_level3.py`.
