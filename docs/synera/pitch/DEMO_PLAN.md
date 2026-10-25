# Live demo plan: two scenes, one building (Sun, target done 12:20, freeze 13:00)

One story: **the building knows what happens where and when.** The guest uses that to find the hackathon;
the facility manager uses it to plan the hackathon. Both scenes run on MUCL (`data/MUCL/building.json`) and
its room calendar (`data/calendars/munich-urban-colab.json`).

| Scene | Who | What the jury takes away | Status | Owner |
|---|---|---|---|---|
| 1 · Reception | Guest | The front desk answers by itself, out loud, from the building calendar | Almost done | **Magnus** |
| 2 · Facility manager | FM | Floor plans in, building out, calendar on, and the AI plans the event like an FM would | To build | **Maximilian** (UI), **Mousa** (agent) |

---

## Scene 1 · The guest asks for the hackathon (Magnus)

**Beat:** The guest walks in and asks the receptionist "where is the hackathon?". The screen on the desk
answers before the receptionist can. It says out loud which room and how to get there. The route draws on
the 3D map. This is the "aha" moment.

**Already works:** voice (ElevenLabs), the `calendar` tool finds *Industrial Agents Hackathon* in `F3-OPEN-S`,
`walk_directions`, and the route animating in 3D.

**To do (Magnus):**
- Make the app's reception view readable from the stage: one big answer card, the room name and floor, and
  the route drawn. Hide everything a guest doesn't need.
- The screen speaks first: it starts talking as soon as the question is heard, with no click.
- Rehearse it 3 times on the stage laptop and mic. Write down the exact question wording that works.

**Done when:** the question is spoken, and the answer plays and the route draws without anyone touching
the laptop, 3 runs in a row.

---

## Scene 2 · The facility manager plans the hackathon

### The click path (about 60 seconds on stage)

| # | On screen | FM does | Behind it |
|---|---|---|---|
| 2.1 | Chat with 6 floor-plan photos attached | Hits **Enter** | Nothing live. The thumbnails come from `data/MUCL` plan images. |
| 2.2 | Our **3D reconstruction video** (already made) plays full screen | Watches | `docs/synera/pitch/video/` |
| 2.3 | The web app opens on the **MUCL map** (2D/3D, OSM around it) | Watches | Exists today |
| 2.4 | Banner: "Connect your building calendar" (Outlook/Google room resources) | Clicks **Connect** | Loads the calendar file |
| 2.5 | Events **fly onto their rooms** one by one. Each room shows a small schedule chip; hovering shows a preview (title, time, organizer) | Hovers 1–2 rooms | Calendar events + room resources |
| 2.6 | **Smart nudge** slides in: "New request: *Hackathon, Sat, 60 people, 12 teams*. Want me to plan it?" | Clicks **Plan it** | The request is seeded demo data |
| 2.7 | Agent works (tool calls tick), then a **plan card**: main room, breakout rooms, kitchen, toilets, each with the reason. The rooms **light up on the map**. | Reads, clicks **Book** | `plan_event` tool (below) |
| 2.8 | The booked rooms turn into calendar entries on the map | – | Same as 2.5 |

**What the FM would otherwise check by hand, and the plan considers:** the date is free, enough seats,
screens/projector, breakout rooms close to the main room, toilets and kitchen nearby, step-free access
from the entrance, and no clash with other events that day.

### Work split

**Maximilian (D, `web/`): the click path, 2.1–2.8**
- A demo entry point for scene 2, e.g. `?demo=fm`, so we never type URLs on stage.
- 2.1: a chat composer with 6 image thumbnails pre-attached. Enter plays the video (2.2). When the video
  ends, go to the map (2.3).
- 2.4–2.5: the connect banner. Then events animate onto their rooms (stagger ~80 ms), with a schedule chip
  per room and a hover preview.
- 2.6: the nudge card. 2.7: render the `plan_event` result: a plan card, highlight `highlight` ids on the
  map, the main room in a stronger colour. 2.8: booked state.
- **Fallback:** if the agent call fails or takes longer than 8 s, show the saved plan from
  `data/calendars/munich-urban-colab.plan.json` (Mousa commits it). The audience can't tell the difference.

**Mousa (C, `src/app/agents/building_guide/`, `data/calendars/`): the data and the plan**
- Widen the calendar file:
  - `resources`: per bookable room, `capacity` and `equipment` (screens, projector, whiteboard), as
    Outlook/Google room resources carry them.
  - Events across the week, so every main room has a schedule, and Saturday has some clashes the plan
    must avoid.
  - `requests`: the seeded hackathon request for the nudge.
- New agent tool `plan_event(title, date, people, teams, needs)`. It filters rooms that are free on that
  date and have enough capacity and screens. Then it picks a main room, plus breakouts within a short walk
  of it (`route`), and the nearest kitchen and toilets. It returns the contract below.
- An eval case: the hackathon request → the plan uses free rooms only, the seats cover 60, and every
  breakout is within N m.
- Commit the saved fallback plan JSON. Expose the tool through the API the web app already calls.

**Magnus (A):** scene 1 as above.

**Vadim (B), to confirm:** record a **backup screen capture** of both scenes as soon as each runs end to
end. If the live run breaks on stage, we play the recording. Also help Magnus with the stage mic and voice
check.

### Contract: `plan_event` result (Mousa writes, Maximilian renders)

```json
{
  "request": {"title": "Hackathon", "date": "2026-10-31", "people": 60, "teams": 12},
  "plan": [
    {"role": "main", "room_id": "F1-ARENA", "room": "Arena (Auditorium)", "floor": "1. OG",
     "why": "Free Sat, 80 seats, projector + 2 screens"},
    {"role": "breakout", "room_id": "F1-SEM1", "room": "Seminarraum 1", "floor": "1. OG",
     "why": "Free, screen, 25 m from the Arena"},
    {"role": "kitchen", "room_id": "F1-TEEK", "room": "Teeküche", "floor": "1. OG", "why": "18 m from the Arena"},
    {"role": "toilets", "room_id": "F1-WC1", "room": "WC", "floor": "1. OG", "why": "Same corridor"}
  ],
  "checked": ["free on Sat", "seats ≥ 60", "screens", "breakouts ≤ 40 m", "kitchen + WC nearby", "step-free"],
  "avoided": [{"room": "Seminarraum 2", "why": "Startup office hours 10:00–12:00"}],
  "highlight": ["F1-ARENA", "F1-SEM1", "F1-TEEK", "F1-WC1"]
}
```

The room ids and values above are illustrative; the tool computes the real ones. `role` values: `main`,
`breakout`, `kitchen`, `toilets`. `highlight` uses the same ids as the guest scene's `highlight`.

---

## Timeline (from 11:20)

| By | Who | Milestone |
|---|---|---|
| 11:35 | Mousa | Calendar file widened + fallback plan JSON committed. Maxi builds on these. |
| 11:50 | Maximilian | 2.1–2.5 click through on `?demo=fm` with the fallback data |
| 11:50 | Magnus | Scene 1 reception view done, rehearsed |
| 12:00 | Mousa | `plan_event` live in the API, eval green |
| 12:10 | Maximilian | 2.6–2.8 wired to the live tool, fallback in place |
| 12:20 | All | Full run-through of both scenes on the stage laptop. Vadim records the backup. |
| 13:00 | – | Code freeze |

## Rules for the next hour

- Only build what the click path uses. If it isn't in a row above, it waits until after the pitch.
- Every step has a fallback. A live demo that stalls loses more than a scripted step we are honest about.
- Nothing building-specific in the agent tools: `plan_event` must work on any building with a calendar
  (55 points are scored on unseen buildings). Demo data lives in `data/calendars/`, not in code.
- **Honest in Q&A:** the photos → 3D step is a video of our reconstruction, and the calendar is demo data
  in the shape of Outlook/Google room resources. The plan itself, the routes and the map are computed live.
