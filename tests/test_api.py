from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from app.agents.supplier_quote import card
from app.api.main import app

client = TestClient(app)


def test_lists_agents_with_input_schemas():
    agents = {a["name"]: a for a in client.get("/agents").json()}
    assert agents["supplier_quote"]["chat_ui"] == "/ui/supplier_quote/"
    assert "document" in agents["supplier_quote"]["input_schema"]["properties"]


def test_run_endpoint():
    with card.agent.override(model=TestModel(call_tools=[])):
        response = client.post("/run/supplier_quote", json={"document": "a quote"})
    assert response.status_code == 200, response.text
    assert {"supplier", "recommendation"} <= response.json().keys()


def test_unknown_agent_is_404():
    assert client.post("/run/nope", json={}).status_code == 404
