# Test cases and tools

What Synera asks (question kinds in `synera-kit/data`) next to what our agent (`building_guide`) can do.
Snapshot of 25 Oct 2026.

## Questions asked

### Scored set (`questions.jsonl`): the automatic benchmark

Test: 145 questions on 40 buildings. Dev (138) and train (517) have the same mix. Each kind uses one fixed
wording (room_stacked has two: room above / room below) and ends with "Finish with a line 'Answer: …'".

| Cluster | Kind | Test | Share | Example |
|---|---|---|---|---|
| Area | floor_area_total | 33 | 23% | Total floor area of every storage on the ground floor? |
| | largest_room | 21 | 14% | Largest storage on floor 2, and how big in m²? |
| Neighbours and 3D | shared_walls_count | 26 | 18% | How many rooms share a wall (≥1 m) with Bedroom 2.07? Corridors, stairwells, lifts don't count |
| | room_stacked | 9 | 6% | Water dripping from the ceiling in room X: which room is directly above? |
| | straight_line_3d | 15 | 10% | Straight-line distance between the centres of rooms X and Y |
| Routing | errand | 20 | 14% | Get a chair from the nearest room with one, carry it to the largest storage on floor 2: total walk in m? |
| | wide_delivery_count | 18 | 12% | 1.0 m pallet, no stairs, doors ≥1.0 m: into how many rooms on floor 1 can it go? |
| | what_if_door | 3 | 2% | Add a door between X and Y: how many rooms on floor 3 get a walk out shorter by >1 m? |
| | step_free_reachable_count | 0 | – | Lift 1 is out of service; how many rooms on floor 2 can a wheelchair user reach? (1 in dev, 1 in train) |

- About two thirds geometry, one third route graph.
- Building sources in test: generated factories/campuses 66, OpenStreetMap 32, Swiss apartments 30, ResPlan 17.
- Stands out: shared_walls_count is large and rule-heavy; errand chains three steps in one sentence;
  what_if_door asks about a different floor than the door's.

### Warm-up set (`questions_basic.jsonl`): Sunday-style, dev and train only, not in test

Dev: 369 questions. Synera says the Sunday building is "mostly routes", scored by `ROUTE_SCORING.md`.

| Kind | Dev | Example |
|---|---|---|
| route | 120 | How do I get from room X to room Y? |
| locate | 100 | Where is the 3D printer? |
| count | 40 | How many of an item type on a floor? |
| route_step_free | 40 | Is there a step-free route from X to Y? |
| tour | 40 | Start at X, visit Y, then Z in this order |
| route_with_closure | 29 | Stairs 1 is closed. How do I get from X to Y? |

## Our tools

Each returns JSON. `?` = optional.

| Tool | Inputs | Output |
|---|---|---|
| find | query, floor? | Rooms and items matching a name, room number or type |
| room_details | room | Area, type, floor, centre, doors (width, step-free), stairs/lifts, items, rooms sharing a wall (≥1 m), spaces above and below |
| list_rooms | floor, room_type?, scope? | Rooms on a floor, largest first, with areas, count and total area |
| count_items | item_type, floor? | Count of one item type, per floor and room |
| distance_3d | first, second | Straight-line distance between two room centres, m |
| route | start, destination, via?, step_free?, min_width_m? | Reachable or not, total length, per leg: steps, spaces passed, doors/stairs/lifts used; avoids reported closures |
| nearest | start, item_type?, room_type?, step_free? | Closest rooms by walking distance holding an item type or of a room type |
| reachable_rooms | start, floor, step_free?, min_width_m?, closed?, scope? | Rooms on a floor reachable from the start, and how many |
| what_if | floor, add_door_between?, close?, step_free?, scope? | How each room's walk out changes with an added door or closure; nothing is changed |
| calculate | expression | Exact arithmetic on numbers from earlier tool results |
| set_closed | element, closed? | Records a lift, stair, door or room as closed/open; later routes respect it |
| add_door | first, second, width_m?, confirmed? | Proposes a door in a shared wall and its effect; adds it when confirmed |
| move_item | item, to_room, when?, new_item_type? | Records an item moved or a delivery arrived, for everyone on the building |
| item_history | item | Where an item is now, where it was, who moved it, expected arrivals |
| building_changes | since?, until? | Deliveries, moves, closures and new doors in a time window |
| undo_last_change | – | Undoes this conversation's latest change |

## Kind → tool

Which tool answers each kind (as tested without a model in `tests/building/test_agent_tools.py`).

| Kind | Tools |
|---|---|
| largest_room, floor_area_total | list_rooms |
| shared_walls_count, room_stacked | room_details |
| straight_line_3d | distance_3d |
| errand | nearest → list_rooms → route (via) |
| wide_delivery_count | reachable_rooms (step_free, min_width_m) |
| step_free_reachable_count | reachable_rooms (step_free, closed) |
| what_if_door | what_if (add_door_between) |
| locate | find |
| count | count_items |
| route, route_step_free, tour | route (step_free, via) |
| route_with_closure | set_closed → route |

Tools no scored question needs: calculate (helper for derived numbers), add_door, move_item, item_history,
building_changes, undo_last_change. These serve the live building (Level 5) and the demo.

Status (scoreboard, dev): scored kinds 100% through the agent; warm-up 368/369, the miss being a closure
question whose start name matches three rooms (osm-way27917482-Q09).
