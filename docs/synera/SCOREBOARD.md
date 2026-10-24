# Team scoreboard

Generated 2026-10-24T20:13:10+00:00 by mousamax with `uv run ia scoreboard`. Don't edit by hand: change `scoreboard.yaml` (rows, stakes, manual status) or make a row's eval pass.

**Team: 78.9 / 100 estimated points.** Stakes are our guess at Synera's split.

| Person | Points |
|---|---|
| Mousa | 36.4 |
| Magnus | 20.5 |
| Vadim | 19.5 |
| Maximilian | 2.5 |

| Part | Row | Owners | Measured | Points | Failing / next |
|---|---|---|---|---|---|
| Does it work | **test-geometry**: Area, wall, stacking and 3D questions answered right end to end (dev, judges' scorer) | Vadim, Mousa | ██████████ 100% (97/97 questions) | 14.0 / 14 | – |
| Does it work | **test-graph**: Errand, delivery, reachability and what-if questions answered right end to end | Magnus, Mousa | ██████████ 100% (41/41 questions) | 6.0 / 6 | – |
| Does it work | **sunday-find-and-route**: Sunday-style find, count, route, step-free, closure and tour questions through the agent | Mousa, Magnus | ██████████ 100% (368/369 questions) | 8.0 / 8 | route_with_closure 28/29 |
| Does it work | **synera-route-rule**: Routes are the best under Synera's announced rule (per-metre stairs/lifts, forklift, bays) | Magnus | ██████████ 100% (523/523 tests) | 8.0 / 8 | – |
| Does it work | **unseen-wording**: The scored kinds asked in other words (reworded dev questions) | Mousa | ██████████ 100% (45/45 questions) | 2.0 / 2 | – |
| Does it work | **unseen-kinds**: 12 question kinds Synera hasn't shown, on test buildings, answers computed by code (held out, score only) | Mousa | ██████████ 98% (94/96 questions) | 3.9 / 4 | within_distance_count 6/8 |
| Does it work | **level4-from-svg**: Buildings drafted from SVG floor plans match the truth (Level 4) | Vadim | ██████████ 100% (8/8 tests) | 5.0 / 5 | – |
| Does it work | **level4-from-images**: Buildings drafted from plan images (PNG, e.g. floor_<level>_plain.png) match the truth (Level 4) | Vadim | ██████████ 100% (8/8 tests) | 5.0 / 5 | – |
| Does it work | **level3-multibuilding**: Several buildings joined by walkways, routed as one site (Level 3; site.json, demo site in evals/datasets/sites) | Magnus | ██████████ 100% (7/7 tests) | 3.0 / 3 | – |
| Interaction | **tricky-users**: Scripted tricky-user conversations pass the rubric (asks only what it needs, shows understanding, followable) | Mousa | █████████░ 93% (14/15 scenarios) | 14.0 / 15 | hands-full-voice |
| Interaction | **voice**: Voice in and out in the app | Maximilian | ░░░░░░░░░░ 0% (0% (owner's call)) | 0.0 / 5 | not started |
| Interaction | **ui-shows-it**: The app draws routes and highlights what the agent understood, on the floor plan | Maximilian | ░░░░░░░░░░ 0% (0% (owner's call)) | 0.0 / 5 | not started |
| Creativity | **level5-addin**: Level 5 "live building": things move and deliveries arrive ("this is now here" in chat, or a barcode/RFID scan), every colleague and the map see it at once, and time questions work (delivered yesterday, where was it, arriving Monday) | Magnus, Vadim, Mousa, Maximilian | ██████████ 100% (11/11 tests + 3/3 scenarios) | 10.0 / 10 | – |
| Submission demo | **demo-video**: Demo video and short technical explanation | Maximilian | ░░░░░░░░░░ 0% (0% (owner's call)) | 0.0 / 10 | not started |
| Health | **golden-geometry**: Geometry functions vs every golden answer | Vadim | ██████████ 100% (97/97 tests) | health | – |
| Health | **golden-graph**: Routing, queries and state vs golden answers and Owner A's checks | Magnus | ██████████ 100% (684/684 tests) | health | – |
| Health | **unseen-kinds-dev**: The same 12 new kinds on dev buildings (the set we may read and tune on) | Mousa | ██████████ 100% (96/96 questions) | health | – |
| Health | **agent-tools**: Agent tools vs every golden answer, no model | Mousa | ██████████ 100% (138/138 tests) | health | – |

Agent suites last run: benchmark 2026-10-24T19:06:20+00:00 by mousamax (default); warmup 2026-10-24T18:36:52+00:00 by mousamax (default); reworded 2026-10-24T18:42:51+00:00 by mousamax (default); scenarios 2026-10-24T20:11:50+00:00 by mousamax (default); unseen 2026-10-24T18:39:40+00:00 by mousamax (default); holdout 2026-10-24T18:40:51+00:00 by mousamax (default)

How a row moves: write or fix the failing eval first (red), then the code (green), then
`uv run ia scoreboard` and commit SCOREBOARD.md with your change.
