"""Team scoreboard: Synera's 100 points as measured rows, each with an owner (docs/synera/scoreboard.yaml).

    uv run ia scoreboard                      # free rows re-measured (golden tests); agent rows from the last run
    uv run ia scoreboard --run scenarios      # re-run one suite: benchmark warmup reworded unseen holdout scenarios
    uv run ia scoreboard --run all            # re-run every agent suite on dev (~10 min, a few dollars)

Writes docs/synera/SCOREBOARD.md (commit it: it's how the team sees progress) and appends a line to
evals/scoreboard_history.jsonl. Agent-suite results live in evals/scoreboard.json (committed), so a
teammate's `ia scoreboard` shows your latest agent numbers without paying for a re-run.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import yaml

from app.settings import ROOT, get_settings

CONFIG = ROOT / "docs" / "synera" / "scoreboard.yaml"
STATE = ROOT / "evals" / "scoreboard.json"
HISTORY = ROOT / "evals" / "scoreboard_history.jsonl"
BOARD = ROOT / "docs" / "synera" / "SCOREBOARD.md"
AGENT_SUITES = ("benchmark", "warmup", "reworded", "unseen", "holdout", "scenarios")
# A test skipped with this reason has nothing to check in that building (e.g. no stairs on one floor):
# it is left out of the row's total instead of counting as a miss.
NOT_APPLICABLE = "not applicable:"


@dataclass
class Measured:
    rate: float  # 0..1
    detail: str  # "138/138", "3 of 11 failing: ..."
    failing: str = ""


# ---- measuring --------------------------------------------------------------------------------------------


def run_golden_tests(split: str = "dev") -> dict[str, str]:
    """{test function name -> worst outcome} for tests/building, strict (stubs fail) and on every question."""
    with tempfile.TemporaryDirectory() as tmp:
        xml = os.path.join(tmp, "junit.xml")
        env = os.environ | {"GOLDEN_STRICT": "1", "GOLDEN_SAMPLE": "all", "GOLDEN_SPLIT": split}
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/building",
                "-q",
                "-p",
                "no:cacheprovider",
                f"--junitxml={xml}",
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        cases = ET.parse(xml).getroot().iter("testcase")
        outcomes: dict[str, list[str]] = defaultdict(list)
        for case in cases:
            name = case.get("name", "")
            if case.find("failure") is not None or case.find("error") is not None:
                outcome = "failed"
            elif (skip := case.find("skipped")) is not None:
                message = skip.get("type", "") + skip.get("message", "")
                if NOT_APPLICABLE in message:
                    outcome = "n/a"
                elif "xfail" in message.lower():
                    outcome = "xfail"
                else:
                    outcome = "skipped"
            else:
                outcome = "passed"
            outcomes[name].append(outcome)
    return {name: ",".join(o) for name, o in outcomes.items()}


def measure_pytest(prefixes: list[str], tests: dict[str, str]) -> Measured:
    picked = {
        n: o for n, o in tests.items() if any(n.split("[")[0].startswith(p) for p in prefixes) and o != "n/a"
    }
    if not picked:
        return Measured(0.0, "no tests yet", "write the tests named " + ", ".join(prefixes))
    passed = [n for n, o in picked.items() if o == "passed"]
    bad: dict[str, int] = defaultdict(int)
    for n, o in picked.items():
        if o != "passed":
            bad[f"{n.split('[')[0]} ({o})"] += 1
    failing = ", ".join(f"{k} ×{v}" if v > 1 else k for k, v in sorted(bad.items(), key=lambda t: -t[1])[:4])
    return Measured(len(passed) / len(picked), f"{len(passed)}/{len(picked)} tests", failing)


def measure_bench(suite: str, kinds: list[str] | None, state: dict) -> Measured:
    s = state.get("suites", {}).get(suite)
    if not s:
        return Measured(0.0, "not run yet", f"uv run ia scoreboard --run {suite}")
    by_kind = {k: v for k, v in s["by_kind"].items() if not kinds or k in kinds}
    ok, total = sum(v[0] for v in by_kind.values()), sum(v[1] for v in by_kind.values())
    failing = ", ".join(f"{k} {v[0]}/{v[1]}" for k, v in by_kind.items() if v[0] < v[1])
    return Measured(ok / total if total else 0.0, f"{ok}/{total} questions", failing)


def measure_scenarios(state: dict, prefixes: list[str] | None = None) -> Measured:
    """All scenarios (`scenarios: true`), or only those whose names start with one of `prefixes`."""
    s = state.get("suites", {}).get("scenarios")
    if not s:
        return Measured(0.0, "not run yet", "uv run ia scoreboard --run scenarios")
    if not prefixes:
        return Measured(
            s["correct"] / s["total"], f"{s['correct']}/{s['total']} scenarios", ", ".join(s["failing"])
        )
    picked = {n: ok for n, ok in s.get("results", {}).items() if any(n.startswith(p) for p in prefixes)}
    if not picked:
        return Measured(0.0, "not run yet", "uv run ia scoreboard --run scenarios")
    failing = ", ".join(n for n, ok in picked.items() if not ok)
    return Measured(
        sum(picked.values()) / len(picked), f"{sum(picked.values())}/{len(picked)} scenarios", failing
    )


def measure(row: dict, tests: dict[str, str], state: dict) -> Measured:
    if "pytest" in row and "scenarios" in row:  # both must hold: the code and the agent using it
        parts = [measure_pytest(row["pytest"], tests), measure_scenarios(state, row["scenarios"])]
        return Measured(
            sum(m.rate for m in parts) / len(parts),
            " + ".join(m.detail for m in parts),
            "; ".join(m.failing for m in parts if m.failing),
        )
    if "pytest" in row:
        return measure_pytest(row["pytest"], tests)
    if "bench" in row:
        return measure_bench(row["bench"], row.get("kinds"), state)
    if "scenarios" in row:
        return measure_scenarios(state, row["scenarios"] if isinstance(row["scenarios"], list) else None)
    if "manual" in row:
        return Measured(
            float(row["manual"]), f"{float(row['manual']):.0%} (owner's call)", row.get("note", "")
        )
    return Measured(0.0, "no eval yet", row.get("missing", ""))


# ---- refreshing agent suites -----------------------------------------------------------------------------


def refresh(suites: list[str], model: str | None = None, only: list[str] | None = None) -> None:
    """Re-run agent suites on dev (full) and store their summaries in evals/scoreboard.json. `only` re-runs
    just the scenarios with those names (or name prefixes) and keeps the others' last results."""
    from app.agents.building_guide import bench, scenarios

    state = load_state()
    for suite in suites:
        stamp = {
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "by": who(),
            "model": model or "default",
        }
        if suite == "scenarios":
            names = scenarios.names()
            picked = [n for n in names if any(n.startswith(o) for o in only)] if only else None
            results, _ = scenarios.run_scenarios(only=picked, model=model, label="scoreboard")
            prev = state["suites"].get("scenarios", {}) if only else {}
            # A board from before per-scenario results: the rest passed except the ones listed as failing.
            old = prev.get("results") or {n: n not in prev.get("failing", []) for n in names if prev}
            merged = {n: ok for n, ok in (old | {r["name"]: r["ok"] for r in results}).items() if n in names}
            state["suites"]["scenarios"] = stamp | {
                "correct": sum(merged.values()),
                "total": len(merged),
                "failing": [n for n, ok in merged.items() if not ok],
                "results": merged,
            }
        else:
            results, _ = bench.run_suite("dev", suite, model=model, label=f"scoreboard-{suite}")
            state["suites"][suite] = stamp | bench.summary(results)
    STATE.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def who() -> str:
    """DEV_NAME from .env, else git's user.name."""
    if name := get_settings().dev_name:
        return name
    out = subprocess.run(
        ["git", "config", "user.name"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return out.stdout.strip() or "someone"


def load_state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"suites": {}}


# ---- the board ------------------------------------------------------------------------------------------


def build(split: str = "dev") -> dict[str, Any]:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    people: dict[str, str] = config["people"]
    tests, state = run_golden_tests(split), load_state()
    rows, per_person = [], dict.fromkeys(people.values(), 0.0)
    for row in config["rows"]:
        m = measure(row, tests, state)
        points = row["stake"] * m.rate
        owners = [people[o] for o in row["owners"]]
        for o in owners:
            per_person[o] += points / len(owners)
        rows.append(
            row
            | {"owners": owners, "rate": m.rate, "detail": m.detail, "failing": m.failing, "points": points}
        )
    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "by": who(),
        "rows": rows,
        "per_person": per_person,
        "team": sum(r["points"] for r in rows),
        "suites": {
            k: {f: v.get(f) for f in ("at", "by", "model")} for k, v in state.get("suites", {}).items()
        },
    }


def _bar(rate: float, width: int = 10) -> str:
    full = round(rate * width)
    return "█" * full + "░" * (width - full)


def render_markdown(board: dict[str, Any]) -> str:
    out = [
        "# Team scoreboard",
        "",
        f"Generated {board['at']} by {board['by']} with `uv run ia scoreboard`. Don't edit by hand: change "
        "`scoreboard.yaml` (rows, stakes, manual status) or make a row's eval pass.",
        "",
        f"**Team: {board['team']:.1f} / 100 estimated points.** Stakes are our guess at Synera's split.",
        "",
        "| Person | Points |",
        "|---|---|",
        *[f"| {p} | {v:.1f} |" for p, v in sorted(board["per_person"].items(), key=lambda t: -t[1])],
        "",
        "| Part | Row | Owners | Measured | Points | Failing / next |",
        "|---|---|---|---|---|---|",
    ]
    for r in board["rows"]:
        points = f"{r['points']:.1f} / {r['stake']}" if r["stake"] else "health"
        failing = r["failing"].replace("|", "/") or "–"
        out.append(
            f"| {r['part']} | **{r['id']}**: {r['what']} | {', '.join(r['owners'])} | "
            f"{_bar(r['rate'])} {r['rate']:.0%} ({r['detail']}) | {points} | {failing} |"
        )
    if board["suites"]:
        out += [
            "",
            "Agent suites last run: "
            + "; ".join(f"{k} {v['at']} by {v['by']} ({v['model']})" for k, v in board["suites"].items()),
        ]
    out += [
        "",
        "How a row moves: write or fix the failing eval first (red), then the code (green), then",
        "`uv run ia scoreboard` and commit SCOREBOARD.md with your change.",
        "",
    ]
    return "\n".join(out)


def save(board: dict[str, Any]) -> None:
    BOARD.write_text(render_markdown(board), encoding="utf-8")
    line = {
        "at": board["at"],
        "by": board["by"],
        "team": round(board["team"], 2),
        "per_person": {k: round(v, 2) for k, v in board["per_person"].items()},
        "rows": {r["id"]: round(r["rate"], 4) for r in board["rows"]},
    }
    with HISTORY.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def previous() -> dict | None:
    if not HISTORY.exists():
        return None
    lines = [line for line in HISTORY.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else None


def short(text: str, n: int = 70) -> str:
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= n else text[: n - 1] + "…"
