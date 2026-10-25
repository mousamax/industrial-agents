"""Sunday rehearsal: run the real Sunday command on a kit building whose answers we know, and score it.

    uv run python scripts/synera/sunday_rehearsal.py --limit 3          # mock run: 3 questions, a few cents
    uv run python scripts/synera/sunday_rehearsal.py                    # every question of the building
    uv run python scripts/synera/sunday_rehearsal.py --building proc-factory-dev-003 --suite warmup

What it does, as close to Sunday as we can get:
1. Copies the building into a fresh folder without its question files (the agent can't see answers) and
   writes Synera-style questions.json: {"id", "text", "constraints"?}.
2. Runs exactly the command from BENCHMARK.md, with --check and --csv, as a subprocess, and times it.
3. Scores answers.json against the golden answers: the reply with the judges' scorer (benchmark.score), and
   the structured fields (item and room ids, counts, route ends, length within 1%).

Uses the model (costs money); everything else is our code. Writes to evals/reports/sunday-rehearsal/<building>/.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from itertools import zip_longest
from typing import Annotated, Any

import typer

from app.agents.building_guide.bench import score, warmup_question
from app.building.model import building_dir
from app.settings import ROOT

WARMUP_ORDER = ["route", "locate", "route_with_closure", "count", "tour", "route_step_free"]
"""Kinds in the order a short mock run takes them: the first few cover the most ground."""
SUITES = {"warmup": "questions_basic.jsonl", "benchmark": "questions.jsonl"}


def golden(building: str, suite: str) -> list[dict[str, Any]]:
    files = list(SUITES.values()) if suite == "all" else [SUITES[suite]]
    out = []
    for name in files:
        path = building_dir(building) / name
        if path.exists():
            out += [
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
    return out


def pick(questions: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    """Every question, or `limit` of them spread over the kinds (round robin, WARMUP_ORDER first)."""
    if not limit:
        return questions
    by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for q in questions:
        by_kind[q["kind"]].append(q)
    kinds = [k for k in WARMUP_ORDER if k in by_kind] + sorted(k for k in by_kind if k not in WARMUP_ORDER)
    spread = [q for row in zip_longest(*(by_kind[k] for k in kinds)) for q in row if q]
    return spread[:limit]


def as_sunday(q: dict[str, Any]) -> dict[str, Any]:
    """The question as Synera would send it: id, text, and step-free as a constraint."""
    return {"id": q["id"], "text": q["question"]} | (
        {"constraints": {"step_free": True}} if q["kind"] == "route_step_free" else {}
    )


def fields_ok(q: dict[str, Any], r: dict[str, Any]) -> bool | None:
    """Do the structured fields match the golden answer? None for kinds that only have a reply."""
    a, kind = q["answer"], q["kind"]
    if kind == "locate":
        return (r.get("item_id"), r.get("space_id")) == (a["asset_id"], a["space_id"])
    if kind == "count":
        return r.get("count") == a["count"]
    if kind not in WARMUP_ORDER:
        return None
    if a.get("reachable") is False:
        return r.get("reachable") is False
    if not r.get("reachable") or not r.get("route") or r.get("length_m") is None:
        return False
    ends = (a["stops"][0], a["stops"][-1]) if kind == "tour" else (a["from_space_id"], a["to_space_id"])
    expected = a["optimal_length_m"]
    return (r["route"][0], r["route"][-1]) == ends and abs(r["length_m"] - expected) <= max(
        0.01 * expected, 0.05
    )


def main(
    building: Annotated[str, typer.Option(help="Kit building id with known answers")] = "osm-way22744230",
    suite: Annotated[
        str, typer.Option(help="warmup (find and route), benchmark (plan facts) or all")
    ] = "all",
    limit: Annotated[
        int | None, typer.Option(help="Mock run: only this many questions, spread over kinds")
    ] = None,
    concurrency: Annotated[int, typer.Option(help="Questions in flight")] = 8,
) -> None:
    if suite not in (*SUITES, "all"):
        raise typer.BadParameter("suite must be warmup, benchmark or all")
    questions = pick(golden(building, suite), limit)
    if not questions:
        raise typer.BadParameter(f"no {suite} questions for {building}")
    out = ROOT / "evals" / "reports" / "sunday-rehearsal" / building
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(building_dir(building), out / "building", ignore=shutil.ignore_patterns("questions*"))
    (out / "questions.json").write_text(
        json.dumps([as_sunday(q) for q in questions], indent=2), encoding="utf-8"
    )

    command = [
        sys.executable, "-m", "app.agents.building_guide.sunday",
        str(out / "building"), str(out / "questions.json"), str(out / "answers.json"),
        "--check", "--csv", str(out / "answers.csv"), "--concurrency", str(concurrency),
    ]  # fmt: skip
    typer.echo(f"{len(questions)} questions on {building} -> {out.relative_to(ROOT)}")
    started = time.monotonic()
    run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    seconds = time.monotonic() - started
    typer.echo(run.stdout.rstrip())
    if not (out / "answers.json").exists():
        typer.echo(run.stderr[-3000:])
        raise typer.Exit(2)

    records = {r["id"]: r for r in json.loads((out / "answers.json").read_text(encoding="utf-8"))}
    rows: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # reply ok, fields ok, fields checked, n
    misses = []
    for q in questions:
        r = records.get(q["id"], {})
        answer = warmup_question(q)["answer"] if q["kind"] in WARMUP_ORDER else q["answer"]
        reply = score(answer, r.get("reply") or "")
        fields = fields_ok(q, r)
        row = rows[q["kind"]]
        row[0] += reply
        row[1] += bool(fields)
        row[2] += fields is not None
        row[3] += 1
        if not reply or fields is False:
            misses.append(f"  {q['id']} ({q['kind']}): reply {'ok' if reply else 'WRONG'}, fields "
                          f"{'-' if fields is None else 'ok' if fields else 'WRONG'}; answer: {r.get('answer')!r}")  # fmt: skip

    typer.echo(f"\n{'kind':<28}{'reply':>8}{'fields':>9}")
    for kind, (reply, fields, checked, n) in rows.items():
        typer.echo(f"{kind:<28}{f'{reply}/{n}':>8}{(f'{fields}/{checked}' if checked else '-'):>9}")
    total_reply = sum(v[0] for v in rows.values())
    total_fields, total_checked = sum(v[1] for v in rows.values()), sum(v[2] for v in rows.values())
    typer.echo(f"{'TOTAL':<28}{f'{total_reply}/{len(questions)}':>8}{f'{total_fields}/{total_checked}':>9}")
    typer.echo(
        f"time: {seconds:.0f} s ({seconds / len(questions):.1f} s per question, concurrency {concurrency})"
    )
    typer.echo(f"--check: {'passed' if run.returncode == 0 else 'found problems (listed above)'}")
    if misses:
        typer.echo("misses:\n" + "\n".join(misses))
    summary = {
        "building": building,
        "questions": len(questions),
        "reply_correct": total_reply,
        "fields_correct": total_fields,
        "fields_checked": total_checked,
        "check_passed": run.returncode == 0,
        "seconds": round(seconds, 1),
        "by_kind": {k: {"reply": v[0], "fields": v[1], "checked": v[2], "n": v[3]} for k, v in rows.items()},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    typer.run(main)
