"""`kosika start`: the way in that needs nothing to be known first.

One command, no options. It makes a folder of starter agents if there is
none, looks for a model server on this computer, picks a model that is
already installed, and opens Studio. It grants nothing: no folder, no
programs, no websites. Everything it decided is said in plain words before
Studio opens, with the command to run to decide otherwise.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .starter import StarterError, create_starter

OLLAMA = "http://localhost:11434"
#: Starter agents made in a new folder, in this order.
STARTERS = ("support", "expense", "notes", "researcher")
#: Models tried first when several are installed: known to follow the
#: structured answers Kosika asks for, largest first.
PREFERRED = ("qwen2.5:14b", "qwen2.5:7b", "llama3.1:8b", "qwen2.5:3b", "llama3.2:3b")
#: A model larger than this is not picked unasked: it may not fit in memory.
MAX_PICKED_BYTES = 9 * 1024**3


def default_folder() -> Path:
    return Path.home() / "KosikaAgents"


def installed_models(
    url: str = OLLAMA, timeout: float = 2.0
) -> list[dict[str, Any]] | None:
    """The models an Ollama on this computer has, or None if none answers."""

    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=timeout) as response:
            found = json.loads(response.read().decode("utf-8")).get("models")
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None
    return (
        [model for model in found if isinstance(model, dict)]
        if isinstance(found, list)
        else None
    )


def _is_embedding(model: dict[str, Any]) -> bool:
    return "embed" in str(model.get("name", "")).lower()


def choose_model(models: list[dict[str, Any]]) -> str | None:
    """The chat model to use: a preferred one if installed, else the largest
    that is not too large."""

    chat = [model for model in models if not _is_embedding(model) and model.get("name")]
    names = [str(model["name"]) for model in chat]
    for wanted in PREFERRED:
        if wanted in names:
            return wanted
    fitting = [m for m in chat if int(m.get("size") or 0) <= MAX_PICKED_BYTES] or chat
    fitting.sort(key=lambda model: (-int(model.get("size") or 0), str(model["name"])))
    return str(fitting[0]["name"]) if fitting else None


def one_model_warning(setting: str | None) -> list[str]:
    """What to say when Ollama is set to keep a single model in memory.

    Search by meaning uses a second, small model. If only one model may be
    loaded, the chat model is thrown out each time and loaded again, which on
    a laptop was measured at about 12 seconds for every question.
    """

    if setting is None or setting.strip() != "1":
        return []
    return [
        "  Slow: Ollama is set to keep only one model in memory",
        "    (OLLAMA_MAX_LOADED_MODELS=1). With search by meaning on, the chat",
        "    model is reloaded for every question, which can add ten seconds or",
        "    more each time. If this computer has memory to spare, set it to 2",
        "    and restart Ollama.",
    ]


def start_plan(
    folder: Path, models: list[dict[str, Any]] | None
) -> tuple[list[str], list[str]]:
    """What `kosika start` will do: the Studio arguments, and the lines that
    tell the person. Makes the starter agents if the folder has none."""

    lines = ["Kosika is starting."]
    made: list[str] = []
    if not folder.exists() or not any(folder.iterdir()):
        for kind in STARTERS:
            try:
                create_starter(folder / kind, kind)
                made.append(kind)
            except (StarterError, OSError) as exc:
                lines.append(f"  Could not make the starter agent {kind}: {exc}")
    lines.append(
        f"  Agents folder: {folder}"
        + (
            f" ({len(made)} starter agents were made for you: {', '.join(made)})"
            if made
            else ""
        )
    )
    arguments = ["studio", str(folder)]
    chosen = choose_model(models) if models else None
    if chosen:
        arguments += ["--model", chosen, "--model-protocol", "ollama"]
        lines.append(f"  Model: {chosen}, from Ollama on this computer.")
        embedding = next(
            (
                str(m["name"])
                for m in models or []
                if _is_embedding(m) and m.get("name")
            ),
            None,
        )
        if embedding:
            arguments += ["--embed-model", embedding.removesuffix(":latest")]
            lines.append(
                f"  Search by meaning: on, with {embedding.removesuffix(':latest')}."
            )
            lines += one_model_warning(os.environ.get("OLLAMA_MAX_LOADED_MODELS"))
    elif models is None:
        lines += [
            "  Model: none found. You can look at agents, read their maps and run their tests.",
            "  To run agents, install Ollama (ollama.com), then: ollama pull qwen2.5:7b",
            "  and run `kosika start` again.",
        ]
    else:
        lines += [
            "  Model: Ollama is running but has no chat model installed.",
            "  Get one with: ollama pull qwen2.5:7b   and run `kosika start` again.",
        ]
    lines += [
        "  Granted: nothing. No agent can read your files, start programs or reach the web.",
        "  To let agents work in one folder, open Permissions in Studio and",
        "  press Choose a folder. You pick it in this computer's own folder window.",
    ]
    return arguments, lines
