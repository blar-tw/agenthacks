"""P1 graph + P2 data layer together: fallback paths still keep the audit trail whole."""
import contracts
from agent.graph import chat, get_graph, run_fixture_demo
from data import sie
from smoke.audit_integrity import check_run
from tests.conftest import DEMO


class BrokenSIE:
    def embed(self, texts, is_query=False):
        raise RuntimeError("503")

    def score(self, query, items):
        raise RuntimeError("503")


def test_graph_uses_p2_single_scrape_node():
    names = set(get_graph().get_graph().nodes)
    assert "scrape" in names and "scrape_ubereats" not in names


def test_sie_down_still_cites_evidence_everywhere(tools):
    sie.set_sie(BrokenSIE())
    run_id = run_fixture_demo()
    assert tools.calls["submit"] == 1
    assert check_run(contracts.get_audit(run_id)) == []


def test_no_options_still_cites_evidence():
    r = chat("t", "Spicy noodles for two under $10 within 40 minutes, no mushrooms")
    assert r["options"] == [] and "over budget" in r["reply"]
    assert check_run(contracts.get_audit(r["run_id"])) == []


def test_demo_shows_options_from_p2_fixtures():
    r = chat("t", DEMO)
    assert r["options"] and all(o["total"] <= 40 for o in r["options"])
    assert all("mushroom" not in o["item"].lower() for o in r["options"])
