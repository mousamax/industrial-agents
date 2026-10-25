# data/multi_building/: multi-building routing cases

33 hand-designed cases where the hard part is choosing between buildings, floors and the outside: yard or
bridge, courtyard or round the U, which order to run errands across a site. Not part of any score; a dev set
for `RouteGraph` and the agent. Checked by `tests/building/test_multi_building.py`.

| Folder | What | Cases |
|---|---|---|
| `xmb-bridge-works/` | Two halls on one plan. Ground floor: an open yard (`outdoor` space) between them, truck gates 4.5 m, a stepped pedestrian door on Hall A's side. First floor: a pedestrian bridge with 1.4 m doors. Hall A's stairs are at its far end, Hall B's at the yard end. | 13: bridge vs yard by floor, step-free flips, forklift can't use the bridge, forklift no-route, yard closed, three tours |
| `xmb-u-wing/` | A U-shaped institute. The courtyard (`outdoor`) cuts across the U on the ground floor only; a 0.9 m fire door splits the ground-floor base corridor; courtyard doors: stepped 1.0 m, ramp 1.2 m, east 1.8 m; lift in the base, stairs in each wing. | 11: courtyard vs inside (from 3 % to 2.5× apart), wheelchair, 1.1 m trolley, first floor down-across-up, closures, two tours |
| `xmb-tour-campus/` | Level 3 `site.json`: Hörsaalzentrum 024, Produktion West 29, Laborgebäude 025 and three walkways (the direct garden path joins two stepped entrances). | 9: direct vs step-free via the factory, tours with a stop in another building, best order of two errands, no step-free tour, an ambiguous room name across buildings |

## Why "outside" is a space here

Synera's rule has no way round the outside: an entrance only leads out. A Level 3 walkway (`model.Walkway`)
can only join two entrances, so it can't be a first-floor bridge. Both plans therefore model the open air as
`outdoor` spaces (a kit space type, used for balconies) with real doors, walked door to door like any other
space, and the bridge as a corridor space. No model change needed.

## Files

- `_build.py` writes the two `building.json` plans (kit schema, checked by `app.building.build.validate`) and
  the `site.json`.
- `_cases.py` holds every case **by hand**: stops, rules, closures, what the best route must use and avoid,
  the naive alternative, the trap. It computes the answers and refuses to write a case whose intent fails,
  whose alternative isn't more than 1 % worse, or whose "invalid" walking route is actually valid.
- `<building>/questions.jsonl`: the kit's `questions_basic` route/tour shape (`space_path`, `element_path`,
  `optimal_length_m`), answer types `route`, `tour`, `no_route`, `clarification`, plus `extra` (trap, rules,
  closures, must_use/must_avoid, the alternative's length, `routegraph_agrees`).

Regenerate: `uv run python data/multi_building/_build.py && uv run python data/multi_building/_cases.py`.

## Answers follow Synera's rule, not just RouteGraph

Expected answers never pass *through* a production bay (`ROUTE_SCORING.md`); RouteGraph doesn't enforce
that yet. Where they differ, `extra.routegraph_agrees` is false and the test is a strict xfail
(`xmb-campus-02`, `-04`, `-08`: step-free across the factory). Most kit factories (e.g. Fertigung Ost 27) only join
the office entrance to the hall gates through a bay, which is why the site uses Produktion West 29.

Space sequences can't name a door: in `xmb-uwing-02` the step-free route has the same spaces as the walking
one but uses the ramp door (75.5 m vs 43.1 m). Only `element_path` and the length show it.
