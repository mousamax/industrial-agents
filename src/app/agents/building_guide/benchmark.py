"""Adapter for the kit's official evaluator and scorer (synera-kit/benchmark).

From synera-kit/:
    uv run python -m benchmark.evaluate data/dev --per-kind 1 --agent app.agents.building_guide.benchmark:answer
    uv run python -m benchmark.evaluate data/test --agent app.agents.building_guide.benchmark:answer \
        --submission-csv team_<name>.csv
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app import observability

from .agent import card


def answer(building_directory: Path, question_text: str) -> str:
    observability.setup()
    with observability.entrypoint("building_guide", kind="eval", tags=["synera-benchmark"]):
        return asyncio.run(card.run({"building": str(building_directory), "question": question_text}))
