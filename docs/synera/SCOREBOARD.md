# Team scoreboard

Generated 2026-10-25T09:24:56+00:00 by Magnus Saurbier with `uv run ia scoreboard`. Don't edit by hand: change `scoreboard.yaml` (rows, stakes, manual status) or make a row's eval pass.

**Team: 88.4 / 100 estimated points.** Stakes are our guess at Synera's split.

| Person | Points |
|---|---|
| Mousa | 41.9 |
| Magnus | 21.5 |
| Vadim | 16.0 |
| Maximilian | 9.0 |

| Part | Row | Owners | Measured | Points | Failing / next |
|---|---|---|---|---|---|
| Does it work | **test-geometry**: Area, wall, stacking and 3D questions answered right end to end (dev, judges' scorer) | Vadim, Mousa | ██████████ 100% (97/97 questions) | 12.0 / 12 | – |
| Does it work | **test-graph**: Errand, delivery, reachability and what-if questions answered right end to end | Magnus, Mousa | ██████████ 100% (41/41 questions) | 6.0 / 6 | – |
| Does it work | **sunday-find-and-route**: Sunday-style find, count, route, step-free, closure and tour questions through the agent | Mousa, Magnus | ██████████ 100% (369/369 questions) | 6.0 / 6 | – |
| Does it work | **sunday-answers-json**: The Sunday run from the PDF: one command, any building folder + questions.json -> answers.json with item/room ids, valid best routes as node ids, lengths; documented in BENCHMARK.md (no model: deterministic answerer) | Mousa, Magnus | ██████████ 100% (21/21 tests) | 5.0 / 5 | – |
| Does it work | **synera-route-rule**: Routes are the best under Synera's announced rule (per-metre stairs/lifts, forklift, bays) | Magnus | ██████████ 100% (523/523 tests) | 8.0 / 8 | – |
| Does it work | **unseen-wording**: The scored kinds asked in other words (reworded dev questions) | Mousa | ██████████ 100% (45/45 questions) | 2.0 / 2 | – |
| Does it work | **unseen-kinds**: 12 question kinds Synera hasn't shown, on test buildings, answers computed by code (held out, score only) | Mousa | ██████████ 99% (95/96 questions) | 3.0 / 3 | exit_distance 7/8 |
| Does it work | **level4-from-svg**: Buildings drafted from SVG floor plans match the truth (Level 4) | Vadim | ██████████ 100% (8/8 tests) | 5.0 / 5 | – |
| Does it work | **level4-from-images**: Buildings drafted from plan images (PNG, e.g. floor_<level>_plain.png) match the truth (Level 4) | Vadim | ██████████ 100% (8/8 tests) | 5.0 / 5 | – |
| Does it work | **level4-conversation**: Level 4 by conversation: upload floor plans, get a building in seconds, and settle what the plans don't say by talking (grouped questions, fixes, forklift doors, storey heights) | Mousa, Vadim | ██████████ 100% (9/9 tests + 2/2 scenarios) | health | – |
| Does it work | **level3-multibuilding**: Several buildings joined by walkways, routed as one site (Level 3; site.json, demo site in evals/datasets/sites) | Magnus | ██████████ 100% (7/7 tests) | 3.0 / 3 | – |
| Interaction | **tricky-users**: Scripted tricky-user conversations pass the rubric (asks only what it needs, shows understanding, followable) | Mousa | █████████░ 94% (17/18 scenarios) | 10.4 / 11 | hands-full-voice |
| Interaction | **walk-directions**: Directions a person can follow: turns (left/right, never compass), landmarks, "go around the printer", from geometry (app.building.directions, agent tool walk_directions) | Magnus, Mousa | ██████████ 100% (235/235 tests) | 4.0 / 4 | – |
| Interaction | **voice**: Voice in and out in the app | Maximilian | ████████░░ 80% (80% (owner's call)) | 4.0 / 5 | in: mic button, Web Speech API (Chrome, Edge, Safari); out: Read aloud + spoken answers on request. Gap: hands-full-voice scenario |
| Interaction | **ui-shows-it**: The app draws routes and highlights what the agent understood, on the floor plan | Maximilian | ██████████ 100% (100% (owner's call)) | 5.0 / 5 | checked in the browser 2026-10-25: routes, highlights, the right floor and 3D on factory, campus, MUCL |
| Creativity | **level5-addin**: Level 5 "live building": things move and deliveries arrive ("this is now here" in chat, or a barcode/RFID scan), every colleague and the map see it at once, and time questions work (delivered yesterday, where was it, arriving Monday) | Magnus, Vadim, Mousa, Maximilian | ██████████ 100% (11/11 tests + 3/3 scenarios) | health | – |
| Creativity | **room-calendar**: Room calendar add-in: rooms are calendar resources (as in Google or Outlook), so a guest asking for an event ("where is the hackathon?") is guided to the room it's booked in today (demo bookings in data/calendars/) | Mousa | ██████████ 100% (5/5 tests + 1/1 scenarios) | 10.0 / 10 | – |
| Submission demo | **demo-video**: Demo video and short technical explanation | Maximilian | ░░░░░░░░░░ 0% (0% (owner's call)) | 0.0 / 10 | not started |
| Health | **golden-geometry**: Geometry functions vs every golden answer | Vadim | ██████████ 100% (97/97 tests) | health | – |
| Health | **golden-graph**: Routing, queries and state vs golden answers and Owner A's checks | Magnus | ██████████ 100% (731/731 tests) | health | – |
| Health | **unseen-kinds-dev**: The same 12 new kinds on dev buildings (the set we may read and tune on) | Mousa | ██████████ 100% (96/96 questions) | health | – |
| Health | **agent-tools**: Agent tools vs every golden answer, no model | Mousa | ██████████ 100% (139/139 tests) | health | – |
| Health | **todo-open-questions**: Open questions to settle with a teammate: a strict-xfail reminder per question in tests/building/test_todo.py (none open now) | Magnus | ██████████ 100% (1/1 tests) | health | – |

Agent suites last run: benchmark 2026-10-25T06:28:32+00:00 by mousamax (default); warmup 2026-10-25T07:33:25+00:00 by mousamax (default); reworded 2026-10-25T07:06:45+00:00 by mousamax (default); scenarios 2026-10-25T07:40:02+00:00 by mousamax (default); unseen 2026-10-25T07:07:07+00:00 by mousamax (default); holdout 2026-10-25T07:30:39+00:00 by mousamax (default)

How a row moves: write or fix the failing eval first (red), then the code (green), then
`uv run ia scoreboard` and commit SCOREBOARD.md with your change.
