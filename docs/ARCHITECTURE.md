# Architecture and design rules

## The one abstraction: a card

```
            ┌──────── CLI (ia run / chat / ui / eval)
AgentCard ──┼──────── API (/run, /chat, /ui)
FlowCard  ──┼──────── eval runner (evals.yaml next to the card)
            └──────── tests (registry contract tests)
```

A card is `name + description + input_type + async run(inputs, model=None)`. `AgentCard` wraps one
pydantic-ai `Agent`; `FlowCard` wraps any async function. The registry imports every
`agents/<name>/` and `flows/<name>/` package and reads its `card`. Adding a unit of work never touches
a shared file, so four people can work in parallel without merge conflicts.

## Choosing the shape of a solution

Start at the top, go down only when you have to (Anthropic's "start simple" ladder):

1. **One structured call.** An agent with an `output_type` and no tools. Extraction, classification,
   checking a document against rules that fit in the prompt.
2. **Agent + tools.** The model needs facts it doesn't have (catalog, regulation text, CAD metadata,
   partner API). Tools return typed data; docstrings tell the model when to call them.
3. **Flow.** Several steps with a known order: fan out over documents, decide in code, fan in.
   Python owns control flow; agents own judgment. Cheap to test, easy to explain in a pitch.
4. **Agent delegation.** An agent calls another agent from a tool, only when the order of steps
   genuinely depends on what the model finds.

## Patterns already wired in

| Need | Use | Example |
|---|---|---|
| Typed, reliable output | `output_type=PydanticModel` | `supplier_quote/schemas.py` |
| Rules the model must respect | `@agent.output_validator` + `ModelRetry` | `totals_add_up` |
| Facts | `@agent.tool` with a typed return | `lookup_part` |
| PDFs, drawings, photos | `tools.documents.as_user_content(path)`: Claude reads them natively | |
| Browse a data drop | `toolsets=[tools.documents.data_files]` | |
| Partner MCP server | `capabilities=[MCP(url=..., authorization_token=...)]` | |
| Web lookup | `capabilities=[WebSearch(), WebFetch()]` (Anthropic server-side tools) | |
| Thinking depth | `capabilities=[Thinking("low"/"medium"/"high")]` | |
| Prompt caching | `capabilities=[Caching()]` (long prompts and documents get cheap and fast) | |
| Parallel steps | `asyncio.gather` in a flow | `quote_comparison` |
| Human approval of risky tool calls | `requires_approval=True` on a tool + deferred tools | pydantic-ai docs: "deferred tools" |

## Models

`models.py` maps aliases to model ids on the Anthropic API, or on OpenRouter when there is no
`ANTHROPIC_API_KEY` (once that key is set, the aliases never use OpenRouter). `.env` can override each alias: `smart` Opus 5.5 (default for agents),
`balanced` Sonnet 5.5 (writers, cheaper steps), `fast` Haiku 5.5 (classification, routing), `judge` Sonnet 5.5.
Notes for the Claude 5.5 models: `temperature`/`top_p` are rejected, thinking is always on (tune
depth instead), forced tool choice is rejected; pydantic-ai already handles that for structured output.

## Evals

`evals.yaml` = cases (inputs, partial expected output) + evaluators. Order of preference:
1. code checks: `FieldsMatch` (partial structural match), `IsInstance`, `MaxDuration`;
2. trajectory checks: `ToolCorrectness`, `TrajectoryMatch`, `MaxToolCalls` (read from the trace spans);
3. LLM judges for qualitative analysis:
   - `LLMJudge` with a binary, specific rubric for criteria you know up front;
   - open coding (`ia analyze`): a judge writes free-form notes on every output of a run, then the notes
     are grouped into failure modes (`failure_modes.yaml`, edited by a human);
   - closed coding: the `ClosedCoding` evaluator (auto-added when `failure_modes.yaml` exists) labels
     every output against that codebook, one `no_<mode>` assertion per failure mode.

Judges run on the `judge` alias (Sonnet 5.5), the axial grouping on `smart`.

Each run prints a table against `evals/baselines/<name>.json` and saves to `evals/reports/`. With
Langfuse keys set, every case's agent run is a trace tagged `eval` and `label:<label>`.

## Observability

`observability.setup()` turns on Pydantic AI's OpenTelemetry instrumentation (`Agent.instrument_all()`)
and, if `LANGFUSE_*` keys are set, the Langfuse client, which exports those spans. Entry points (CLI,
API, eval cases) set the trace name, tags and user. Flows add one `chain` node. Agents need no tracing
code. Without keys, spans stay in memory, which is how the trajectory evaluators see tool calls.
