---
name: new-agent
description: Create a new agent in this repo from a plain-language description of the job. Use when the user wants a new agent, a new step in a flow, or "an agent that does X".
---

1. Pick a snake_case name. Run `uv run ia new <name>`.
2. Ask (or infer from the conversation) for: the input, the output a program or person needs, the facts
   the agent must look up, and what "correct" means. Don't invent partner APIs; leave a typed stub tool.
3. Edit `src/app/agents/<name>/`:
   - `agent.py`: `Input`/`Output` models (strict, with `Field(description=...)`), tools with docstrings
     written for the model, `@agent.output_validator` for any rule code can check.
   - `prompt.md`: role, steps, decision rules, output style. Short and concrete.
   - `evals.yaml`: at least 3 cases (happy path, edge case, should-refuse/should-flag case) with partial
     `expected_output` checked by `FieldsMatch`, plus `LLMJudge` rubrics for the qualitative criteria.
4. `uv run ruff check --fix src tests && uv run pytest -q` must pass (the registry contract test covers the
   new agent).
5. If `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY` is in `.env`: `uv run ia eval <name>`; fix the agent until cases pass, then
   `--save-baseline`.
