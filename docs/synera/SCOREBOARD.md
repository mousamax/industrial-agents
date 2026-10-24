# Team scoreboard

Generated 2026-10-24T15:52:59+00:00 by Magnus Saurbier with `uv run ia scoreboard`. Don't edit by hand: change `scoreboard.yaml` (rows, stakes, manual status) or make a row's eval pass.

**Team: 62.3 / 100 estimated points.** Stakes are our guess at Synera's split.

| Person | Points |
|---|---|
| Mousa | 29.9 |
| Vadim | 17.0 |
| Magnus | 15.4 |
| Maximilian | 0.0 |

| Part | Row | Owners | Measured | Points | Failing / next |
|---|---|---|---|---|---|
| Does it work | **test-geometry**: Area, wall, stacking and 3D questions answered right end to end (dev, judges' scorer) | Vadim, Mousa | ██████████ 100% (97/97 questions) | 14.0 / 14 | – |
| Does it work | **test-graph**: Errand, delivery, reachability and what-if questions answered right end to end | Magnus, Mousa | ██████████ 100% (41/41 questions) | 6.0 / 6 | – |
| Does it work | **sunday-find-and-route**: Sunday-style find, count, route, step-free, closure and tour questions through the agent | Mousa, Magnus | ██████████ 100% (368/369 questions) | 10.0 / 10 | route_with_closure 28/29 |
| Does it work | **synera-route-rule**: Routes are the best under Synera's announced rule (per-metre stairs/lifts, forklift, bays) | Magnus | █████████░ 93% (509/549 tests) | 7.4 / 8 | test_a4_3_stairs_and_lift_cost_per_metre (xfail) ×30, test_a4_3_stairs_and_lift_cost_per_metre (skipped) ×10 |
| Does it work | **unseen-wording**: The scored kinds asked in other words (reworded dev questions) | Mousa | ██████████ 100% (45/45 questions) | 4.0 / 4 | – |
| Does it work | **level4-from-svg**: Buildings drafted from SVG floor plans match the truth (Level 4) | Vadim | ██████████ 100% (8/8 tests) | 5.0 / 5 | – |
| Does it work | **level4-from-images**: Buildings drafted from plan images (PNG, e.g. floor_<level>_plain.png) match the truth (Level 4) | Vadim | ██████████ 100% (8/8 tests) | 5.0 / 5 | – |
| Does it work | **level3-multibuilding**: Several buildings joined by walkways (Level 3) | Magnus | ░░░░░░░░░░ 0% (no eval yet) | 0.0 / 3 | a walkway model and a test that routes across two buildings |
| Interaction | **tricky-users**: Scripted tricky-user conversations pass the rubric (asks only what it needs, shows understanding, followable) | Mousa | ███████░░░ 73% (8/11 scenarios) | 10.9 / 15 | first-day-coffee, vague-press, add-a-door |
| Interaction | **voice**: Voice in and out in the app | Maximilian | ░░░░░░░░░░ 0% (0% (owner's call)) | 0.0 / 5 | not started |
| Interaction | **ui-shows-it**: The app draws routes and highlights what the agent understood, on the floor plan | Maximilian | ░░░░░░░░░░ 0% (0% (owner's call)) | 0.0 / 5 | not started |
| Creativity | **level5-addin**: One Level 5 add-in done well (pick it as a team) | Magnus, Vadim, Mousa, Maximilian | ░░░░░░░░░░ 0% (0% (owner's call)) | 0.0 / 10 | not picked yet |
| Submission demo | **demo-video**: Demo video and short technical explanation | Maximilian | ░░░░░░░░░░ 0% (0% (owner's call)) | 0.0 / 10 | not started |
| Health | **golden-geometry**: Geometry functions vs every golden answer | Vadim | ██████████ 100% (97/97 tests) | health | – |
| Health | **golden-graph**: Routing, queries and state vs golden answers and Owner A's checks | Magnus | ██████████ 100% (702/702 tests) | health | – |
| Health | **agent-tools**: Agent tools vs every golden answer, no model | Mousa | ██████████ 100% (138/138 tests) | health | – |

Agent suites last run: benchmark 2026-10-24T15:06:13+00:00 by mousamax (default); warmup 2026-10-24T15:07:21+00:00 by mousamax (default); reworded 2026-10-24T15:09:55+00:00 by mousamax (default); scenarios 2026-10-24T15:10:17+00:00 by mousamax (default)

How a row moves: write or fix the failing eval first (red), then the code (green), then
`uv run ia scoreboard` and commit SCOREBOARD.md with your change.
