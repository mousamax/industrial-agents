# BENCHMARK.md

How to run our agent (Track 01, "Talk to Your Building") on new buildings and their questions. One command,
no manual steps between input and output. `SUBMISSION/answers.json` next to this file is its output on the
kit's test set (`synera-kit/data/test`, 145 questions).

## Setup (once)

Needs [uv](https://docs.astral.sh/uv/) and an Anthropic API key. Python 3.12 is installed by uv.

```bash
git clone --recurse-submodules <this repo> industrial-agents && cd industrial-agents
uv sync
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env     # the only variable needed; or export it in the shell
```

Everything else in `.env.example` (Langfuse tracing, OpenRouter) is optional and can stay unset.

## The benchmark command

Run from the repo root. The data folder can be anywhere on disk.

```bash
uv run run_benchmark <data_dir> <answers.json>
# the secret building:  uv run run_benchmark ~/secret SUBMISSION/answers.json
# how answers.json here was made:  uv run run_benchmark synera-kit/data/test SUBMISSION/answers.json
```

`<data_dir>` is a folder in the kit's format, like `synera-kit/data/test`: one folder per building
(`building.json`, `equipment.csv`, floor plans) and the questions, either in a top-level `questions.csv` /
`questions.jsonl` (with `building_id`) or in each building's `questions.jsonl`. A single building folder
holding its `questions.jsonl` works too. A separate questions file can be given in the middle:
`uv run run_benchmark <building_dir> <questions.json|.jsonl|.csv> <answers.json>`.

`run_benchmark` is an alias of `uv run python -m app.agents.building_guide.sunday` (same arguments).
Options:

- `--check`: afterwards checks every answer the way routes are scored (a crash, no answer, a route that
  skips a real door/stair/lift or breaks a step-free or closure constraint) and exits 1 if any has a
  problem. It doesn't change answers.json.
- `--concurrency 8` (default): questions answered in parallel. A failed API request is retried twice.
- `--csv <path>`: also writes the kit's CSV format (question_id, full reply).
- `--details <path>`: also writes the structured records behind the answers (see below).

Re-running on the same input rewrites answers.json. Routes, lengths, ids and counts are computed by code;
the model only picks the tool and its arguments, so they come out the same on a re-run unless the model
reads a question differently.

## Output: answers.json

What Synera asked for: a JSON list of `{question_id, answer}`, one per question, in input order. `answer`
is the agent's full reply; its last line starting with `Answer:` is the answer the kit's scorer reads.

```json
[{"question_id": "osm-way363805324-H01", "answer": "… Generic room 1.02 is the largest …\nAnswer: Generic room 1.02, 162.33 m²"}]
```

A question whose answering crashed still gets its entry, with an empty answer, so one failure never loses
the file.

## Structured records (--details)

Behind each answer, for checking and for the app: one record per question.

```json
{"id": "Q1", "reply": "<full reply>", "answer": "<text after the last 'Answer:'>",
 "item_id": "<asset id>|null", "space_id": "<space id>|null", "count": 3,
 "reachable": true, "route": ["<space id>", "..."], "elements": ["<door/stair/lift id>", "..."],
 "length_m": 42.17, "stops": ["<space id>", "..."]}
```

| Field | Meaning |
|---|---|
| `item_id`, `space_id` | The located item (asset id) and the room holding it, or the room found |
| `route` | The route as an ordered list of node ids: the spaces passed, first to last (a tour's legs joined) |
| `elements` | The doors, openings, stairs and lifts used, in order |
| `length_m` | Route length in metres (door to door, stairs 2 m per metre climbed, lift 15 m + 1 m per metre) |
| `reachable` | `false` when no route exists under the constraints; `route` and `length_m` are then null |
| `stops` | A tour's stops in visiting order (start, each stop, end); null otherwise |
| `count` | The number, for "how many" questions |
| `error` | Only present if answering that question crashed; every other field is then null |

Fields that don't apply to a question are null. The structured fields are copied from the agent's tool
results, not parsed from its prose. The schema lives in `empty_record`/`to_record`
in `src/app/agents/building_guide/sunday.py`.

## Constraints in a questions.json

`{"id", "text", "constraints"?}` entries may carry `"step_free": true`; a closed lift, stair, door or room by
id or name under `"closed"` (also `closures`, `closed_elements`, `closed_element_id(s)`); `"start"`: where
the person is. Other constraint keys are passed to the agent as text.

## Test set (145 questions, CSV)

The kit's test submission, written with our parallel runner (same agent, same scorer, one failed request
doesn't stop the run):

```bash
uv run python -m app.agents.building_guide.bench test --submission evals/test_answers.csv
uv run python -m app.agents.building_guide.bench dev        # self-check on dev, score per kind
```

The kit's own sequential evaluator gives the same answers:

```bash
cd synera-kit && uv run --project .. python -m benchmark.evaluate data/test \
  --agent app.agents.building_guide.benchmark:answer --submission-csv ../evals/test_answers.csv
```

## Rehearsal and plumbing test

```bash
uv run python scripts/synera/sunday_rehearsal.py --limit 3   # the command above on a kit building with known answers
uv run pytest tests/building/test_sunday.py -q                # no model: the runner, schema and --check
```

## Several buildings (Level 3)

Put a `site.json` next to them and give the site folder as the building folder: every route then works across
buildings, joined only by the walkways listed (there is no free way round the outside).

```json
{
  "site": {"id": "sunday-site", "name": "…"},
  "buildings": ["<building folder or kit id>", "…"],
  "walkways": [{"id": "W1", "name": "Covered walkway", "from": "<entrance opening id>", "to": "<entrance opening id>",
                "length_m": 120.0, "step_free": true, "width_m": 2.5}]
}
```

Without `length_m` a walkway is the straight line between its two entrance doors, which is only right when
the buildings share one coordinate frame. Example: `evals/datasets/sites/demo-campus`.
