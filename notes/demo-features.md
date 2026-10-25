# Demo features: where we go beyond the brief

What `building_guide` and the app can do beyond what the brief asks, and which of those make good scenes for
the demo video. Snapshot of 25 Oct 2026, read from the code (`tools.py`, `agent.py`, `drafting.py`,
`room_calendar.py`, `api/main.py`) and the git log; not re-run live for this note.

## What the brief asks

Find, route, edit, react on a graph of rooms, doors, stairs and lifts. The kit's starter tools only cover
search and routing. The scored kinds are covered by `find`, `room_details`, `list_rooms`, `count_items`,
`distance_3d`, `route`, `nearest`, `reachable_rooms` and `what_if`: 100% of scored kinds on dev, 368/369 on
the warm-up set.

## Beyond the brief

| Feature | What it does | Why it's beyond the brief |
|---|---|---|
| `walk_directions` | Directions inside each room, built on turns and landmarks ("go around the printer, keeping it on your left"), with the drawn path round walls and obstacles | The brief only needs a list of rooms |
| `plan_visits` | "Printer, coffee and a toilet, in the best order": tries every order and every choice of room, proves when the result is shortest | Also returns `greedy_total_m`, the naive trip, so we can show the saving |
| `walk_distances` | Every room on a floor ranked by its walk to the nearest exit; "how many rooms within X m", counted in code | Evacuation and fire-drill questions |
| `what_if` | Simulates a new door or a closure and lists which rooms' walk out changes; nothing is changed | Planning before building |
| Plan edits | `add_door`, and `add_wall`, which splits a room into two equal halves with their own numbers and warns when a half has no door. `undo_last_change` takes edits back; in voice the agent asks for a yes first | Editing the plan, safely |
| Live building (Level 5) | `move_item`, `item_history` ("who moved it, where was it yesterday"), `building_changes`. Future deliveries are expected but not findable yet. `POST /buildings/{id}/scan` lets an RFID or barcode reader report "this is now here"; every chat and the map see it | One shared, current state for everyone in the building |
| Room calendar | "Where's the hackathon?" finds the room it's booked in today, then guides there (`data/calendars/`) | Finding events, not just rooms |
| Level 4 by conversation | Upload SVG or PNG plans and a draft building is ready in seconds. The agent asks only about guesses under 90% sure, grouped so one answer covers many rooms ("the ground floor is all production halls"). Routing works on the draft straight away | Any building, from its plans |
| Voice (ElevenLabs) | Live voice; you can interrupt it mid-sentence and it stops. It moves the map | Interaction score counts voice |
| Map | 2D/3D with OpenStreetMap surroundings, see-through stairwells, an x-ray route line that draws in, auto-tilt when the answer is about stacked floors. `highlight_rooms` lights up result sets | The answer is visible, not only told |
| Honesty checks | Output validators reject any number in the `Answer:` line that no tool returned, and any claim of a change that didn't happen. With no route, the agent says which constraint blocks it. Tools never crash | Trustworthy answers |
| Industrial rules | Routes never cross production bays; a forklift needs 3 m doors and no steps; the app passes in where the person is | Factory use, not just offices |
| Extra test sets | 100 hard extra cases (`data/extra_tricky`, Haiku 73 → 98/100), multi-building cases (`data/multi_building`), a Sunday rehearsal script | Evidence it generalises |

## Demo scenes

Ranked by how well they show on screen, all on MUCL.

1. **Visitor, by voice: "Where's the hackathon?"** The calendar finds the room, the 3D route draws in, the
   agent gives landmark directions. Shows voice, the calendar and the map in about 20 seconds.
2. **Facility manager: "The lift is out."** Then a wheelchair user asks for a step-free route: the agent
   reroutes, or explains why no route exists. The "react" part of the brief, and an accessibility story.
3. **Logistics:** a scanner reports a pallet live through `/scan`. Then: "What was delivered yesterday, and
   where is it now?", followed by a forklift route under the 3 m door rule. The use case from Synera's
   kickoff talk.
4. **Fire drill:** "Which room is farthest from an exit?", then "What if we add a door here?" The agent
   shows the improvement, adds the door after a spoken yes, the map updates; undo is there.
5. **Errand:** "I need a printer, coffee and a toilet." `plan_visits` gives the best order and how many
   metres it saves over the naive order.
6. **Plans to a building:** upload a floor plan, answer two questions, route on it right away. Answers
   "does it work on any building?" and shows Level 4.
7. **Split a room:** the agent warns that one half has no door and offers to add one. A small tricky-user
   moment.

### Suggested 3-minute cut

Scene 1, then 2 or 3, then 4 or 5, ending on 6. That covers find, route, react and edit, plus Levels 4 and 5.
