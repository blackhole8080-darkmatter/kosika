"""Studio's Models page: the models Kosika's agents reach, and how each is doing.

The facts:

* the router's list of models (kosika-agents/models/router.json): each
  slot's model, where it is served and the name of its key;
* whether each key is set (as an environment or Windows user variable; the
  key itself is never read out);
* whether a local model is there: Ollama's own list at 127.0.0.1:11434, or
  the port of Kosika's own small model;
* the router's record of the last day (~/.kosika/virtual-router.jsonl):
  how often each slot was asked, answered, refused for its free allowance,
  not found, or refused as too big;
* where a model of Kosika's own was trained (kosika-daily/training/NAME-vN),
  so the page can say how to install it.

app_assets/models.kos says what the facts mean.
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .agents_home import agents_home

MODELS_KOS = Path(__file__).parent / "app_assets" / "models.kos"
ROUTER_PORT = int(os.environ.get("KOSIKA_ROUTER_PORT", "8791"))


def router_file() -> Path:
    return agents_home() / "models" / "router.json"


def ledger_file() -> Path:
    return Path.home() / ".kosika" / "virtual-router.jsonl"


def key_set(name: str) -> bool:
    if not name:
        return True
    if os.environ.get(name):
        return True
    if os.name != "nt":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return bool(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return False


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


def ollama_models() -> set[str] | None:
    """What Ollama has here, or None when Ollama is not running."""

    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as answer:
            names = {m.get("name", "") for m in json.load(answer).get("models", [])}
    except (OSError, ValueError):
        return None
    return names | {n.removesuffix(":latest") for n in names}


def provider_of(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc
    if "groq" in host:
        return "Groq"
    if "openrouter" in host:
        return "OpenRouter"
    if host.endswith(":11434"):
        return "Ollama"
    return "Kosika"


def install_hint(model: str) -> str:
    """How to install a model of Kosika's own, from its newest training."""

    base = Path.home() / "Aryan_Private" / "kosika-daily" / "training"
    trained = sorted(base.glob(f"{model}-v*/Modelfile"), key=lambda p: p.parent.name) if base.exists() else []
    if not trained:
        return ""
    return f'ollama create {model} -f "{trained[-1]}"'


def stats(since: datetime.datetime, path: Path | None = None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    path = path or ledger_file()
    if not path.exists():
        return out
    with path.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            try:
                said = json.loads(line)
                at = datetime.datetime.fromisoformat(said.get("at", ""))
            except (ValueError, TypeError):
                continue
            if at < since:
                continue
            for tried in said.get("tried", []):
                slot = tried.get("model")
                if not slot or "status" not in tried:
                    continue
                s = out.setdefault(slot, {"tries": 0, "ok": 0, "limited": 0, "missing": 0, "rejected": 0, "ms": []})
                status = tried["status"]
                s["tries"] += 1
                s["ok"] += status == 200
                s["limited"] += status == 429
                s["missing"] += status == 404
                s["rejected"] += status in (400, 413)
                if status == 200 and isinstance(tried.get("ms"), (int, float)):
                    s["ms"].append(tried["ms"])
    return out


def facts(now: datetime.datetime | None = None) -> dict[str, Any]:
    now = now or datetime.datetime.now()
    try:
        router = json.loads(router_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        router = {"models": {}, "routes": {}}
    seen = stats(now - datetime.timedelta(days=1))
    ollama = ollama_models()
    slots = []
    for slot, model in router.get("models", {}).items():
        url = str(model.get("url", ""))
        provider = provider_of(url)
        local = provider in ("Ollama", "Kosika")
        model_id = str(model.get("id", ""))
        if provider == "Ollama":
            reachable = ollama is not None and model_id in ollama
        elif provider == "Kosika":
            port = urllib.parse.urlparse(url).port
            reachable = bool(port) and port_open(port)
        else:
            reachable = True
        s = seen.get(slot, {})
        times = sorted(s.get("ms", []))
        slots.append({"slot": slot, "id": model_id, "provider": provider, "local": local,
                      "key_set": key_set(str(model.get("key", ""))), "reachable": reachable,
                      "tries": s.get("tries", 0), "ok": s.get("ok", 0), "limited": s.get("limited", 0),
                      "missing": s.get("missing", 0), "rejected": s.get("rejected", 0),
                      "ms": times[len(times) // 2] if times else 0,
                      "install": install_hint(model_id) if local and provider == "Ollama" else ""})
    return {"router_up": port_open(ROUTER_PORT), "slots": slots, "routes": router.get("routes", {}),
            "ollama_up": ollama is not None, "router_file": str(router_file())}


def page() -> dict[str, Any]:
    from .modules import load_program
    from .runtime import run

    told = facts()
    try:
        with tempfile.TemporaryDirectory(prefix="kosika-models-") as scratch:
            made = run(load_program(MODELS_KOS), "page", Path(scratch) / "run.jsonl",
                       inputs={"m": {"router_up": told["router_up"], "slots": told["slots"]}})
        shown, error = made.get("shown", made), ""
    except (ValueError, RuntimeError, OSError) as exc:
        shown, error = {"summary": "", "verdicts": [], "ready": 0}, f"models.kos: {exc}"
    return {**told, **shown, "ok": not error, "error": error}
