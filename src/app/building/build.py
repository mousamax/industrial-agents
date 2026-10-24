"""Level 4: create or update a building from floor plans, by conversation. "Minutes, not days."

OWNER: Owner B, geometry & build. Scored automatically ("correct buildings built from plans"), but the
input and the comparison aren't published yet: ask Synera (docs/synera/README.md, question 2) before
going deep. Until then, target: plan images (floor_<level>_plain.png) -> building.json in the kit format
that loads with app.building.model.parse_building and passes `validate` with no errors.

Train has 200 buildings with both the image and the true building.json: that's the test set for this.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class Issue:
    severity: Literal["error", "warning"]
    element_id: str | None
    message: str


def validate(raw: dict[str, Any]) -> list[Issue]:
    """Schema and topology checks for a kit-format building.json: required fields, ids unique, every
    opening joins 2 existing spaces on its floor, connectors stop once per floor, every space reachable
    from outside, door positions on a shared wall."""
    raise NotImplementedError("Owner B")


def draft_from_plans(
    images: list[Path], *, name: str, levels: list[int] | None = None, metres_per_pixel: float | None = None
) -> dict[str, Any]:
    """Floor-plan images -> kit-format building.json (Claude vision + geometry clean-up). One image per
    floor, `levels` in the same order (default 0, 1, 2, ...)."""
    raise NotImplementedError("Owner B")


def compare(predicted: dict[str, Any], truth: dict[str, Any]) -> dict[str, float]:
    """How close a drafted building is to the true one (room count, area IoU, door/adjacency recall):
    our own score for Level 4 until Synera tells us theirs."""
    raise NotImplementedError("Owner B")
