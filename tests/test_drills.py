"""Firedrill drills (sim_ui/firedrill) run through the real agent with the synthetic shop."""
import re

import pytest

firedrill = pytest.importorskip("firedrill")

from sim_ui import drills  # noqa: E402


def test_every_drill_passes_on_fixtures():
    result = drills.run()
    ok, lines = drills.summarize(result)
    assert ok, "\n".join(lines)
    assert len(result["drills"]) == 10


def test_a_drill_catches_a_submit_without_a_plain_yes(monkeypatch):
    from agent import nodes
    monkeypatch.setattr(nodes, "_YES", re.compile(r".*", re.S))   # sabotage: every answer approves
    result = drills.run(drill="reject-at-approval")
    ok, lines = drills.summarize(result)
    assert not ok
    assert any("submits" in line for line in lines), lines
