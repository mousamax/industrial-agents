from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from app.settings import get_settings


class CatalogEntry(BaseModel):
    description: str
    target_price_eur: float
    max_lead_time_days: int
    approved_suppliers: list[str]


@dataclass
class Deps:
    catalog: dict[str, CatalogEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> Deps:
        path = path or get_settings().data_dir / "sample" / "catalog.json"
        raw = json.loads(path.read_text())
        return cls(catalog={k: CatalogEntry.model_validate(v) for k, v in raw.items()})
