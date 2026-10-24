"""How many routes would Synera score 0? Runs routing.validate_route on dev or train.

Three sets, all free (no API calls):
- golden:   Synera's published space_path for every warm-up route (route, step-free, closure, tour legs).
            Should be 0 invalid apart from production bays (the kit's model has no bay rule).
- ours:     RouteGraph.shortest for the same questions.
- sweep:    RouteGraph from outside to every reachable space, walking and forklift, in every building.
            This is where unseen-question problems show (bays crossed, narrow doors).

    uv run python scripts/synera/validate_routes.py dev            # or train
    uv run python scripts/synera/validate_routes.py dev --show 10  # print the first invalid routes
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from app.building.model import OUTSIDE, load_building
from app.building.routing import FORKLIFT, WALKING, Constraints, RouteGraph, validate_route
from app.settings import get_settings

KINDS = {"route": WALKING, "route_step_free": Constraints(step_free=True), "route_with_closure": WALKING}


def legs(q):
    """(from, to, golden space_path or None, constraints) for every leg of a warm-up route question."""
    a = q["answer"]
    if q["kind"] == "tour":
        return [(leg["from_space_id"], leg["to_space_id"], leg["space_path"], WALKING) for leg in a["legs"]]
    c = KINDS[q["kind"]]
    if "closed_element_id" in a:
        c = c.with_closed(a["closed_element_id"])
    return [(a["from_space_id"], a["to_space_id"], a.get("space_path"), c)]


def reason(problem: str) -> str:
    return re.sub(r" [A-Z].*| (unknown|closed) space .*", "", problem).split(" ->")[0]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("split", choices=["dev", "train"])
    p.add_argument("--show", type=int, default=0, help="print this many invalid routes per set")
    args = p.parse_args()
    data = get_settings().synera_kit_dir / "data" / args.split

    totals = {name: Counter() for name in ("golden", "ours", "sweep walking", "sweep forklift")}
    reasons = {name: Counter() for name in totals}
    shown = Counter()

    def check(name, b, path, c, label):
        problems = validate_route(b, path, c)
        totals[name]["routes"] += 1
        if problems:
            totals[name]["invalid"] += 1
            reasons[name].update({reason(x) for x in problems})
            if shown[name] < args.show:
                shown[name] += 1
                print(f"[{name}] {label}: {'; '.join(problems)}")

    for qf in sorted(Path(data).glob("*/questions_basic.jsonl")):
        b = load_building(qf.parent.name)
        graphs: dict[Constraints, RouteGraph] = {}
        for line in qf.read_text(encoding="utf-8").splitlines():
            q = json.loads(line)
            if q["kind"] not in {*KINDS, "tour"}:
                continue
            for start, goal, golden, c in legs(q):
                if golden:
                    check("golden", b, golden, c, q["id"])
                graph = graphs.setdefault(c, RouteGraph(b, c))
                route = graph.shortest(start, goal)
                if route is not None:
                    check("ours", b, route.space_path, c, q["id"])
        for name, c in (("sweep walking", WALKING), ("sweep forklift", FORKLIFT)):
            graph = RouteGraph(b, c)
            for target in sorted(graph.reachable(OUTSIDE)):
                route = graph.shortest(OUTSIDE, target)
                check(name, b, route.space_path, c, f"{b.id} outside -> {b.label(target)}")

    print(f"\n{args.split}: invalid routes under Synera's rule (validate_route)")
    for name, t in totals.items():
        why = ", ".join(f"{r} {n}" for r, n in reasons[name].most_common()) or "-"
        print(f"  {name:15} {t['invalid']:5} / {t['routes']:<6}  {why}")


if __name__ == "__main__":
    main()
