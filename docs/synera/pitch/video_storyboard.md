# Pitch video: storyboard and script (draft v1)

**Length:** 60 s hard cap · **Format:** 16:9, animated (3D MUCL model + UI screens) · **Assistant name:** Navi (working title)

**Setting:** Munich Urban Colab (MUCL), Freddie-Mercury-Straße 5, rebuilt from its floor plans in `data/MUCL/building.json`.
Every room, floor and route below is real in that file.

**Story idea:** two people, one building model. The guest **finds** the hackathon; the facility manager
**booked** it. At the end both scenes land on the same rooms, so the audience sees that one building model
serves both sides.

| | Time | Scene |
|---|---|---|
| 0 | 0:00–0:05 | Cold open: MUCL assembles from floor plans |
| 1 | 0:05–0:30 | The guest: "Where's the hackathon?" |
| 2 | 0:30–0:53 | The facility manager: "@Navi, book it." |
| 3 | 0:53–1:00 | Payoff and title card |

---

## 0 · Cold open (0:00–0:05)

| Shot | Visual | Audio / on-screen text |
|---|---|---|
| 0.1 (0:00–0:03) | Flat 2D floor plan (EG) on dark background. Walls extrude up, floors 1–4 stack on top, the camera orbits. | Low synth swell. |
| 0.2 (0:03–0:05) | Camera stops on the full 3D building. Label fades in. | **TEXT:** "Munich Urban Colab: 5 floors, 300+ spaces, rebuilt from floor plans." |

## 1 · The guest (0:05–0:30)

| Shot | Visual | Audio / on-screen text |
|---|---|---|
| 1.1 (0:05–0:08) | Camera dives through the glass front into **Empfang / Foyer Süd-Ost**. A guest with a backpack walks in. | Door swoosh, foyer ambience. |
| 1.2 (0:08–0:11) | At the **Empfangstresen**: the guest leans in. | **GUEST:** "Hi, where's the hackathon?" |
| 1.3 (0:11–0:13) | Receptionist, headphones on, doesn't look up and points at a tablet on the counter with a "Navi" sticker. | Shrug sound. *(Comic beat, keep it short.)* |
| 1.4 (0:13–0:15) | Close-up on the tablet: a pulsing mic waveform, then the transcription types out. | **SCREEN:** 🎙 "Where's the hackathon?" |
| 1.5 (0:15–0:21) | Tablet screen: tool calls tick in one by one, each with a ✓. | **SCREEN:**<br>`calendar.find_event("hackathon", today)` → *Industrial Agents Hackathon* ✓<br>`find("Besprechung 03.21")` → Floor 3 ✓<br>`route(from="Empfang", to="Besprechung 03.21")` → 58.7 m, elevator ✓ |
| 1.6 (0:21–0:25) | Answer card on the tablet, with a mini 3D map beside it highlighting floor 3. | **NAVI (voice):** "The Industrial Agents Hackathon is on the 3rd floor, Besprechung 03.21 to 03.25. Take the central elevator to floor 3, turn left into the meeting-room corridor, second door on the right." |
| 1.7 (0:25–0:30) | The 3D model goes semi-transparent. A glowing route line runs Empfang → **Aufzug Mitte** → floor 3 → **Flur Besprechungsräume 3.OG** → 03.21. The guest's dot follows it and arrives at a room full of laptops. | Upbeat hit. **TEXT:** "Guest → room in 10 seconds. No staff needed." |

## 2 · The facility manager (0:30–0:53)

| Shot | Visual | Audio / on-screen text |
|---|---|---|
| 2.1 (0:30–0:32) | Hard cut, "3 WEEKS EARLIER" stamp. Facility manager at a desk with coffee and two monitors. | *Bing* (mail notification). |
| 2.2 (0:32–0:36) | Email opens on screen. Key lines highlight as the camera pans. | **SCREEN (email):**<br>Subject: *Hackathon at MUCL, Sat 24 Oct*<br>"60 participants, ~12 teams. One room for kickoff and pitches, breakout rooms for teams, step-free access, coffee close by. Please book." |
| 2.3 (0:36–0:38) | The facility manager sighs, clicks **Reply**, and types one line. "@Navi" highlights as a mention chip. | **SCREEN:** "**@Navi** please handle this." *Send* swoosh. |
| 2.4 (0:38–0:46) | Switch to the Navi agent screen. On the left a "thinking" stream; on the right tool calls run. Numbers count up. | **SCREEN (thinking):** "60 people · 12 teams of 5 · plenary + breakouts · step-free · coffee nearby"<br>**SCREEN (tools):**<br>`list_rooms(type="meeting")` → 22 candidates ✓<br>`room_details(...)` → seats, area, projector ✓<br>`calendar.free_slots("Sat 24 Oct")` → 14 free ✓<br>`nearest("coffee", from="Besprechung 03.21")` → Küche 03.26, 32 m ✓<br>`route(step_free=True, ...)` → all reachable by elevator ✓ |
| 2.5 (0:46–0:50) | Three option cards slide in. Option A pulses as "Recommended". | **SCREEN:**<br>**A ★** Arena (1st floor) for kickoff + Besprechung 03.21–03.25 for teams. Same elevator core, coffee on floor 3.<br>**B** Seminarraum 1–3 (1st floor), all on one floor, tighter.<br>**C** Foyer/Eventbereich + Projekträume (EG), largest, but public noise. |
| 2.6 (0:50–0:53) | The facility manager clicks **Book A**. The rooms light up in the 3D model. Calendar entries pop in. Reply email auto-sent to the organiser. | **SCREEN:** "✓ 6 rooms booked · signage + guest directions published." |

## 3 · Payoff (0:53–1:00)

| Shot | Visual | Audio / on-screen text |
|---|---|---|
| 3.1 (0:53–0:56) | Split screen: left is the facility manager's booked rooms glowing on floor 3, right is the guest arriving at the same door. Match cut. | **TEXT:** "Booked by one sentence. Found by one question." |
| 3.2 (0:56–1:00) | Logo card on the dark building silhouette. | **TEXT:** "**Navi**: talk to your building." · team name · "Synera track" |

---

## Voice-over script (if we use narration instead of / on top of on-screen text)

About 120 words, which fits 60 s at a calm pace. Dialogue lines are bold.

> **[0:00]** Every building has a map. Almost nobody can use it.
> **[0:05]** **"Hi, where's the hackathon?"** *(receptionist points at the tablet)*
> **[0:13]** Navi hears the question, checks today's events, finds the room in the building model, and plans the route.
> **[0:21]** **"Third floor, Besprechung 03.21. Take the central elevator, turn left, second door on the right."**
> **[0:30]** Three weeks earlier. Sixty people, twelve teams, step-free, coffee nearby. The facility manager writes one line: **"@Navi, please handle this."**
> **[0:38]** Navi works out the rooms, checks seats, availability, coffee distance and step-free routes, and comes back with three options.
> **[0:50]** One click, and it's booked.
> **[0:53]** Booked by one sentence. Found by one question. **Navi: talk to your building.**

---

## Honesty notes (for the Q&A)

- **Real today:** the MUCL building model, room names, floors, elevator and corridors; the agent tools
  `find`, `list_rooms`, `room_details`, `route` (incl. step-free), `nearest`.
- **Shown but not built:** `calendar.find_event`, `calendar.free_slots`, booking, voice input, the email
  integration. The video presents these as the product vision; if a judge asks, we say so.
- Distances in the video are real `route` / `nearest` output on MUCL: Empfang → Besprechung 03.21 is
  58.7 m step-free via Aufzug Mitte, Arena → 03.21 is 59 m, nearest coffee from 03.21 is Küche 03.26 at 32 m.
  Room areas (Arena 219 m², Seminarraum 3 109 m², Foyer + Projekträume 445 m²) come from the polygons.

## Animation

Source: `web/video/` (`index.html` + `scene.js`; every frame is a pure function of time). Render with
`web/video/render.mjs`; see `web/video/README.md`. Output: `docs/synera/pitch/video/navi_pitch.mp4`.
Change from the storyboard: the payoff (3.1) is one 3D shot where the booked rooms glow and the guest's
route arrives at the same door, instead of a split screen.

## Open decisions

1. **Order:** guest first (the hook is funnier) vs. facility manager first (follows cause and effect). The
   draft keeps guest first and uses "3 weeks earlier" to flip time.
2. **Name:** Navi (Zelda's "Hey, listen!" fairy, a fun Easter egg) or something neutral.
3. **Narration:** voice-over vs. on-screen text only (text-only is safer in a noisy hall).
