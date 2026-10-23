"""One way to turn outputs into plain JSON data, so traces, judges and evaluators all see the same shape."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


def _default(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return str(value)  # dates, paths, enums...


def to_jsonable(value: Any) -> Any:
    """Pydantic models (also nested in dicts/lists) become dicts; anything else non-JSON becomes a string."""
    return json.loads(json.dumps(value, default=_default))
