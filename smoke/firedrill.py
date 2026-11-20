"""Firedrill: the package imports, the drill world validates, the happy-path drill passes,
and the agent's POST /chat answers (SKIP when the server is not running)."""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import Skip, main  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SIM = ROOT / "sim_ui"


def imports() -> str:
    import firedrill  # noqa: F401
    from importlib.metadata import version
    return f"firedrill-run {version('firedrill-run')}"


def validate() -> str:
    exe = Path(sys.executable).parent / "firedrill"
    out = subprocess.run([str(exe), "validate"], cwd=SIM, capture_output=True, text=True, timeout=120)
    text = (out.stdout + out.stderr).strip()
    if out.returncode != 0 or "World valid" not in text:
        raise RuntimeError(text.splitlines()[-1] if text else f"exit {out.returncode}")
    return text.splitlines()[0]


def happy_path() -> str:
    out = subprocess.run([sys.executable, "-m", "sim_ui.drills", "--drill", "happy-path"], cwd=ROOT,
                         capture_output=True, text=True, timeout=300)
    first = next((l for l in out.stdout.splitlines() if l.startswith(("PASS", "FAIL"))), out.stderr[-200:])
    if out.returncode != 0:
        raise RuntimeError(first)
    return first


def chat_endpoint() -> str:
    body = json.dumps({"message": "Find me spicy noodles for two under $40", "thread_id": "smoke-firedrill"})
    req = urllib.request.Request("http://localhost:8000/chat", data=body.encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.load(r)
    except (ConnectionError, OSError) as e:
        raise Skip(f"agent server not running on :8000 ({type(e).__name__})")
    return f"status {data.get('status')}, {len(data.get('ranked') or data.get('options') or [])} options"


if __name__ == "__main__":
    main([("firedrill import", imports), ("firedrill validate", validate),
          ("firedrill happy-path drill", happy_path), ("POST /chat", chat_endpoint)])
