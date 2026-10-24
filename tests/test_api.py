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


def test_a_building_drafted_in_chat_is_on_the_map():
    """load_floor_plans switches one chat to a new building; its id must then work for the map (scene),
    the building list and other chats, sharing that chat's state."""
    import copy

    from app.agents.building_guide.tools import Deps
    from app.api import main
    from app.building.model import load_building, parse_building
    from app.building.state import BuildingState

    raw = copy.deepcopy(load_building("proc-factory-dev-003").raw)
    raw["building"] = raw["building"] | {"id": "drafted-hall", "name": "Drafted hall"}
    deps = Deps(BuildingState(parse_building(raw)))
    main._sessions[("proc-campus-dev-000", "chat-1")] = deps
    try:
        assert client.get("/buildings/drafted-hall/scene").status_code == 200
        assert main.live_state("drafted-hall") is deps.state  # one shared state, not a copy
        drafted = client.get("/buildings", params={"split": "drafted"}).json()
        assert [b["id"] for b in drafted] == ["drafted-hall"]
    finally:
        main._sessions.pop(("proc-campus-dev-000", "chat-1"), None)
        main._live.pop("drafted-hall", None)
