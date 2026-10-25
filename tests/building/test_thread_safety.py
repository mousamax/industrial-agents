"""Tools run in worker threads, many conversations at once, so the walk geometry they share must be
thread-safe. directions.py caches shapely prepared geometries, which GEOS doesn't guard: without the lock
in directions.py this script segfaulted (exit 138/139) on every run. It runs in a subprocess so a crash
fails this test instead of killing pytest."""

from __future__ import annotations

import os
import subprocess
import sys

SCRIPT = r"""
import itertools, json, random, threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from app.agents.building_guide.tools import Deps, never_crash, route, walk_directions
from app.building import directions as D
from app.building.model import load_building

BID = "proc-factory-dev-003"
b = load_building(BID)

# 1. The race itself: 16 threads at once on one freshly prepared room (the most corners, obstacles cut out).
rooms = []
for sid, s in b.spaces.items():
    obstacles = tuple(
        tuple(map(tuple, D._footprint(a, D.OBSTACLE_CLEARANCE_M).exterior.coords))
        for a in b.assets_in(sid) if D._is_obstacle(a)
    )
    free = D._free.__wrapped__(s.polygon, obstacles)
    if free is not None:
        rooms.append((len(free.corners), s.polygon, obstacles, free))
_, outline, obstacles, sample = max(rooms, key=lambda r: r[0])
rng = random.Random(0)
x0, y0, x1, y1 = sample.area.bounds
points = [D._snap(sample, (rng.uniform(x0, x1), rng.uniform(y0, y1))) for _ in range(64)]
for _ in range(100):
    free = D._free.__wrapped__(outline, obstacles)
    barrier = threading.Barrier(16)

    def walk(k, free=free, barrier=barrier):
        barrier.wait()
        for i in range(4):
            D._shortest(free, points[(4 * k + i) % 64], points[(4 * k + i + 7) % 64])

    threads = [threading.Thread(target=walk, args=(k,)) for k in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

# 2. The agent's tools as the agent registers them (never_crash, no global lock), concurrently, cold caches.
safe_route, safe_walk = never_crash(route), never_crash(walk_directions)
names = sorted({s.name for s in b.spaces.values() if s.name})[:30]
pairs = random.Random(1).sample(list(itertools.permutations(names, 2)), 40)

def ask(pair):
    c = SimpleNamespace(deps=Deps.load(BID), run_id="x", last_attempt=True)
    json.loads(safe_route(c, *pair))
    json.loads(safe_walk(c, *pair))

for _ in range(3):
    D._free.cache_clear()
    D._walk.cache_clear()
    with ThreadPoolExecutor(16) as pool:
        list(pool.map(ask, pairs * 2))
print("ok")
"""


def test_walks_and_route_tools_survive_concurrent_threads():
    done = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYDANTIC_AI_NO_BANNER": "1"},
    )
    assert done.returncode == 0, f"exit {done.returncode}\n{done.stderr[-2000:]}"
    assert done.stdout.strip().endswith("ok")
