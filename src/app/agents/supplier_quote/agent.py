"""supplier_quote: turn a messy supplier quote into a structured, checked analysis.

Shows the patterns we want everywhere:
- structured output as the contract (QuoteAnalysis),
- tools for facts (catalog lookup) instead of trusting the model's memory,
- an output validator that makes the model fix its own arithmetic (ModelRetry),
- the prompt in prompt.md, the eval cases in evals.yaml.
"""

from __future__ import annotations

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.capabilities import Caching, Thinking

from app.core import AgentCard, load_prompt
from app.models import model

from .schemas import QuoteAnalysis, QuoteInput
from .tools import CatalogEntry, Deps

agent = Agent(
    model("smart"),
    name="supplier_quote",
    instructions=load_prompt(__file__),
    deps_type=Deps,
    output_type=QuoteAnalysis,
    capabilities=[Thinking("low"), Caching()],
    defer_model_check=True,
)


@agent.tool
async def lookup_part(ctx: RunContext[Deps], part_number: str) -> CatalogEntry | str:
    """Look up our catalog entry for a part: target price (EUR), max lead time, approved suppliers.

    Call it once for every part number on the quote.
    """
    entry = ctx.deps.catalog.get(part_number.strip().upper())
    return entry or f"{part_number} is not in our catalog"


@agent.output_validator
async def totals_add_up(ctx: RunContext[Deps], out: QuoteAnalysis) -> QuoteAnalysis:
    computed = sum(i.quantity * i.unit_price for i in out.line_items)
    if out.line_items and abs(computed - out.total) > max(1.0, 0.01 * out.total):
        raise ModelRetry(
            f"Line items sum to {computed:.2f} but total is {out.total:.2f}. "
            "Re-read the quote: fix a misread quantity/price, or keep the stated total and add a "
            "'warning' flag that the quote's total does not match its lines."
        )
    return out


card = AgentCard(
    name="supplier_quote",
    description="Extracts a supplier quote into structured lines and checks it against the catalog.",
    agent=agent,
    input_type=QuoteInput,
    deps=Deps.load,
)
