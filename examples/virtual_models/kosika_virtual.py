"""Run any Kosika command on the virtual model, with nothing else to name.

    python kosika_virtual.py run main.kos --input in.json
    python kosika_virtual.py agent host.kos --agent monitor --task "..."
    python kosika_virtual.py studio FOLDER

It is `python -m kosika.cli ...` with two things done first: the learning
router is started if it is not already running, and `--model kosika-virtual
--model-url http://127.0.0.1:8791/v1` is added. So an agent started this way
is answered by whichever free model is quickest to pass for the job in hand,
down to a model on this computer, and by none that costs money.

Nothing about Kosika itself changes: a program run the ordinary way still
gets only the model its owner names, and one run with no model still runs
with none.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PORT = 8791
URL = f"http://127.0.0.1:{PORT}/v1"
NAME = "kosika-virtual"
KEYS = ("GROQ_API_KEY", "OPENROUTER_API_KEY")


def router_up() -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


def user_variable(name: str) -> str:
    """A key kept as a Windows user variable, which a new shell may not have."""
    if os.name != "nt":
        return ""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return ""


def ensure_router() -> None:
    """Start the learning router, and leave it running for the next command."""
    if router_up():
        return
    env = dict(os.environ)
    for name in KEYS:
        if not env.get(name):
            env[name] = user_variable(name)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(
        [sys.executable, str(HERE / "learning_router.py"), "--port", str(PORT)],
        env=env, cwd=HERE, creationflags=flags,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        if router_up():
            return
        time.sleep(0.25)
    raise SystemExit("the virtual model's router did not start; is a key missing? It needs " + " and ".join(KEYS))


def main() -> None:
    args = sys.argv[1:]
    if not args:
        raise SystemExit(__doc__)
    for flag in ("--model", "--model-url", "--models", "--model-fixture"):
        if flag in args:
            raise SystemExit(f"{flag} names a model already; run `python -m kosika.cli` for that.")
    ensure_router()
    env = dict(os.environ, PYTHONPATH=str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", ""))
    # The router asks no key of its callers; a real one must not be sent to it.
    env["KOSIKA_MODEL_API_KEY"] = "none"
    done = subprocess.run([sys.executable, "-m", "kosika.cli", *args, "--model", NAME, "--model-url", URL], env=env)
    raise SystemExit(done.returncode)


if __name__ == "__main__":
    main()
