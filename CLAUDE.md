# Industrial Agents: guide for coding agents (Claude Code, Cursor, Codex)

Hackathon repo. Python 3.12, uv, Pydantic AI 2.60 on Claude (Anthropic API or OpenRouter), Logfire/Langfuse
tracing, pydantic-evals. Built for fast iteration: no Docker, no Makefile, no CI. Every agent change is
checked with its evals, not by eyeballing one output.

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
- `uv run ia list | run <name> '<json|@file|text>' | chat <name> | ui <name> | eval <name> | analyze <name>`
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
