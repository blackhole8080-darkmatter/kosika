"""The virtual model router, learning which free model to ask for what.

    python learning_router.py                 # serves http://127.0.0.1:8791/v1
    python learning_router.py --show          # what it has learned so far

router.py tries its models in the order router.json gives. This one keeps a
ledger of every answer each model gave, by kind of job (an edit to a long
file, a Kosika file, a call with tools, a plain question), and tries first
the model that has been quickest to give an answer that passes, for that
kind. A model that keeps failing a kind sinks for that kind only, and a fast
one rises; one never tried yet keeps its place from router.json, so new
models still get a chance. Nothing else changes: the same checks, the same
rate-limit rests, the same log.

The ledger is ~/.kosika/router-ledger.json. It holds counts and times, never
a word of any request.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import socket

import router as base

_resolve = socket.getaddrinfo


def _ipv4_first(host, *args, **kwargs):
    """Addresses with IPv4 ones first. On a network whose IPv6 route is
    broken, every request otherwise waits out an IPv6 connection (some 40
    seconds) before trying IPv4, which answers in a third of a second."""
    found = _resolve(host, *args, **kwargs)
    return sorted(found, key=lambda entry: entry[0] != socket.AF_INET)


socket.getaddrinfo = _ipv4_first

#: A model's first answers count for less than its later ones: until it has
#: this many, its record is blended with a middling prior.
PRIOR_ANSWERS = 3
#: What an untried model is assumed to take, in milliseconds.
PRIOR_MS = 20_000


def kind_of(body: dict) -> str:
    """The kind of job a request is, from its own words and shape."""
    if body.get("tools"):
        return "tools"
    messages = body.get("messages") or []
    last = str(messages[-1].get("content") or "") if messages else ""
    if "<<<<<<< FIND" in last or "with edits; do not write it again" in last:
        return "edit"
    if ".kos" in last or "Kosika is its own language" in last:
        return "kosika"
    if "Write the complete new" in last:
        return "file"
    return "plain"


class Ledger:
    """Answers and times for each model and kind, kept on disk."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data: dict = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def note(self, kind: str, model: str, ok: bool, ms: int) -> None:
        with self.lock:
            row = self.data.setdefault(kind, {}).setdefault(model, {"asked": 0, "ok": 0, "ms_ok": 0})
            row["asked"] += 1
            if ok:
                row["ok"] += 1
                row["ms_ok"] += ms
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")

    def cost(self, kind: str, model: str) -> float:
        """Expected milliseconds until a passing answer from this model:
        its time for a good answer over how often it gives one."""
        row = self.data.get(kind, {}).get(model)
        if not row:
            return float("nan")
        asked, ok = row["asked"], row["ok"]
        rate = (ok + 0.5 * PRIOR_ANSWERS) / (asked + PRIOR_ANSWERS)
        mean = (row["ms_ok"] + PRIOR_MS * PRIOR_ANSWERS) / (ok + PRIOR_ANSWERS)
        return mean / max(rate, 0.01)

    def order(self, kind: str, route: list[str]) -> list[str]:
        """The route, with tried models sorted by cost into the places tried
        models held; untried ones keep theirs."""
        tried = [m for m in route if self.cost(kind, m) == self.cost(kind, m)]
        ranked = iter(sorted(tried, key=lambda m: self.cost(kind, m)))
        return [next(ranked) if m in tried else m for m in route]


#: The Kosika program that makes every routing decision.
CHOOSE = Path(__file__).resolve().parent / "choose.kos"
NATIVE = base.HERE.parents[1] / "native"
#: Kosika's Go engine, built once from native/ and kept here.
ENGINE = Path.home() / ".kosika" / "bin" / ("kosika-native.exe" if os.name == "nt" else "kosika-native")


def engine() -> Path | None:
    """Kosika's Go engine, built from this checkout the first time."""
    if not ENGINE.exists():
        ENGINE.parent.mkdir(parents=True, exist_ok=True)
        built = subprocess.run(["go", "-C", str(NATIVE), "build", "-o", str(ENGINE), "."], capture_output=True)
        if built.returncode != 0:
            return None
    return ENGINE


def kosika_order(body: dict, route: list[str], ledger: Ledger) -> tuple[str, list[str]] | None:
    """The kind of job and the order to ask models in, as choose.kos decides:
    the Kosika program is given the request's last words, whether it offers
    tools, the route and the ledger, and gives back an Order. None when the
    engine cannot be had or the program fails; the router then decides in
    Python, the same way."""
    found = engine()
    if found is None:
        return None
    messages = body.get("messages") or []
    last = str(messages[-1].get("content") or "") if messages else ""
    seen = [
        {"kind": kind, "name": name, **row}
        for kind, models in ledger.data.items()
        for name, row in models.items()
    ]
    job = {"job": {"text": last[:20000], "tools": bool(body.get("tools")), "route": route, "seen": seen}}
    with tempfile.TemporaryDirectory() as folder:
        given = Path(folder) / "job.json"
        given.write_text(json.dumps(job), encoding="utf-8")
        ran = subprocess.run([str(found), "run", str(CHOOSE), "--input", str(given), "--json"],
                             capture_output=True, text=True, cwd=folder, timeout=20)
    if ran.returncode != 0:
        return None
    try:
        order = json.loads(ran.stdout.strip().splitlines()[0])
        return str(order["kind"]), [str(m) for m in order["models"]]
    except (ValueError, KeyError, IndexError):
        return None


class LearningRouter(base.Router):
    def __init__(self, config: dict, log: Path, ledger: Ledger) -> None:
        super().__init__(config, log)
        self.ledger = ledger
        self.local = threading.local()

    def route(self, start: str) -> list[str]:
        written = self.fitting(super().route(start))
        decided = kosika_order(getattr(self.local, "body", {}), written, self.ledger)
        if decided is not None and sorted(decided[1]) == sorted(written):
            self.local.kind = decided[0]
            return decided[1]
        return self.ledger.order(getattr(self.local, "kind", "plain"), written)

    def fitting(self, route: list[str]) -> list[str]:
        """The route without models the request is too long for. A model on
        a laptop's processor reads a long coding prompt in tens of minutes:
        router.json gives it a `max_prompt_chars`, and a longer request
        waits for a model that can read it in time instead."""
        body = getattr(self.local, "body", {})
        size = sum(len(str(m.get("content") or "")) for m in body.get("messages") or [])
        kept = [m for m in route if size <= self.config["models"][m].get("max_prompt_chars", size)]
        return kept or route

    def answer(self, body: dict) -> tuple[int, dict]:
        self.local.body = body
        self.local.kind = kind_of(body)
        code, reply = super().answer(body)
        return code, reply

    def write(self, start: str, why: str, tried: list, began: float, ok: bool) -> None:
        kind = getattr(self.local, "kind", "plain")
        for step in tried:
            if "skipped" in step:
                continue
            good = step.get("status") == 200 and not step.get("problem")
            self.ledger.note(kind, step["model"], good, int(step.get("ms", 0)))
        super().write(start, why + f"; kind {kind}", tried, began, ok)


def with_keys(path: Path) -> dict:
    """router.json without the models whose key is not set here, and out
    of every route: on a machine that has only a Groq key, such as a cloud
    runner, the router still starts and asks what it can."""
    config = json.loads(path.read_text(encoding="utf-8"))
    missing = {name for name, model in config["models"].items() if model.get("key") and not os.environ.get(model["key"])}
    if len(missing) == len(config["models"]):
        raise SystemExit("No model's key is set; the router reads keys from the environment only.")
    for name in missing:
        del config["models"][name]
    config["routes"] = {route: [m for m in names if m not in missing] for route, names in config["routes"].items()}
    return config


def show(ledger: Ledger) -> None:
    for kind, models in sorted(ledger.data.items()):
        print(kind)
        for model in ledger.order(kind, sorted(models)):
            row = models[model]
            mean = row["ms_ok"] / row["ok"] / 1000 if row["ok"] else 0
            print(f"  {model:10} {row['ok']:3}/{row['asked']:<3} passed   {mean:6.1f} s a good answer")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(base.HERE / "router.json"))
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--log", default=str(Path.home() / ".kosika" / "virtual-router.jsonl"))
    parser.add_argument("--ledger", default=str(Path.home() / ".kosika" / "router-ledger.json"))
    parser.add_argument("--show", action="store_true")
    args = parser.parse_args()
    ledger = Ledger(Path(args.ledger))
    if args.show:
        show(ledger)
        return
    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    router = LearningRouter(with_keys(Path(args.config)), log, ledger)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), base.handler(router))
    print(f"{base.NAME} (learning) on http://127.0.0.1:{args.port}/v1, ledger {ledger.path}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
