"""P1 nodes and the LLM layer, offline (fake chat model)."""

from types import SimpleNamespace

import contracts
from agent import llm, nodes
from tests.conftest import DEMO


class FakeLLM:
    def __init__(self, args=None, text="Option 1 is cheapest.", fail_forced=False):
        self.args, self.text, self.fail_forced, self.tool_choices = args, text, fail_forced, []

    def bind_tools(self, tools, tool_choice=None):
        self.tool_choices.append(tool_choice)
        if self.fail_forced and tool_choice not in ("auto", None):
            return SimpleNamespace(invoke=lambda m: (_ for _ in ()).throw(ValueError("forced not allowed")))
        return SimpleNamespace(invoke=lambda m: SimpleNamespace(tool_calls=[{"args": self.args}]))

    def invoke(self, messages):
        return SimpleNamespace(content=self.text)


def test_rules_parser_reads_the_doc_example():
    c = llm.rules_parse(DEMO)
    assert (c.cuisine, c.people, c.budget_total, c.max_eta_min) == ("Chinese", 2, 40.0, 40)
    assert c.hard_excludes == ["mushrooms"]
    assert "noodles" in c.soft_prefs


def test_parse_with_llm_returns_valid_constraints():
    llm.set_llm(FakeLLM({"cuisine": "Chinese", "people": 2, "budget_total": 40, "max_eta_min": 40,
                         "hard_excludes": ["mushrooms"], "soft_prefs": ["noodles"]}))
    c, parser = llm.extract_constraints(DEMO)
    assert parser == "llm" and c.people == 2 and c.hard_excludes == ["mushrooms"]


def test_llm_cannot_invent_a_hard_rule():
    llm.set_llm(FakeLLM({"people": 2, "hard_excludes": ["mushrooms", "peanuts"]}))
    c, _ = llm.extract_constraints(DEMO)
    assert c.hard_excludes == ["mushrooms"]  # peanuts never stated by the user


def test_forced_tool_choice_falls_back_to_auto():
    fake = FakeLLM({"people": 2}, fail_forced=True)
    llm.set_llm(fake)
    c, parser = llm.extract_constraints(DEMO)
    assert parser == "llm" and fake.tool_choices == ["ExtractConstraints", "auto"]


def test_bad_llm_output_falls_back_to_rules():
    llm.set_llm(FakeLLM({"people": "lots"}))
    c, parser = llm.extract_constraints(DEMO)
    assert parser == "rules" and c.people == 2


def test_parse_request_saves_stated_hard_rule_only():
    contracts.set_audit_context("r", "parse_request")
    nodes.parse_request({"request_text": DEMO, "user_id": "u1", "memory": {}})
    prefs = contracts.read_preferences("u1")
    assert [(p["text"], p["kind"], p["source"]) for p in prefs] == [("mushrooms", "hard", "stated")]


def test_handle_feedback_new_hard_rule_from_user_words():
    contracts.set_audit_context("r", "handle_feedback")
    out = nodes.handle_feedback({"feedback": "no, I'm allergic to peanuts", "user_id": "u2", "constraints": {},
                                 "selected": {"offer": {"offer_id": "o1"}}, "rejected": []})
    assert "peanuts" in out["constraints"]["hard_excludes"] and out["rejected"] == ["o1"]


def test_feedback_routes():
    assert llm.read_feedback("too oily")["route"] == "more"
    assert llm.read_feedback("actually make it Thai")["route"] == "new"
    assert llm.read_feedback("never mind, done")["route"] == "done"


def test_submit_refuses_without_approval(tools):
    contracts.set_audit_context("r", "submit_or_handoff")
    out = nodes.submit_or_handoff({"approval": None, "quote": {}})
    assert tools.calls["submit"] == 0 and "approval" in out["reply"]


def test_explain_reasons_are_marked_and_cited():
    contracts.set_audit_context("r", "explain")
    eid = contracts.audit("evidence", {"price": 14.5})
    offer = contracts.Offer(restaurant="A", item="Spicy Noodles", price=14.5, fees=2, eta_min=30,
                            source="doordash", evidence_id=eid).model_dump(mode="json")
    out = nodes.explain({"constraints": {"people": 2, "soft_prefs": ["noodles"], "hard_excludes": ["mushrooms"]},
                         "ranked": [{"offer": offer, "total": 31.0, "score": -31}]})
    reasons = out["shown"][0]["reasons"]
    assert {r["kind"] for r in reasons} == {"evidence", "inference"}
    assert all(r["evidence_ids"] for r in reasons)
