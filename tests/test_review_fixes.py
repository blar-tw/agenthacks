"""Regression tests for defects found in the architect and security reviews."""

import json
from types import SimpleNamespace

import pytest

import contracts
from agent import llm
from agent.graph import chat
from tests.conftest import DEMO


@pytest.mark.parametrize("reply", ["ok but too expensive, cancel", "yeah no", "sure thing not", "yes I mean no",
                                   "no", "maybe", "yes please, but cheaper", "looks good not"])
def test_hedged_or_negative_reply_is_not_approval(tools, reply):
    chat("t", DEMO)
    chat("t", "1")
    chat("t", reply)
    assert tools.calls["submit"] == 0


@pytest.mark.parametrize("reply", ["yes", "Yes!", " ok ", "approve", "go ahead.", "yes please", "yes, go ahead",
                                   "looks good", "Sounds good, thanks!"])
def test_plain_yes_is_approval(tools, reply):
    chat("t", DEMO)
    chat("t", "1")
    assert chat("t", reply)["status"] == "done" and tools.calls["submit"] == 1


def test_never_mind_creates_no_hard_rule():
    fb = llm.read_feedback("no, never mind")
    assert fb["route"] == "done" and fb["new_hard_rules"] == []


def test_no_thanks_is_not_an_exclude():
    assert llm.rules_parse("Noodles for two, no thanks to delivery fees").hard_excludes == []
    assert llm.rules_parse("noodles, no more spice please").hard_excludes == []
    assert llm.rules_parse("noodles, no peanuts").hard_excludes == ["peanuts"]


def test_allergy_in_feedback_still_creates_rule():
    assert llm.read_feedback("too oily, and I'm allergic to peanuts")["new_hard_rules"] == ["peanuts"]


def test_rejection_survives_a_rescrape(tools):
    r1 = chat("t", DEMO)
    first = r1["options"][0]["offer_id"]
    chat("t", "1")
    r = chat("t", "no, instead make it spicy rice noodles")  # new search -> rescrape
    assert tools.calls["search"] == 4
    assert first not in [o["offer_id"] for o in r["options"]]


def test_scrub_redacts_secret_values_and_more_keys():
    out = contracts.scrub({"note": "key sk-or-v1-abcdef1234567890 and card 4111 1111 1111 1111",
                           "auth_header": "x", "pwd": "y", "items": ("a", {"jwt": "z"})})
    assert out == {"note": "key <redacted> and card <redacted>", "items": ["a", {}]}


def test_fallback_cap_stops_openrouter_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "spend.json")
    monkeypatch.setenv("LLM_PROVIDER", "fallback")
    monkeypatch.setenv("FALLBACK_LLM_BASE_URL", "http://unused")
    monkeypatch.setenv("FALLBACK_LLM_API_KEY", "unused")
    monkeypatch.setenv("FALLBACK_LLM_MODEL", "unused")
    monkeypatch.setenv("FALLBACK_LLM_BUDGET_USD", "0.01")
    llm.record_fallback_cost(SimpleNamespace(usage_metadata={"input_tokens": 1000, "output_tokens": 500}))
    assert llm.fallback_spent() == pytest.approx(1000 * 2e-6 + 500 * 1e-5)
    assert llm.get_llm() is not None  # $0.007 < $0.01
    llm.record_fallback_cost(SimpleNamespace(usage_metadata={"input_tokens": 0, "output_tokens": 500}))
    assert llm.fallback_over_budget() and llm.get_llm() is None
    c, parser = llm.extract_constraints(DEMO)  # still works, rule-based
    assert parser == "rules" and c.people == 2
    assert json.loads((tmp_path / "spend.json").read_text())["budget"] == 0.01
