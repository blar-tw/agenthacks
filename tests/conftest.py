import os

import pytest

os.environ["LLM_PROVIDER"] = "none"  # tests never call a model or the network
os.environ["TOOL_MODE"] = "fixtures"

import contracts  # noqa: E402
from agent import graph as graph_mod  # noqa: E402
from agent import llm  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_world():
    contracts.set_store(contracts.InMemoryStore())
    contracts.reset_tools()
    contracts.EVENTS.clear()
    llm.set_llm(None)
    graph_mod.reset_graph()
    yield


@pytest.fixture
def tools():
    return contracts.get_tools("fixtures")


DEMO = graph_mod.DEMO_REQUEST
