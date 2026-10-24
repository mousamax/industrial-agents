# Industrial Agents: guide for coding agents (Claude Code, Cursor, Codex)

## North star
We are building for a 3-minute live pitch. What wins: an idea that is valuable, solves a real problem for
the partner's users, and is presentable on stage. Code is only the tool for iterating on that idea fast.
Prefer the quickest change that makes the live demo
convincing on the partner's real data and error free.

Hackathon repo. Python 3.12, uv, Pydantic AI 2.60 on Claude (Anthropic API or OpenRouter), Langfuse
tracing, pydantic-evals. Built for fast iteration: no Docker, no Makefile, no CI. Every agent change is
checked with its evals, not by eyeballing one output.

## Track: Synera "Talk to Your Building"
We chose Synera's track. Brief, scoring and open questions: `docs/synera/README.md`. Finalists are picked
from the online submission, where 55 of 100 points are an automatic benchmark on unseen buildings: never
hard-code anything to a building or question.
- **Read `docs/synera/ROUTE_SCORING.md` before touching routing, queries, state or any route tool or
  answer.** It is Synera's announced rule for scoring routes (distance model, forklift/step-free/closure/
  production-bay rules, score = best length ÷ ours, invalid route = 0) and overrides the kit where they differ.
- `synera-kit/`: Synera's kit as a git submodule (data, `starter_agent/building_map.py` with the route
  model behind the golden answers, `benchmark/score.py` the judges' scorer). Don't edit it; import from it.
- `src/app/agents/building_guide/`: baseline agent on that kit. `BENCHMARK.md`: the official run command.
- `src/app/building/`: the domain core (model, geometry, routing, state, queries, build, view) as typed
  interfaces. Four people build behind them in parallel: **roles, file ownership and rules are in
  `docs/synera/TEAM_PLAN.md`.** Owners: A graph & state = Magnus, B geometry & build = Vadim, C agent
  harness = Mousa, D product & story = Maximilian. Ask which role the user has; only edit files that role
  owns; optimize for that role's metric in TEAM_PLAN; never change a signature in `src/app/building/`
  without the user agreeing to announce it to the team.
- Golden scoreboard: `uv run pytest tests/building -q -rs` checks each interface against Synera's answers
  (unimplemented stubs are skipped). Make the relevant tests pass; never weaken them.

## Map
- `src/app/agents/<name>/`: one folder per agent: `agent.py` (Agent + tools + `card`), `prompt.md`,
  `evals.yaml`, optional `schemas.py`, `tools.py`. Copy via `uv run ia new <name>`, never by hand.
- `src/app/flows/<name>/`: multi-agent orchestration in plain async Python, exposes a `FlowCard`.
- `src/app/core.py`: `AgentCard` / `FlowCard`, the contract the CLI, API and evals use.
- `src/app/registry.py`: discovers cards by folder. Don't add a central list.
- `src/app/models.py`: model aliases. Use `model("smart"|"balanced"|"fast")`, never a raw model id.
- `src/app/tools/`: tools shared by several agents. Agent-specific tools stay in the agent folder.
- `src/app/evals/`: runner, custom evaluators (`FieldsMatch`), `coding.py` (open/closed coding with LLM
  judges). `evals/baselines/` and each agent's `failure_modes.yaml` are committed.

## Commands
- `uv run ia list | show <name> | run <name> '<json|@file|text>' | chat <name> | ui <name> | eval <name> | analyze <name>`
- Before committing: `uv run ruff check --fix src tests && uv run ruff format src tests && uv run pytest -q`.
- `ia eval` / `ia analyze` need `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY` in `.env`.

## Rules
- Tests never call real models (`ALLOW_MODEL_REQUESTS=False`). Use `TestModel` or `FunctionModel`;
  see `tests/test_supplier_quote.py`. New agents are covered by `tests/test_registry.py` automatically.
- Prompts live in `prompt.md`, not in Python strings.
- Structured output (`output_type=SomeModel`) for anything a program or UI consumes.
- Facts come from tools, not from the model's memory. Tool docstrings are the tool descriptions: write them
  for the model.
- Validate outputs in code (`@agent.output_validator` + `ModelRetry`) when a rule can be checked.
- Control flow in Python, judgment in agents. Reach for an agent-calls-agent tool only for open-ended
  delegation.
- Claude Opus 5.5 / Sonnet 5.5: don't set `temperature`/`top_p`; thinking can't be turned off, set its
  depth with `Thinking("low"|"medium"|"high")`.
- Evals: code checks for anything with a right answer; LLM judges for qualitative analysis: `LLMJudge`
  rubrics, open coding (`ia analyze`) and closed coding against `failure_modes.yaml`.
- When you change an agent's behavior: run its evals, and if better, `--save-baseline` in the same commit.
- Partner data goes in `data/private/` (gitignored). Never commit secrets or partner data.
