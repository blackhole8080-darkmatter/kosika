"""Kosika's virtual models, up and checked: every model it can use, its own
included, asked a real Kosika edit, and the router started so that it
outlives the window that started it.

    python virtual_up.py            # check every model, then start the router
    python virtual_up.py --keep     # and keep everything up, checking every 30 minutes
    python virtual_up.py --check    # only say how each model does

What it does, in order:

1. Starts Ollama, the server for Kosika's own model kosika-coder, if it is
   not running, as a process of its own that stays when this one ends.
2. Asks every model in router.json one small Kosika edit (PROBE), straight
   at the model, not through the router: it passes when the answer is an
   edit that applies and gives the right line. Each result is written to
   the router's ledger, so the router learns how fast and how right every
   model is, Kosika's own as much as the cloud ones, instead of trying its
   own models only when every cloud model has failed.
3. Starts the learning router (port 8791) the same lasting way, after the
   ledger is written; one already running is stopped first, so that it
   reads the new ledger.

A model whose key is not set, or whose server is not running, is said so
and skipped. Nothing here costs money: the models are the free ones in
router.json and the ones on this computer.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "training"))

import kosika_virtual  # noqa: E402
import learning_router  # noqa: E402
import score  # noqa: E402

LEDGER = Path.home() / ".kosika" / "router-ledger.json"
HEALTH = Path.home() / ".kosika" / "virtual-health.jsonl"
DETACHED = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

SHOWN = '''def sign(n: float) -> int:
    if n < 0:
        return -1
    return 1
'''
WANTED = '''def sign(n: float) -> int:
    if n < 0:
        return -1
    if n == 0:
        return 0
    return 1
'''
PROBE = [
    {"role": "system", "content":
     "You are the Kosika coder. You change Kosika's files with edits: each edit is <<<<<<< FIND, whole lines "
     "copied exactly from the file, =======, the lines to put in their place, >>>>>>> REPLACE. Answer with the edit blocks only."},
    {"role": "user", "content": "Task: sign(0) must give 0, not 1. Change only what that needs.\n\n"
     "The parts of kosika/numbers.py the task is about:\n```\n" + SHOWN + "```\n\nChange the files with edits."},
]


def listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def start(command: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    subprocess.Popen(command, cwd=cwd, env=env, creationflags=DETACHED,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def ensure_ollama() -> str:
    if listening(11434):
        return "running"
    ollama = shutil.which("ollama")
    if not ollama:
        return "not installed"
    # Several requests answered together: the router asks kosika-coder's
    # tries at once, and a swarm's coders share it.
    start([ollama, "serve"], env={**os.environ, "OLLAMA_NUM_PARALLEL": os.environ.get("OLLAMA_NUM_PARALLEL", "4")})
    for _ in range(40):
        if listening(11434):
            return "started"
        time.sleep(0.5)
    return "did not start"


def key_of(model: dict) -> str:
    name = model.get("key") or ""
    if not name:
        return "none"
    return os.environ.get(name) or kosika_virtual.user_variable(name)


def ask(model: dict) -> tuple[bool, float, str]:
    """One probe of one model: passed, seconds, and what went wrong."""
    key = key_of(model)
    if not key:
        return False, 0.0, f"no {model['key']} set"
    body = {"model": model["id"], "messages": PROBE, "temperature": 0.1, "max_tokens": 600, **model.get("extra", {})}
    request = urllib.request.Request(model["url"], data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + key, "User-Agent": "Kosika/0.1 (health)"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=min(240, model.get("timeout_seconds", 120))) as answer:
            text = json.load(answer)["choices"][0]["message"].get("content") or ""
    except urllib.error.HTTPError as exc:
        return False, time.monotonic() - started, f"HTTP {exc.code}"
    except Exception as exc:  # a refused connection, a timeout
        reason = getattr(exc, "reason", exc)
        return False, time.monotonic() - started, f"{type(exc).__name__}: {str(reason)[:80]}"
    seconds = time.monotonic() - started
    got = score.apply(SHOWN, re.sub(r"^```\w*\n|```\s*$", "", text.strip(), flags=re.M))
    if got is None:
        return False, seconds, "no edit that applies"
    # Judged by what the code does, not by its words: any way of writing it
    # that gives -1, 0 and 1 passes. It runs in a Python of its own, isolated
    # and with nothing but the function, for at most five seconds.
    test = got + "\nassert sign(-2) == -1 and sign(0) == 0 and sign(3) == 1 and sign(0.5) == 1\n"
    try:
        ran = subprocess.run([sys.executable, "-I", "-c", test], capture_output=True, timeout=5)
    except subprocess.TimeoutExpired:
        return False, seconds, "applies, but the code hangs"
    if ran.returncode != 0:
        return False, seconds, "applies, but the code is not right"
    return True, seconds, ""


def stop_router() -> None:
    if not listening(kosika_virtual.PORT):
        return
    if os.name == "nt":
        found = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
        for line in found.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(f":{kosika_virtual.PORT}") and parts[3] == "LISTENING":
                subprocess.run(["taskkill", "/PID", parts[4], "/F"], capture_output=True)
    for _ in range(20):
        if not listening(kosika_virtual.PORT):
            return
        time.sleep(0.25)


def check(config: dict, note: bool) -> list[dict]:
    ledger = learning_router.Ledger(LEDGER)
    rows = []
    for name, model in config["models"].items():
        ok, seconds, problem = ask(model)
        row = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "name": name, "model": model["id"], "ok": ok,
               "seconds": round(seconds, 1), "problem": problem}
        rows.append(row)
        print(f"  {'✓' if ok else '✗'} {name:7} {model['id'][:34]:34} {seconds:6.1f} s  {problem}", flush=True)
        if note and not problem.startswith(("no ", "URLError")):
            ledger.note("edit", name, ok, int(seconds * 1000))
    HEALTH.parent.mkdir(parents=True, exist_ok=True)
    with HEALTH.open("a", encoding="utf-8") as out:
        out.write("".join(json.dumps(r) + "\n" for r in rows))
    return rows


def local_share(days: int = 7) -> tuple[int, int]:
    """Of the requests the router answered in the last `days`, how many
    Kosika's own models (or its kept answers) answered: the number that
    says whether Kosika is getting more independent."""
    log = Path.home() / ".kosika" / "virtual-router.jsonl"
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days * 86400))
    own = total = 0
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("at", "") < since or not row.get("ok"):
                continue
            total += 1
            winner = next((t.get("model") for t in reversed(row.get("tried", [])) if t.get("status") == 200 and not t.get("problem")), "")
            own += winner in ("coder", "small", "local", "kept")
    return own, total


def up(only_check: bool) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    print("Ollama (Kosika's own model):", ensure_ollama())
    config = json.loads((HERE / "router.json").read_text(encoding="utf-8"))
    print("Asking every model one Kosika edit:")
    rows = check(config, note=not only_check)
    passed = [r["name"] for r in rows if r["ok"]]
    print(f"{len(passed)} of {len(rows)} passed: {', '.join(passed) or 'none'}")
    own, total = local_share()
    if total:
        print(f"This week Kosika's own models answered {own} of {total} requests ({100 * own // total}%) with no cloud.")
    if only_check:
        return
    stop_router()
    kosika_virtual.ensure_router()
    print(f"router: {'up' if listening(kosika_virtual.PORT) else 'DOWN'} on {kosika_virtual.URL}, model name {kosika_virtual.NAME}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="only say how each model does")
    parser.add_argument("--keep", action="store_true", help="keep everything up, checking every 30 minutes")
    args = parser.parse_args()
    up(args.check)
    while args.keep:
        time.sleep(30 * 60)
        if not listening(kosika_virtual.PORT) or not listening(11434):
            up(False)


if __name__ == "__main__":
    main()
