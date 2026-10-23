"""Scripting the model with FunctionModel: test tool use and self-correction without any API call."""

from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from app.agents.supplier_quote import card
from app.agents.supplier_quote.schemas import Flag, QuoteAnalysis
from app.flows.quote_comparison.flow import rank

GOOD = {
    "supplier": "Hydratech GmbH",
    "currency": "EUR",
    "line_items": [{"part_number": "HV-200", "description": "valve", "quantity": 12, "unit_price": 398.0}],
    "total": 4776.0,
    "flags": [],
    "recommendation": "accept",
    "rationale": "Approved supplier, below target.",
}


def scripted(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    parts = [p for m in messages for p in m.parts]
    output_tool = info.output_tools[0].name
    if not any(isinstance(p, ToolReturnPart) and p.tool_name == "lookup_part" for p in parts):
        return ModelResponse(parts=[ToolCallPart("lookup_part", {"part_number": "hv-200"})])
    if not any(isinstance(p, ToolCallPart) and p.tool_name == output_tool for p in parts):
        return ModelResponse(parts=[ToolCallPart(output_tool, {**GOOD, "total": 9999.0})])  # wrong sum
    return ModelResponse(parts=[ToolCallPart(output_tool, GOOD)])


async def test_looks_up_parts_and_fixes_its_total():
    with card.agent.override(model=FunctionModel(scripted)):
        result = await card.agent.run("quote...", deps=card.deps())  # type: ignore[misc]
    assert result.output.total == 4776.0
    returns = [p for m in result.all_messages() for p in m.parts if isinstance(p, ToolReturnPart)]
    catalog_hit = next(p for p in returns if p.tool_name == "lookup_part")
    assert catalog_hit.content.target_price_eur == 410.0  # case-insensitive lookup worked
    # The validator's ModelRetry goes back to the model as the output tool's result; then it fixes the total.
    feedback = next(p for p in returns if p.tool_name == "final_result")
    assert "sum to 4776.00" in str(feedback.content)


def test_rank_prefers_accept_then_fewer_flags():
    def qa(supplier, rec, flags=()):
        return QuoteAnalysis.model_validate(
            {**GOOD, "supplier": supplier, "recommendation": rec, "flags": list(flags)}
        )

    warn = Flag(severity="warning", message="slow")
    ranked = rank([qa("C", "reject"), qa("B", "negotiate", [warn, warn]), qa("A", "negotiate", [warn])])
    assert [a.supplier for a in ranked] == ["A", "B", "C"]
