"""Merge _staging/<direction>.jsonl into <folder>/questions.jsonl (kit layout) and run every _verify script.

Run: uv run python data/extra_tricky/_merge.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
DIRECTIONS = ["routing_rules", "geometry_traps", "altered_plans", "beyond_tools", "adversarial_language"]
REQUIRED = ["id", "building_id", "building_path", "kind", "question", "answer", "extra"]
EXTRA = ["direction", "solvable", "expected_behavior", "tool_coverage", "trap", "reasoning", "verification"]


def main() -> None:
    cases = []
    for direction in DIRECTIONS:
        path = HERE / "_staging" / f"{direction}.jsonl"
        cases += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    problems = []
    ids = Counter(c["id"] for c in cases)
    problems += [f"duplicate id {i}" for i, n in ids.items() if n > 1]
    for c in cases:
        problems += [f"{c.get('id')}: missing {k}" for k in REQUIRED if k not in c]
        problems += [f"{c.get('id')}: missing extra.{k}" for k in EXTRA if not c.get("extra", {}).get(k)]
        if not (REPO / c["building_path"] / "building.json").exists():
            problems += [f"{c['id']}: no building.json at {c['building_path']}"]
    if problems:
        sys.exit("\n".join(problems))

    by_folder = defaultdict(list)
    for c in cases:
        path = Path(c["building_path"])
        by_folder[path.name if path.parent == Path("data/extra_tricky") else c["building_id"]].append(c)
    for folder, group in sorted(by_folder.items()):
        (HERE / folder).mkdir(exist_ok=True)
        lines = [json.dumps(c, ensure_ascii=False) for c in sorted(group, key=lambda c: c["id"])]
        (HERE / folder / "questions.jsonl").write_text("\n".join(lines) + "\n")

    for direction in DIRECTIONS:
        result = subprocess.run(
            [sys.executable, str(HERE / "_verify" / f"{direction}.py")], capture_output=True, text=True
        )
        last = (result.stdout.strip().splitlines() or ["(no output)"])[-1]
        print(f"verify {direction}: {'PASS' if result.returncode == 0 else 'FAIL'}  {last}")

    print(f"\n{len(cases)} cases in {len(by_folder)} folders")
    for key in ["direction", "expected_behavior", "solvable", "tool_coverage"]:
        print(f"{key}: {dict(Counter(c['extra'][key] for c in cases))}")


if __name__ == "__main__":
    main()
