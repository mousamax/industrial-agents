# data/extra_tricky/: hard test cases beyond Synera's kit

100 hand-designed, deliberately hard questions on Synera kit buildings (dev/train only), some on altered
copies of those buildings. They probe what the kit's question kinds and our `building_guide` tools
(`notes/test-cases-and-tools.md`) don't cover well: rule-heavy routing, geometry traps, unsolvable or
ambiguous questions, multi-step chains outside one tool, and adversarial wording.

Every case carries the **reasoning** for its expected answer and how that answer was **verified**. If a model
fails a case, you can check whether the model or the test is wrong.

Not part of any score. Never tune on `evals/datasets/unseen_holdout.jsonl`. These cases are a separate dev set.

## Layout (mirrors `synera-kit/data/<split>/<building_id>/`)

```
data/extra_tricky/
  README.md
  <building_id>/questions.jsonl        # cases on an unaltered kit building (building_path points into synera-kit)
  <altered_id>/building.json           # altered copy of a kit building, same schema as the kit
  <altered_id>/equipment.csv           # copied (and altered if needed) from the source building
  <altered_id>/questions.jsonl         # cases on that altered building
  _verify/<direction>.py               # script that recomputes every answer of one direction from the data
```

Altered building ids: `<source_id>-xt-<short-tag>` (e.g. `proc-factory-dev-003-xt-nolift`). Every id inside an
altered building.json (spaces, floors, openings…) keeps the source prefix. Only `building_id`/`id` at the top is
renamed, and `extra_tricky_modification` (top-level string) says what changed.

## One line per case (kit fields first, then `extra`)

```json
{
  "id": "xt-<direction>-NN",
  "building_id": "proc-factory-dev-003",
  "building_path": "synera-kit/data/dev/proc-factory-dev-003",
  "kind": "<kit kind if it fits, else a new snake_case kind>",
  "skill": "short skill label",
  "question": "User-facing question, ending with \"Finish with a line 'Answer: <...>'\" where a single answer exists",
  "inputs": {"...": "resolved ids, as in the kit"},
  "answer": { "...": "typed answer, see below" },
  "extra": {
    "direction": "routing_rules | geometry_traps | altered_plans | beyond_tools | adversarial_language",
    "difficulty": "hard | very_hard",
    "solvable": "yes | partially | no",
    "expected_behavior": "answer | say_no_route | ask_clarification | flag_false_premise | answer_with_caveat | refuse_out_of_scope",
    "tool_coverage": "covered | partial | not_covered",
    "tools_needed": ["route", "list_rooms", "..."],
    "based_on": "kit question id that inspired it, or null",
    "modification": "what was altered in the plan, or null",
    "trap": "one sentence: what makes it tricky / what a naive agent gets wrong",
    "reasoning": "numbered steps from the data to the answer, with the intermediate values",
    "verification": "how the answer was computed and checked (script in _verify/, functions used, values)"
  }
}
```

### Answer types
- Kit types as in `synera-kit/data/*/*/questions.jsonl`: `{"type":"number","value":..,"unit":..,"relative_tolerance":..,"absolute_tolerance":..}`,
  `{"type":"space","space_id":..,"accept":[..],"reject":[..]}`, plus kit-style extra keys (area, via, target).
- `{"type":"route","from_space_id":..,"to_space_id":..,"spaces":[ordered space ids],"length_m":..,"constraints":{...}}`:
  the best route under `docs/synera/ROUTE_SCORING.md`; any route within 1 % of `length_m` that passes
  `validate_route` is also correct.
- `{"type":"no_route","reason":".."}`: the right answer is to say no route exists under the rules.
- `{"type":"clarification","options":[space ids or values],"reason":".."}`: the agent must ask, or list the
  options, instead of guessing.
- `{"type":"false_premise","correction":".."}`: the question assumes something untrue. The agent must say so.
- `{"type":"set","values":[..]}`: an unordered set of ids or names.
- `{"type":"text","must_mention":[..],"must_not_claim":[..]}`: for out-of-scope / caveat cases.
