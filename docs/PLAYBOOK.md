# Weekend playbook

## Before kickoff (Sat 09:30)
- [ ] Everyone: clone, `uv sync`, `cp .env.example .env`, `uv run pytest -q` green. Claude Code / Cursor
  pointed at the repo.
- [ ] One Langfuse project; everyone uses the same `LANGFUSE_*` keys and their own `DEV_NAME`.
- [ ] The Anthropic key from the organisers goes in each `.env` (never committed). Until then:
  `OPENROUTER_API_KEY`.
- [ ] Repo on GitHub, everyone has push rights, branch protection off (speed).

## 10:00–11:30 track briefings → choose
Read each brief for: who the user is, what data we get, what a "wow" demo looks like in 3 minutes.
The three partners all work on documents in industrial workflows (Tacto: procurement and suppliers,
Synera: engineering workflow automation, Certivity: regulatory and requirements compliance), so the
`supplier_quote` pattern (document → structured output → checks with tools → decision) transfers.
Write the one-sentence problem statement and the demo script **before** coding.

## 12:00 first two hours
1. Design on paper (15 min): the flow as boxes. For each box: card name, input model, output model,
   tools, owner. Shapes from `ARCHITECTURE.md` ("Choosing the shape of a solution").
2. Each owner: `uv run ia new <name>`, writes `Input`/`Output` first and pushes them, so the others
   can build against the contract while the prompt is still rough.
3. Write 3–5 eval cases per agent from the partner's real data before tuning prompts.
4. One person owns the flow and the demo surface (`/ui`, or a small frontend on `/chat` and `/run`).

## Roles (team of 4; merge roles if fewer)
| Role | Owns |
|---|---|
| Agent A / Agent B | one agent folder each: prompt, tools, evals, baseline |
| Flow + data | `flows/`, partner API/MCP clients in `tools/`, data loading |
| Demo + pitch | frontend or `/ui`, the 3-minute story, the eval numbers slide |

## Git rules (fast, low-conflict)
- Short branches, small PRs, merge to `main` many times a day. `uv run pytest -q` before pushing.
- You own your agent folder. Touching someone else's? Tell them.
- Behavior change ⇒ eval run in the PR description (before/after pass rate) + updated baseline.
- Partner data stays in `data/private/`.

## Iteration loop (most of the weekend)
`ia eval <name> -v` → `ia analyze <name>` (open coding → failure modes) → fix the most common failure
mode → re-run with `--label` (closed coding shows whether that mode went away) → keep it if the pass
rate goes up → `--save-baseline` → commit. The `improve-agent` skill makes
Claude Code run this loop for you.

## Sat evening checkpoint (~21:00)
End-to-end flow works on real partner data, even if rough. Cut scope, don't add it.

## Sunday
- 08:00–11:00: quality on the demo path only; evals on the cases the jury will see.
- 11:00: feature freeze. Rehearse the demo twice. Record a backup video of the working demo.
- 13:00: code freeze and submission.

## Pitch (3 min + Q&A)
1. The user and the painful job, in their words (30 s).
2. Live demo on the partner's real data (90 s).
3. Why it's trustworthy: eval pass rate, how the agent checks itself, traces (30 s).
4. What it's worth and what's next (30 s).
