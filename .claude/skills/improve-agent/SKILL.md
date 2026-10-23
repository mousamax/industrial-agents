---
name: improve-agent
description: Iterate on an existing agent's prompt, tools or model against its evals (hill-climb). Use when the user says an agent is wrong, flaky, slow or expensive, or asks to tune a prompt.
---

Loop, one change per iteration:

1. Baseline: `uv run ia eval <name> --repeat 3 --label before -v`. Read the failing assertions and
   reasons. If the failure isn't covered by a case yet, add the case to `evals.yaml` first.
2. Qualitative analysis: `uv run ia analyze <name>` (open coding of the run, grouped into
   `failure_modes.yaml`). Review and tighten the codebook with the user; it is committed and every later
   `ia eval` labels outputs against it (closed coding). Pick the most frequent failure mode, then read its
   traces (Logfire, or `-v`): wrong tool use? missing fact? ambiguous instruction? loose schema? weak model?
3. Make the smallest change that addresses it, preferring, in order: a tool or data fix, a schema or
   validator fix, a prompt fix, a model/effort change (`--model balanced|smart`, `Thinking(...)`).
4. Re-run with a new `--label`. Keep the change only if the targeted `no_<mode>` assertion improves without
   new failures (LLMs are noisy: compare with `--repeat 3`).
5. Done: `--save-baseline`, commit agent + evals.yaml + baseline together, and say what changed and
   the before/after pass rate.

Never edit an eval case just to make it pass unless the expected output was actually wrong.
