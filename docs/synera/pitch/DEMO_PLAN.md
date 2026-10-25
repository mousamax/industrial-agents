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
- **No model call needed for the clicks:** 2.5–2.8 use the calendar endpoints below. They answer instantly
  and give the same plan the agent gives in chat, so nothing on stage waits on a model.

**Mousa (C): done.** The mock calendar, the `plan_event` agent tool and the endpoints are in place.
- `data/calendars/munich-urban-colab.json`:
  - `resources`: 36 bookable rooms with seats and equipment, in the shape of Outlook/Google room resources.
  - 26 events over the next 7 days. The week always starts on the day the app runs.
  - On Saturday the Foyer (Open House) and Seminarraum 2 (Founder bootcamp) are booked, so the plan has to
    avoid them.
  - `requests`: the hackathon request from Lena Huber (UnternehmerTUM): Saturday, 60 people, 12 teams,
    projector, screen and catering.
- In the chat the agent knows the request is waiting. "A hackathon request just came in, can you plan it?"
  calls `plan_event` and answers room by room with the reasons and the rooms it avoided. "Is Seminarraum 2
  free that day?" calls `calendar` for that room. Scenario `calendar-plan-hackathon-fm` passes.
- Scene 1 still finds the hackathon today in `F3-OPEN-S`. `calendar-hackathon-guest` passed 3 of 4 runs:
  once the agent called `highlight_rooms` last and replied only "I've highlighted the room on your map".
  Magnus, rehearse the exact wording.

### Endpoints for the app (Maximilian)

| Step | Call | Gives |
|---|---|---|
| 2.4–2.5 Connect | `GET /buildings/munich-urban-colab/calendar` | `rooms` {id: room, floor_id, seats, equipment}, `events` for 7 days (room_id, date, weekday, from/until or `when: "all day"`, status, organizer, about), `requests` |
| 2.6 Nudge | the same `requests[0]` | title, from, weekday, date, start, end, people, teams, needs, message |
| 2.7 Plan it | `POST /buildings/munich-urban-colab/calendar/plan` with `{"request_id": "req-hackathon"}` | the plan below, instantly |
| 2.8 Book | `POST /buildings/munich-urban-colab/calendar/book` with the plan as the body | `booked`, `events`, `highlight`; from then on the calendar and the agent show the booking |

Bookings live in memory until the API restarts, so restart it before each run-through and before the
pitch, so the request is waiting again.

**Magnus (A):** scene 1 as above.

**Vadim (B), to confirm:** record a **backup screen capture** of both scenes as soon as each runs end to
end. If the live run breaks on stage, we play the recording. Also help Magnus with the stage mic and voice
check.

### Contract: the plan (the endpoint and the agent's `plan_event` tool return the same thing)

The real result for the hackathon request, shortened:

```json
{
  "request": {"title": "Industrial AI Hackathon", "date": "2026-10-31", "weekday": "Saturday",
              "from": "09:00", "until": "21:00", "people": 60, "teams": 12, "needs": ["projector", "screen", "catering"]},
  "plan": [
    {"role": "main", "room_id": "F1-ARENA", "room": "Arena (Auditorium) (Floor 1 …)", "floor": "Floor 1 …",
     "why": "Free then, 120 seats, projector, screen, microphones, video_call", "seats": 120},
    {"role": "breakout", "room_id": "F1-SEM3", "why": "Free, 4 teams, projector, screen, whiteboard, 28 m from the main room", "teams": 4, "walk_m": 27.9},
    {"role": "breakout", "room_id": "F1-SEM1", "why": "Free, 4 teams, …, 44 m from the main room", "teams": 4, "walk_m": 44.0},
    {"role": "breakout", "room_id": "F1-CAD", "why": "Free, 4 teams, screen, 59 m from the main room", "teams": 4, "walk_m": 59.0},
    {"role": "catering", "room_id": "F0-GAST", "why": "80 m from the main room, step-free", "walk_m": 80.0},
    {"role": "kitchen", "room_id": "F1-TEEK", "why": "23 m from the main room, step-free", "walk_m": 23.0},
    {"role": "toilets", "room_id": "F1-WC2", "why": "31 m from the main room, step-free", "walk_m": 31.0}
  ],
  "checked": ["free on Saturday 09:00–21:00", "120 seats for 60 people plus 10%", "projector, screen in the main room",
              "12 of 12 teams in breakouts within 60 m", "kitchen and toilets nearby",
              "step-free from Eingangsbereich (Ground floor …), 55 m"],
  "avoided": [{"room_id": "F0-FOYER", "why": "Open House: Munich Urban Colab 10:00–16:00"},
              {"room_id": "F1-SEM2", "why": "Founder bootcamp 09:00–17:00"}],
  "booked": false,
  "highlight": ["F1-ARENA", "F1-SEM3", "F1-SEM1", "F1-CAD", "F0-GAST", "F1-TEEK", "F1-WC2"]
}
```

`role` is one of `main`, `breakout`, `catering`, `kitchen`, `toilets`. Draw `main` strongest, the
breakouts next, and services lightest; `avoided` rooms in a muted "booked" style. If nothing fits, `plan`
is `[]` and `note` says why. `highlight` uses the same ids as the guest scene's `highlight`.

---

## Timeline (from 11:20)

| By | Who | Milestone |
|---|---|---|
| 11:35 | Mousa | ✓ Calendar widened, endpoints live. Maxi builds on these. |
| 11:50 | Maximilian | 2.1–2.5 click through on `?demo=fm` with `GET /calendar` |
| 11:50 | Magnus | Scene 1 reception view done, rehearsed |
| 12:00 | Mousa | ✓ `plan_event` in the agent, scenario green |
| 12:10 | Maximilian | 2.6–2.8 wired to `/calendar/plan` and `/calendar/book` |
| 12:20 | All | Full run-through of both scenes on the stage laptop. Vadim records the backup. |
| 13:00 | – | Code freeze |

## Rules for the next hour

- Only build what the click path uses. If it isn't in a row above, it waits until after the pitch.
- Every step has a fallback. A live demo that stalls loses more than a scripted step we are honest about.
- Nothing building-specific in the agent tools: `plan_event` must work on any building with a calendar
  (55 points are scored on unseen buildings). Demo data lives in `data/calendars/`, not in code.
- **Honest in Q&A:** the photos → 3D step is a video of our reconstruction, and the calendar is demo data
  in the shape of Outlook/Google room resources. The plan itself, the routes and the map are computed live.
