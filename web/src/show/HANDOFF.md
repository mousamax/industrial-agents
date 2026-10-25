# Route animation: handoff

The stage demo at `/show` (or any URL with `?show`) plays a scripted "Where is the Hackathon" in MUCL: a pulsing
mic, the prompt types itself, the guide's answer appears, the route draws in through the building in 3D, and
"Let's go" appears. Next step (another agent): **use the same animation for every route answer in the normal
app.** This note says which piece does what and how to reuse it.

Run it: API on :8000, then `npm --prefix web run dev` → http://localhost:5173/show, or build + preview
(`npm --prefix web run build`, `npm --prefix web run preview -- --port 4174`, optionally
`cloudflared tunnel --url http://localhost:4174`) → `<host>/show`. Tap anywhere; ↻ (top right) re-arms the mic.

## What happens, in order

| Time | What | Where |
|---|---|---|
| tap | Greyed welcome page + pulsing mic fades out | `ShowIntro.tsx`, `.show-*` classes in `index.css` |
| +0.3 s | Prompt types into the bar (28 ms/char) | `runShow` in `AgentWorkspace.tsx` |
| send | Phase `working` ("Thinking about that…"); 60 ms later the map tilts to 3D (`setView3d({link: null})`) so the 3D engine is loaded before the line starts | `runShow` |
| +0.75 s | The captured real answer (`SHOW_TURN`) goes through the normal `extractLiveTurn` → `showResult` path, opening on the route's **first** floor, tilted | `runShow` → `answer()` |
| +0.6 s settle | Route draws in, frame by frame (`requestAnimationFrame`), along `revealTimeline` | `answer()` → `tick` |
| draw | **Phone:** answer card slides down, camera follows the head of the line up close, room labels and floor chips hidden. **Desktop:** card stays, map switches floor as the line reaches it | `tick`, `AssistantView`, `MapView`, `Overlay3D` |
| end + 0.8 s | Map returns to the entrance floor, flat (top-down); card slides back up; "Let's go" pops in | `endHoldMs` timer in `tick` |

All timings are in `SHOW_TIMING` (`show.ts`).

## Files

### New: `web/src/show/`
- **`show.ts`** — everything show-specific plus the two reusable route functions:
  - `SHOW`, `SHOW_BUILDING`, `SHOW_PROMPT`, `SHOW_TURN` (the guide's real answer, captured once from the live
    `building_guide`: calendar + route tool calls + text, as a `UIMessage`). Demo only.
  - `SHOW_TIMING`: typing, working, settle, `walkMps` (line speed on a floor, m/s), `minFloorMs` (minimum time
    on each floor, also the stair floors where the line barely moves), `hopMs` (each floor change), `endHoldMs`,
    `followRadiusM` (phone follow-cam: metres kept around the line's head).
  - **`truncateRoute(route, frac)`** — the first `frac` (0..1) of a `ShowRoute` by walking length. A floor change
    counts `FLOOR_CHANGE_M` (8 m); the next floor's segment appears once the draw is past the middle of the hop.
    Returns the same object at `frac >= 1`. **Reusable as is.**
  - **`revealTimeline(route)`** → `{ totalMs, at(ms) }`: piecewise-linear time → reveal fraction from
    `walkMps`/`minFloorMs`/`hopMs`, with consecutive same-floor segments grouped as one floor. **Reusable as is.**
- **`ShowIntro.tsx`** — the mic overlay. Demo only.

### Changed
- **`store.ts`**
  - `routeReveal: number | null` + `setRouteReveal`: how much of the route is drawn. `null` = normal drawing
    (the old 1.1 s 2D draw-in). Anything else switches the map to "the caller drives the reveal".
  - `focus.points` / `focus.follow` + `focusPoints(floorId, points, follow?)`: fit raw points (building metres)
    on a floor into view. With `follow: true` and called every frame, the camera eases after them instead of
    animating each call.
  - `initialBuildingId()` returns MUCL in show mode.
- **`components/map/MapView.tsx`**
  - `drawnRoute = truncateRoute(route, routeReveal)` feeds the 2D `RouteLines` (with `instant`) and the 3D
    engine (`engine.setRoute`). Pins/markers keep the **full** route, so the destination pin is there from the
    start and only the line grows.
  - Focus effect: `focus.points` branch; follow mode uses `chase()` (`FOLLOW_EASE` share of the way per frame)
    and allows `FOLLOW_MIN_SPAN_M` (9 m) instead of 16 m.
  - `Overlay3D quiet` while `0 <= routeReveal < 1`.
- **`components/map/layers.tsx`** — `RouteLines` gets `instant` (skip its own draw-in; the caller truncates).
- **`components/map/Overlay3D.tsx`** — `quiet` hides room-name labels and "Continue on Floor X" chips (they
  covered the thin line). POI icons stay.
- **`components/map3d/Map3D.ts`** + **`floorMesh.ts`** (apply to **all** 3D routes and buildings already, not
  only show mode):
  - Stairwell floors (`type === 'stair'` or room_type ~ `stair`) go into a separate `see` geometry drawn with
    `seeMat` (opacity 0.18, no depth write): you look down the stairwell and see the line come up.
  - Route x-ray: the route mesh gets a child with the same geometry and `routeXrayMat` (opacity 0.45, no depth
    test, renderOrder 10): where walls or floors hide the line, a ghosted line still shows.
- **`components/agent/AgentWorkspace.tsx`** — `runShow` / `cancelShow` (timers + rAF), `showIntro`,
  `showDrawing` state; the map-sync effect uses phone bottom inset 110 instead of 420 while `showDrawing`;
  `clearView` cancels the show and re-arms the mic.
- **`components/agent/AssistantView.tsx`** — new prop `drawing` (in `ViewProps`): phone card slides off
  (`translate-y` + opacity, 500 ms) while true; "Let's go" is `invisible` while drawing, then pops in
  (`.show-pop`) when `routeReveal !== null`.
- **`index.css`** — `.show-intro`, `.show-mic*`, `.show-halo`, `.show-pop` and their keyframes.
- **`.claude/launch.json`** — `web-preview` config (build preview on 4174).

## Bringing it into the normal app

The live path already ends in `showResult(extracted.result)` (the `liveMode` effect in `AgentWorkspace.tsx`).
The animation is the `answer()` body of `runShow` after `extractLiveTurn`. Suggested shape:

1. Pull the draw out of `runShow` into a reusable `playRoute(result)` in `AgentWorkspace.tsx` (or a hook):
   `setRouteReveal(0)`, `setShowDrawing(true)` (rename it, e.g. `routeDrawing`),
   `showResult({...result, focusFloor: firstFloor, view3d: result.view3d ?? { link: null }})`, the rAF `tick`
   (reveal + phone follow-cam / desktop floor switch), then the end-hold timer
   (`setResult(r => ({...r, view3d: undefined}))`, `setRouteDrawing(false)`).
2. Call it from the live effect when `extracted.result.kind === 'route' && extracted.result.route` instead of
   `showResult`; keep `showResult` for everything else. `runShow` then just calls `playRoute` too.
3. Move `truncateRoute`, `revealTimeline` and the draw timings out of `show/` (e.g. `lib/routeReveal.ts`) so
   the normal app doesn't import the demo module; `show.ts` keeps `SHOW*`, `SHOW_TURN` and the typing timings.
4. The 3D tilt before the draw: in show mode it happens during "Thinking". Live, the answer arrives without
   warning, so either tilt when the route tool call starts streaming (the `trace` already knows), or add a short
   settle (`settleMs`) after `showResult` before the draw starts. Without it the first floor draws flat.

Things to keep in mind:
- **Cancel on everything that changes the screen:** `clearView` (new question, reset, building switch) must
  stop the rAF and timers and set `routeReveal` back to `null`, as `cancelShow` does. A follow-up question
  arriving mid-draw must also cancel it. Walk mode (`Let's go`) after a draw works as is.
- **`routeReveal` stays at `1` after a draw** until `clearView`. That's intentional: setting it to `null` makes
  `RouteLines` replay its own 1.1 s draw-in (new route object). Only reset it together with the route.
- **The map-sync effect re-runs on `result` changes** and calls `setRoute` → `focusOn(route)` (whole route).
  The tick only steers the camera on frames after that, so don't change `result` mid-draw.
- **Phone vs desktop** is the `phone` flag (`useIsPhone`, < 768 px) passed into the workspace; the follow-cam
  and card slide are phone only on purpose (the desktop sidebar leaves room for the whole route).
- **Floor label in the header** follows the `floor` state, not the drawn floor. On desktop it shows the start
  floor during the draw.
- `FLOOR_CHANGE_M` (8 m) is a drawing weight, not a real distance; routing and `length_m` are untouched.
- `npm run build` + `npx tsc -b` + `npx eslint src` are the checks; there are no web unit tests. Note the
  `lib` target: no `Array.prototype.at`.
