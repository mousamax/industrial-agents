"""Adapter for the kit's official evaluator and scorer (synera-kit/benchmark).

From synera-kit/:
    uv run --project .. python -m benchmark.evaluate data/dev --per-kind 1 \
        --agent app.agents.building_guide.benchmark:answer
    uv run --project .. python -m benchmark.evaluate data/test --agent app.agents.building_guide.benchmark:answer \
        --submission-csv team_<name>.csv

The faster parallel equivalent for iterating: `python -m app.agents.building_guide.bench` (bench.py).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app import observability

from .agent import agent
from .tools import Deps


def answer(building_directory: Path, question_text: str) -> str:
    observability.setup()
    with observability.entrypoint("building_guide", kind="eval", tags=["synera-benchmark"]):
        result = asyncio.run(agent.run(question_text, deps=Deps.load(building_directory)))
    return result.output
