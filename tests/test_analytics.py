"""P3's Statsig wiring with a fake server: no network."""
from types import SimpleNamespace

import pytest

pytest.importorskip("statsig")

import contracts  # noqa: E402
from agent import graph  # noqa: E402
from sim_ui import analytics  # noqa: E402


class FakeServer:
    def __init__(self, value=True, reason="Network", boom=False):
        self.value, self.reason, self.boom, self.events = value, reason, boom, []

    def get_feature_gate(self, user, name):
        if self.boom:
            raise RuntimeError("statsig down")
        return SimpleNamespace(get_value=lambda: self.value,
                               evaluation_details=SimpleNamespace(reason=self.reason))

    def log_event(self, event):
        if self.boom:
            raise RuntimeError("statsig down")
        self.events.append(event)


@pytest.mark.parametrize("server, expected", [
    (FakeServer(value=True), "reranker"),
    (FakeServer(value=False), "price"),
    (FakeServer(value=False, reason="Unrecognized"), "reranker"),   # gate not created: keep the default
    (FakeServer(boom=True), "reranker"),
])
def test_ranker_from_the_gate(server, expected, monkeypatch):
    monkeypatch.delenv("RANKER", raising=False)
    assert analytics.install(server) is True
    assert graph._ranker_provider("demo") == expected


def test_events_reach_statsig_scrubbed_and_never_raise(monkeypatch):
    server = FakeServer()
    analytics.install(server)
    contracts.log_event("options_shown", {"run_id": "run_1", "count": 3, "api_key": "sk-secret-123456"})
    (event,) = server.events
    assert event.event_name == "options_shown" and event.metadata["count"] == "3"
    assert "sk-secret-123456" not in str(event.metadata)
    analytics.install(FakeServer(boom=True))
    contracts.log_event("anything", {"x": 1})   # must not raise


def test_no_key_changes_nothing(monkeypatch):
    monkeypatch.setenv("STATSIG_SERVER_KEY", "")
    before = graph._ranker_provider
    assert analytics.install() is False and graph._ranker_provider is before


@pytest.fixture(autouse=True)
def restore_hooks():
    provider = graph._ranker_provider
    yield
    graph.set_ranker_provider(provider)
    contracts.set_event_sink(lambda name, metadata: None)
