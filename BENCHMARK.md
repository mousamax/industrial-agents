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

```bash
git submodule update --remote synera-kit    # pulls Synera's release
# then run the command they specify on the new building directory (TBD)
```
