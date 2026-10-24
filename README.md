# Industrial Agents

Our team's stack for the [Industrial Agents Hackathon](https://www.industrialagents.dev/) (MM x UVC x Anthropic,
Munich, 24–25 Oct 2026). The plumbing (models, tracing, evals, API, chat UI, deploy) is done, so we spend the
weekend on **agent design, prompts and flows**.

| Concern | Choice | Why |
|---|---|---|
| Agents | [Pydantic AI](https://ai.pydantic.dev) 2.60 | Typed outputs, tools, capabilities, test models |
| Models | Claude via aliases (`smart` = Opus 5.5, `balanced` = Sonnet 5.5, `fast` = Haiku 5.5), Anthropic API or OpenRouter | One env var swaps models for everyone |
| Tracing | Pydantic AI's built-in OpenTelemetry → [Langfuse](https://langfuse.com) | See every agent, prompt, model call and tool call |
| Evals | [pydantic-evals](https://ai.pydantic.dev/evals/) + `evals.yaml` next to each agent | Fast iteration, committed baselines |
| Error analysis | LLM judges for open coding → failure-mode codebook → closed coding | Know *why* outputs fail, not just *that* |
| Serving | FastAPI: `/run`, `/chat` (Vercel AI protocol), `/ui` (built-in chat UI) | A demo in zero lines of frontend |
| Tooling | uv, ruff, pytest. No Docker, no Makefile, no CI: just Python | |

**Track: Synera, "Talk to Your Building".** Start at [docs/synera/README.md](docs/synera/README.md)
(brief, scoring, questions for Synera) and [BENCHMARK.md](BENCHMARK.md). Clone with
`git clone --recurse-submodules`, or run `git submodule update --init` after cloning: Synera's kit and data
are the `synera-kit/` submodule.

## Start in 3 minutes

```bash
uv sync && cp .env.example .env
# put ANTHROPIC_API_KEY or OPENROUTER_API_KEY (and the team's LANGFUSE_* keys) in .env
uv run ia list             # every agent/flow: model, tools, eval cases
uv run ia show supplier_quote        # its job, input/output, model, tools, full prompt
uv run ia run supplier_quote @data/sample/quotes/automa.txt
uv run ia ui supplier_quote          # chat UI on http://localhost:8001
uv run ia eval supplier_quote        # evals vs baseline
uv run ia analyze supplier_quote     # why did cases fail? (open coding -> failure modes)
```

## Build an agent

```bash
uv run ia new spec_checker      # copies src/app/agents/_template
```
Then edit three files in `src/app/agents/spec_checker/`:
- `prompt.md`: the job,
- `agent.py`: input/output models, tools, validators,
- `evals.yaml`: cases and what "good" means.

It appears in the CLI, the API, the chat UI and the eval runner automatically. Iterate:

```bash
uv run ia eval spec_checker -v                       # see outputs and judge reasons
uv run ia eval spec_checker --model balanced --label sonnet --repeat 3
uv run ia eval spec_checker --save-baseline          # commit evals/baselines/spec_checker.json
uv run ia eval-gen spec_checker --n 8 --hint "edge cases with missing data"   # draft more cases
```

Before committing, run `uv run ruff check --fix src tests && uv run pytest -q` (about 2 s, no API calls).

## Evals: three layers

1. **Code checks** for anything with a right answer: `FieldsMatch` (partial expected output),
   `IsInstance`, `MaxDuration`, and the trajectory checks `ToolCorrectness` / `MaxToolCalls`.
2. **`LLMJudge` rubrics** in `evals.yaml` for qualitative criteria you can name up front
   ("flags the prepayment as a blocker", "email asks for a concrete next step").
3. **Qualitative error analysis with LLM judges**, for the failures you can't name yet:
   - *open coding*: `ia analyze <name>` has a judge read every case of the last run and write specific
     notes on what went wrong, then groups the notes into a codebook of failure modes in
     `failure_modes.yaml` next to the agent. Edit it like any other file.
   - *closed coding*: once that file exists, every `ia eval` labels each output against the codebook
     and reports one `no_<failure_mode>` assertion per mode. You can see which modes a prompt change fixed
     and which it introduced.

Loop: `ia eval` → `ia analyze` → fix the top failure mode → `ia eval` → `--save-baseline`.

Two worked examples to copy from:
- `agents/supplier_quote`: extraction + catalog tool + self-correcting output validator + evals using
  `FieldsMatch`, `ToolCorrectness` and `LLMJudge`.
- `flows/quote_comparison`: fan out (parallel agents), decide in code, fan in (writer agent).

## Layout

```
src/app/
  agents/<name>/     agent.py · prompt.md · evals.yaml   (one folder per agent, owned by one person)
  flows/<name>/      flow.py  · evals.yaml               (multi-agent orchestration)
  tools/             shared tools (documents: PDFs/images straight to Claude, data/ browsing)
  core.py            AgentCard / FlowCard: the contract everything else uses
  registry.py        finds agents/flows by folder (no central list, no merge conflicts)
  models.py          model aliases
  observability.py   Langfuse tracing
  evals/             eval runner + custom evaluators
  api/main.py        FastAPI
  cli.py             `ia` command
tests/               offline only (TestModel / FunctionModel); new agents are covered automatically
evals/baselines/     committed reference eval runs
data/sample/         fixtures · data/private/ partner data (gitignored)
docs/                ARCHITECTURE.md · PLAYBOOK.md (how we work this weekend)
```

## Models

Put either key in `.env`. With only `OPENROUTER_API_KEY`, the aliases resolve to the same Claude models on
OpenRouter (`openrouter:anthropic/claude-opus-5.5`, ...). Any full id works for comparisons:
`uv run ia eval supplier_quote --model openrouter:openai/gpt-6.1-sol --label gpt`.

## Tracing

Two ways to see what's going on:
- **Without leaving the terminal:** `ia list` (all agents and flows, their models, tools, eval cases) and
  `ia show <name>` (job, input/output, model, tools, full prompt).
- **Langfuse** (one shared project; `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` in `.env`):
  one trace per run, named after the agent or flow and tagged `cli`/`api`/`eval`, with `DEV_NAME` as the user.
  A flow is a `chain` node, with each agent run nested under it, and inside each agent run the model calls
  (prompt, thinking, tokens, cost) and tool calls (arguments, result). Leave the keys empty to trace nothing.
  Cost shows for `claude-*` models on the Anthropic API. OpenRouter model names aren't in Langfuse's price
  table, so OpenRouter runs show tokens only.

The Langfuse agent skill is in `.claude/skills/langfuse` (query traces, prompts, datasets via `langfuse-cli`).

## API

`uv run uvicorn app.api.main:app --reload` → http://localhost:8000/docs

- `GET /agents`: names, descriptions, input JSON schemas
- `POST /run/{name}`: JSON in, JSON out (agents and flows)
- `POST /chat/{name}`: streaming, [Vercel AI SDK](https://ai-sdk.dev) `useChat` compatible, for a custom frontend
- `GET /ui/{name}/`: built-in chat UI per agent

## For coding agents

`CLAUDE.md` (= `AGENTS.md`) holds the conventions. Claude Code skills in `.claude/skills/`: `new-agent`,
`improve-agent` (eval + error-analysis loop).
