# BENCHMARK.md

How Synera runs our agent on unseen buildings and questions (Track 01). One command, no manual steps.

> **Status:** wired to the current baseline agent (`building_guide`). The Sunday format is still to
> confirm with Synera: the brief mentions `answers.json` (item id, route as node ids, length in metres),
> the kit README a CSV of replies. Update this file once they publish the schema.

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
