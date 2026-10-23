"""Finds every agent and flow by convention, so nobody edits a shared list (no merge conflicts).

Convention: `src/app/agents/<name>/__init__.py` (or `src/app/flows/<name>/__init__.py`) exposes `card`.
Folders starting with `_` (like `_template`) are skipped.
"""

from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from app import agents, flows
from app.core import Card


@dataclass(frozen=True)
class Entry:
    card: Card
    kind: str  # "agent" | "flow"
    path: Path

    @property
    def evals_path(self) -> Path:
        return self.path / "evals.yaml"


@cache
def registry() -> dict[str, Entry]:
    found: dict[str, Entry] = {}
    for package, kind in ((agents, "agent"), (flows, "flow")):
        for mod in pkgutil.iter_modules(package.__path__):
            if not mod.ispkg or mod.name.startswith("_"):
                continue
            module = importlib.import_module(f"{package.__name__}.{mod.name}")
            card = getattr(module, "card", None)
            if card is None:
                raise RuntimeError(f"{module.__name__} must expose `card` (see agents/_template)")
            if card.name in found:
                raise RuntimeError(f"duplicate agent/flow name {card.name!r}")
            found[card.name] = Entry(card=card, kind=kind, path=Path(module.__file__).parent)  # type: ignore[arg-type]
    return found


def get(name: str) -> Entry:
    try:
        return registry()[name]
    except KeyError:
        raise KeyError(f"unknown agent/flow {name!r}; known: {', '.join(sorted(registry()))}") from None
