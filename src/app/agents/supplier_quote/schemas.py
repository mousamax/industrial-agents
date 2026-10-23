from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class QuoteInput(BaseModel):
    document: str = Field(description="Raw text of a supplier quote: email body, PDF text, ...")


class LineItem(BaseModel):
    part_number: str
    description: str
    quantity: int
    unit_price: float = Field(description="In the quote's currency")
    lead_time_days: int | None = Field(None, description="Weeks are converted to days (1 week = 7 days)")


class Flag(BaseModel):
    severity: Literal["info", "warning", "blocker"]
    message: str
    part_number: str | None = None


class QuoteAnalysis(BaseModel):
    supplier: str
    currency: str = Field(description="ISO 4217 code, e.g. EUR, USD")
    line_items: list[LineItem]
    total: float = Field(description="Total as stated on the quote")
    valid_until: date | None = None
    incoterms: str | None = Field(None, description="e.g. FCA, EXW, DDP (code only)")
    flags: list[Flag] = Field(description="Every deviation from catalog targets or policy")
    recommendation: Literal["accept", "negotiate", "reject"]
    rationale: str = Field(description="Two or three sentences a buyer can act on")
