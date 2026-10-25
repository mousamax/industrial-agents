"""Open team to-dos as scoreboard rows: each test here is a reminder, not a check.

A strict xfail keeps `uv run pytest -q` green but counts as not passed on the scoreboard (row
`todo-open-questions`), so the reminder stays red until someone resolves it and deletes the test.
Only delete one when the person who asked for it says it's done.
"""

from __future__ import annotations


def test_todo_none_open():
    """Keeps the row green while no reminder is open; add new reminders as strict xfails next to it."""
