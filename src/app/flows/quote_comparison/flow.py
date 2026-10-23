"""quote_comparison: several quotes in, one decision and a negotiation email out.

The flow pattern we use: Python owns the control flow, agents own the judgment.
1. Fan out: analyse every quote in parallel with the supplier_quote agent.
2. Decide in code: rank deterministically (cheap, testable, explainable).
3. Fan in: a writer agent drafts the email to the chosen supplier.

The flow is one `chain` node in Langfuse with every agent run nested under it.
"""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.capabilities import Thinking

from app.agents.supplier_quote import card as quote_card
from app.agents.supplier_quote.schemas import QuoteAnalysis
from app.core import FlowCard, load_prompt, to_prompt
from app.models import model


class ComparisonInput(BaseModel):
    quotes: list[str] = Field(min_length=1, description="Raw text of each supplier quote")


class Decision(BaseModel):
    chosen_supplier: str | None
    ranking: list[str] = Field(description="Suppliers, best first")
    analyses: list[QuoteAnalysis]
    email: str = Field(description="Draft email to the chosen supplier; empty if every quote is rejected")


writer = Agent(
    model("balanced"),
    name="negotiation_writer",
    instructions=load_prompt(__file__, "writer_prompt.md"),
    capabilities=[Thinking("low")],
    defer_model_check=True,
)

_RANK = {"accept": 0, "negotiate": 1, "reject": 2}
_SEVERITY = {"blocker": 100, "warning": 10, "info": 1}


def rank(analyses: list[QuoteAnalysis]) -> list[QuoteAnalysis]:
    """Best first: recommendation, then fewest/least severe flags."""
    return sorted(
        analyses, key=lambda a: (_RANK[a.recommendation], sum(_SEVERITY[f.severity] for f in a.flags))
    )


async def compare_quotes(inputs: ComparisonInput, *, model: str | None = None) -> Decision:
    analyses = await asyncio.gather(*(quote_card.run({"document": q}, model=model) for q in inputs.quotes))

    ranked = rank(list(analyses))
    best = ranked[0] if ranked[0].recommendation != "reject" else None
    email = ""
    if best is not None:
        result = await writer.run(to_prompt({"chosen": best.model_dump(mode="json")}), model=model)
        email = result.output

    return Decision(
        chosen_supplier=best.supplier if best else None,
        ranking=[a.supplier for a in ranked],
        analyses=list(analyses),
        email=email,
    )


card = FlowCard(
    name="quote_comparison",
    description="Compares several supplier quotes, picks one, drafts the negotiation email.",
    fn=compare_quotes,
    input_type=ComparisonInput,
)
