"""Contract tests that cover every agent and flow automatically, including the ones you add."""

import pytest
from pydantic_ai.models.test import TestModel

from app.core import AgentCard
from app.evals.runner import load_dataset
from app.registry import registry

ENTRIES = registry()


def test_examples_are_registered_and_template_is_not():
    assert {"supplier_quote", "quote_comparison"} <= set(ENTRIES)
    assert "__NAME__" not in ENTRIES


@pytest.mark.parametrize("name", list(ENTRIES))
def test_evals_yaml_parses(name):
    """Broken evals.yaml (typo in an evaluator, input not matching the schema) fails CI, not the demo."""
    dataset = load_dataset(ENTRIES[name])
    assert dataset.cases, f"{name}/evals.yaml has no cases"


@pytest.mark.parametrize("name", [n for n, e in ENTRIES.items() if isinstance(e.card, AgentCard)])
async def test_agent_smoke(name):
    """Prompt loads, tools register, output schema works, using the first eval case as input."""
    card = ENTRIES[name].card
    inputs = load_dataset(ENTRIES[name]).cases[0].inputs
    with card.agent.override(model=TestModel(call_tools=[])):
        output = await card.run(inputs)
    assert isinstance(output, card.agent.output_type)
