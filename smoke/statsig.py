"""Statsig: the server key initializes, the ranker gate returns a bool, an event flushes.
Name note: this file is smoke/statsig.py, so smoke/ is dropped from sys.path before importing the SDK.
"""
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _HERE]
sys.modules.pop("statsig", None)
sys.path.append(str(_HERE))

from _common import Skip, main  # noqa: E402

_server = None


def init() -> str:
    global _server
    key = os.environ.get("STATSIG_SERVER_KEY", "").strip()
    if not key:
        raise Skip("STATSIG_SERVER_KEY not set")
    from statsig import StatsigOptions, StatsigServer
    _server = StatsigServer()
    details = _server.initialize(key, StatsigOptions(overall_init_timeout=10.0))
    if not details.init_success:
        raise RuntimeError(f"initialize failed (source {details.source})")
    return f"initialized from {details.source}"


def gate() -> str:
    from statsig import StatsigUser
    name = os.environ.get("STATSIG_RANKER_GATE", "mealmatch_reranker")
    g = _server.get_feature_gate(StatsigUser(user_id="smoke-test"), name)
    if not isinstance(_server.check_gate(StatsigUser(user_id="smoke-test"), name), bool):
        raise TypeError("check_gate did not return a bool")
    reason = getattr(getattr(g, "evaluation_details", None), "reason", "")
    known = str(getattr(reason, "value", reason)) != "Unrecognized"
    return (f"gate {name} = {g.get_value()} " +
            ("(rule " + (g.rule_id or "default") + ")" if known else
             "(NOT CREATED in the Statsig console yet: the agent keeps its default ranker)"))


def event() -> str:
    from statsig import StatsigEvent, StatsigUser
    _server.log_event(StatsigEvent(user=StatsigUser(user_id="smoke-test"), event_name="smoke_check",
                                   metadata={"source": "smoke/statsig.py"}))
    _server.flush()
    _server.shutdown()
    return "log_event + flush ok"


if __name__ == "__main__":
    main([("statsig init", init), ("statsig ranker gate", gate), ("statsig event", event)])
