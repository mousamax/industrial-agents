"""Open team to-dos as scoreboard rows: each test here is a reminder, not a check.

A strict xfail keeps `uv run pytest -q` green but counts as not passed on the scoreboard (row
`todo-open-questions`), so the reminder stays red until someone resolves it and deletes the test.
Only delete one when the person who asked for it says it's done.
"""

from __future__ import annotations

import pytest


@pytest.mark.xfail(strict=True, reason="reminder: Magnus asks Mousa, before code freeze")
def test_todo_ask_mousa_closure_miss_duplicate_room_names():
    """route_with_closure 28/29 on dev: osm-way27917482-Q09. Three spaces are named "Generic room
    03.08.033" on Floor 3 (F3-S015/S016/S017); the agent picked S015 (125.35 m, the shortest) and listed
    the others, the golden answer starts at S016 (127.22 m). Ask Mousa how the agent should pick between
    rooms that share a name (ask back? pick the shortest? the first by id?) and whether it's worth chasing.
    Delete this test once Magnus says it's settled."""
    pytest.fail("open: ask Mousa about rooms that share a name (osm-way27917482-Q09)")
