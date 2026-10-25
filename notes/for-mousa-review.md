*Heads-up Mousa: I changed your building_guide files* (tools.py, prompt.md, agent.py). Not committed yet. Please look before you build on top.

*Why:* I made 100 extra-hard test cases (`data/extra_tricky/`). Haiku went from 73 to 98/100. Scored dev stays 31/31, warmup 60/60, pytest green (545).

*Things you might worry about*
1. *Noisy diff:* ruff reformatted all of tools.py, so the diff looks bigger than it is.
2. *`never_crash` wraps every tool:* an exception or last-retry ModelRetry now comes back as a JSON error instead of killing the run. Good for the demo, but it can hide bugs.
3. *Stricter Answer-line validator (agent.py):*
   - one number only, and exactly `Answer: no route` when there is none
   - "A or B" allowed only after a tool flagged "Ambiguous:"
   - removes an Answer line the question didn't ask for
   - derived numbers must come from `calculate` (the retry now says so)
   - Known gap: `_ambiguity_flagged` only looks at RetryPromptPart, but in pydantic-ai 2.60 validator retries arrive as UserPromptPart.
4. *Ambiguity flags:* find says "Ambiguous" on shared names or numbers; list_rooms has `largest_is_a_tie` (within 1%); room_details has `directly_above/below_ambiguous` (runner-up ≥90% overlap). None of the kit questions trigger these, but watch for hedging on unseen buildings.
5. *Name matching:* "room 1.3" now matches 1.03 and 1.30. "the street" / "any entrance" mean `outside`.
6. *New tools:* `plan_visits` (best visiting order, exact up to 12 stops, under 1 s) and `walk_distances` (every room's walk from the entrances, longest first). The prompt sends free-order trips to plan_visits; fixed order stays `route` with via.
7. *New params / fields:*
   - route: returns `passes` (door counts) and `blocked_by` (why there's no route). blocked_by uses the private `RouteGraph._dijkstra(bays_are_ends=...)`.
   - reachable_rooms: `through_production_bays` (default True, so the delivery counts don't change) and `room_type`
   - nearest: `min_width_m`, `rooms_in_all`
   - Forklift docstrings say min_width_m=3 plus step_free.
8. *what_if:* a room that goes from unreachable to reachable now counts as "shorter".
9. *From my side (routing):* routes never pass through production bays any more, so some factory route lengths changed. The tools' global lock is gone; a lock in directions.py fixed the GEOS segfault on parallel tool calls.
10. *Stale in evals.yaml:* errand-proc-factory-dev-007-H04 should be 34.07, what_if_door-osm-way27917482-H08 should be 0.

Ask me anything.
