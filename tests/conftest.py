"""Tests never call a real model: ALLOW_MODEL_REQUESTS=False makes any accidental call fail loudly.

Use TestModel (auto-generates valid tool calls/outputs) or FunctionModel (you script the model's turns).
Mark tests that need the real API with @pytest.mark.live.
"""

import os

import pytest
from pydantic_ai import models

from app import observability

models.ALLOW_MODEL_REQUESTS = False
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")
observability.setup("industrial-agents-tests")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("ANTHROPIC_API_KEY", "").startswith("test-"):
        skip = pytest.mark.skip(reason="needs ANTHROPIC_API_KEY")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip)
