"""Print notes/todo.md with each item's real status at the end of a test run.

Items are lines like "- [ ] **1.4** text · tests: `test_x`, `test_y`". An item is done when all its tests
ran and passed (an xfail that passes counts). Items without tests are shown as manual.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

TODO = Path(__file__).resolve().parents[2] / "notes" / "todo.md"
ITEM = re.compile(r"^\s*- \[[ xX]\] \*\*(?P<id>[\d.]+)\*\* (?P<text>.+)$")
TEST = re.compile(r"`(test_\w+)`")

_outcomes: dict[str, Counter[str]] = defaultdict(Counter)


def _items() -> list[tuple[str, str, list[str]]]:
    if not TODO.exists():
        return []
    items = []
    for line in TODO.read_text(encoding="utf-8").splitlines():
        if m := ITEM.match(line):
            text, _, tests = m["text"].partition(" · tests:")
            title = re.sub(r"[`*]", "", text.split(": ")[0]).strip()
            items.append((m["id"], title, TEST.findall(tests)))
    return items


def pytest_runtest_logreport(report):
    name = report.nodeid.split("::")[-1].split("[")[0]
    if report.when == "call" or (report.when == "setup" and not report.passed):
        if hasattr(report, "wasxfail"):
            _outcomes[name]["passed" if report.passed else "xfailed"] += 1
        else:
            _outcomes[name][report.outcome] += 1


def pytest_terminal_summary(terminalreporter):
    items = _items()
    if not any(name in _outcomes for _, _, tests in items for name in tests):
        return
    tr = terminalreporter
    tr.write_sep("=", f"to-do progress ({TODO.relative_to(TODO.parents[1])})")
    done = with_tests = 0
    for item_id, text, tests in items:
        if not tests:
            tr.write_line(f"[ ] {item_id:<4} {text[:60]:<60}  (manual)")
            continue
        counts = sum((_outcomes.get(t, Counter()) for t in tests), Counter())
        ran = sum(counts.values())
        ok = ran > 0 and counts["passed"] == ran
        with_tests += 1
        done += ok
        detail = ", ".join(f"{n} {k}" for k, n in counts.items() if n) or "not run"
        tr.write_line(
            f"[{'x' if ok else ' '}] {item_id:<4} {text[:60]:<60}  ({detail})",
            green=ok,
            red=counts["failed"] > 0,
        )
    tr.write_line(f"{done}/{with_tests} items with tests done")
