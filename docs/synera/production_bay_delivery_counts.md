# Delivery counts that cross production bays (kit 1.1.0)

The route rule from Discord (Sat 24 Oct) says **production bays are destinations: a route may end in one,
but never pass through one.** Ten expected answers to `wide_delivery_count` questions in the 1.1.0 kit
don't follow that rule. Every room they count can only be reached by crossing at least one production bay.
Under the announced rule, each of these answers would be **0**.

We'd like to know which version is correct before the benchmark runs. The 18 `wide_delivery_count`
questions in the test set probably include more cases like these.

## Affected questions

Every question below asks: "It can't go on stairs or steps (lifts are fine), and every door it passes
through must be at least *W* m wide. It arrives through the entrance into *Start*. Into how many rooms on
*Floor* can it be delivered?"

| Question | Start | Floor | W | Expected | Bay rule applied | Bays crossed |
|---|---|---|---|---|---|---|
| `proc-factory-dev-003-H03` | Warehouse zone H-C3 | Floor 1 | 1.1 | 7 | 0 | Production zone H-A1, Assembly zone H-A2 |
| `proc-factory-dev-004-H02` | Logistics zone H-C2 | Ground floor | 1.1 | 7 | 0 | Assembly zone H-A1, Assembly zone H-A3 |
| `proc-factory-dev-007-H03` | Main aisle H-B3 | Ground floor | 1.1 | 8 | 0 | Production zone H-A3 |
| `proc-factory-dev-008-H03` | Logistics zone H-D2 | Ground floor | 1.0 | 8 | 0 | Production zone H-A1, H-B1, H-B4, Assembly zone H-A4, Additive zone H-B3 |
| `proc-factory-dev-010-H03` | Logistics zone H-C1 | Ground floor | 1.1 | 3 | 0 | Production zone H-A1, H-A3, Assembly zone H-A2 |
| `proc-factory-dev-011-H03` | Logistics zone H-D5 | Ground floor | 1.1 | 13 | 0 | Production zone H-A1, H-B2, H-B3, H-B5, Assembly zone H-A2, H-A5 |
| `proc-factory-dev-012-H03` | Logistics zone H-D4 | Floor 1 | 1.0 | 10 | 0 | Production zone H-B1, Assembly zone H-B2 |
| `proc-factory-train-005-H03` | Warehouse zone H-C2 | Floor 1 | 1.0 | 6 | 0 | Production zone H-A1 |
| `proc-factory-train-008-H02` | Main aisle H-B3 | Ground floor | 1.1 | 3 | 0 | Production zone H-A2, Assembly zone H-A1, Additive zone H-A3 |
| `proc-factory-train-009-H03` | Main aisle H-B4 | Floor 1 | 1.1 | 6 | 0 | Production zone H-A1, H-A3, Additive zone H-A2 |

"Bays crossed" lists every bay passed through on the shortest way to at least one counted room. All the
counted rooms are in the office wing (meeting rooms, server rooms, toilets, quality labs).

Example paths (shortest under the 1.1.0 distance rule, with bays allowed):

- `proc-factory-dev-007-H03`, to Server room 0.14: Main aisle H-B3 → **Production zone H-A3** → Hall access 0.20 → Office corridor 0.10 → Server room 0.14
- `proc-factory-train-005-H03`, to Toilet 1.08: Warehouse zone H-C2 → Main aisle H-B2 → Main aisle H-B1 → **Production zone H-A1** → Hall access 0.16 → Office corridor 0.13 → Lift 0.15 → Lift 1.03 → Office corridor 1.01 → Toilet 1.08
- `proc-factory-dev-011-H03`, to Server room 0.26: Logistics zone H-D5 → Main aisle H-C5 → H-C4 → H-C3 → **Production zone H-B3** → **Production zone H-B2** → **Assembly zone H-A2** → **Production zone H-A1** → Hall access 0.24 → Office corridor 0.21 → Server room 0.26

## Why the width limit doesn't explain it

The door width isn't the cause. If we drop the width limit completely and only forbid crossing bays, all
ten questions still come out at **0** rooms.

The cause is the factory layout. The office wing's "Hall access" space opens only onto production bays.
There is no door from a main aisle, warehouse zone or logistics zone into it. Across all 26 `proc-factory-*`
buildings in train and dev, **20 have offices that can only be reached from the hall through a production
bay**, even on foot. So under the announced rule, nobody can walk from the hall to the offices in most
factories. That also affects route questions about those buildings in the hidden test set.

The expected counts look like plain reachability (width and step-free filters, no bay rule). The 70 other
`wide_delivery_count` answers give the same number with or without the bay rule, so these ten are the only
ones where it matters.

## Our assumptions

- We treat `production_zone`, `assembly_zone` and `additive_zone` as production bays, and **not**
  `warehouse_zone` or `logistics_zone`. If warehouse or logistics zones also count as bays, more answers
  are affected. Some routes cross a warehouse zone, for example `proc-factory-dev-012` via Warehouse zone H-A1.
- Doors must be at least *W* m wide, there are no stairs, lifts are allowed, and closed spaces are excluded,
  as the questions say.

## What we'd like to know

1. **Delivery and reachability counts:** do they follow the bay rule, which would make these ten answers 0,
   or is the bay rule only for route answers, so the current numbers stand?
2. **Routes from the hall to the office wing:** if the bay rule applies, is "no route" the expected answer
   for a walking route from a main aisle to an office in these factories? Or is a bay that's the only way
   through allowed?
3. **Bay types:** which `room_type` values count as production bays? Do `warehouse_zone` and
   `logistics_zone` count?
