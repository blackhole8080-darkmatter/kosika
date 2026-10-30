"""Kosika Designs for every part of Kosika: one call, one shape of answer.

Studio, the website's tools and any Python glue ask Kosika Designs the
same way Chat and the agents do: they run the front door,
std/designs.kos in Kosika's standard library, and get back what it decided (a
diagram, an animated illustration or moving art), a whole page, the SVG,
and an embed that any page can drop in as it is.

The engine is the fast one when it is there (KOSIKA_NATIVE: the path of a
kosika-native executable), otherwise this Python's own `kosika run`. The
model is a free one: KOSIKA_DESIGNS_MODEL and KOSIKA_DESIGNS_MODEL_URL, or
by default Groq's gpt-oss-120b with the key in GROQ_API_KEY.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

FRONT_DOOR = Path(__file__).resolve().parent / "std" / "designs.kos"
KINDS = ("", "diagram", "illustration", "art", "canvas")
PLACES = ("chat", "studio", "site", "agent")
DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_MODEL_URL = "https://api.groq.com/openai/v1"


class DesignsError(Exception):
    """Designs could not draw, in words a person can act on."""


def _engine() -> list[str]:
    native = os.environ.get("KOSIKA_NATIVE", "")
    if native and Path(native).is_file():
        return [native]
    return [sys.executable, "-m", "kosika.cli"]


def _result(printed: str, first: str = "kind") -> dict[str, Any]:
    # `kosika run` prints the answer as a record, then a line naming its
    # trace; a line naming the model may come first.
    record = printed.split("\nTrace:")[0]
    start = record.find("{'" + first + "'")
    if start < 0:
        raise DesignsError(record.strip()[-400:] or "Designs gave no answer.")
    return ast.literal_eval(record[start:])


def design(
    text: str,
    *,
    palette: str = "",
    place: str = "studio",
    kind: str = "",
    timeout: float = 600,
) -> dict[str, Any]:
    """Ask Kosika Designs for a picture of `text`.

    The answer holds kind, why, title, page, svg, embed, problems and
    fixed, as std/designs.kos returns them."""

    text = text.strip()
    if not text:
        raise DesignsError("Say what to draw.")
    if kind not in KINDS:
        raise DesignsError("kind is diagram, illustration, art, canvas, or empty to let Kosika choose.")
    if place not in PLACES:
        raise DesignsError("place is chat, studio, site or agent.")
    if not FRONT_DOOR.is_file():
        raise DesignsError(f"Kosika Designs is not in this copy of Kosika ({FRONT_DOOR}).")
    ask = {"ask": {"text": text, "palette": palette, "place": place, "kind": kind}}
    return _run(FRONT_DOOR, "design", ask, timeout=timeout)


def _run(source: Path, workflow: str, given_input: dict, *, extra: list[str] | None = None, first: str = "kind", timeout: float = 600) -> dict[str, Any]:
    env = dict(os.environ)
    env.setdefault("KOSIKA_MODEL_API_KEY", os.environ.get("GROQ_API_KEY", ""))
    with tempfile.TemporaryDirectory() as work:
        given = Path(work) / "ask.json"
        given.write_text(json.dumps(given_input), encoding="utf-8")
        command = _engine() + [
            "run", str(source), "--workflow", workflow, "--input", str(given),
            "--model", os.environ.get("KOSIKA_DESIGNS_MODEL", DEFAULT_MODEL),
            "--model-url", os.environ.get("KOSIKA_DESIGNS_MODEL_URL", DEFAULT_MODEL_URL),
            "--model-protocol", "openai", "--model-timeout", "400",
            "--max-output-tokens", "8000",
        ] + (extra or [])
        try:
            done = subprocess.run(  # noqa: S603 - our own engine and our own file
                command, cwd=work, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise DesignsError("Designs took too long; try a shorter ask.") from exc
    return _result(done.stdout + ("\n" + done.stderr if done.returncode else ""), first)


STUDY = Path(__file__).resolve().parent.parent / "examples" / "agents" / "study" / "main.kos"


def study(text: str, *, timeout: float = 900) -> dict[str, Any]:
    """Ask the study companion (examples/agents/study): an answer in words,
    from the person's own study notes where they help, and a Designs canvas
    beside it. "remember: ..." keeps a note instead.

    Notes are kept in KOSIKA_STUDY_MEMORY, by default ~/.kosika/study. With
    KOSIKA_STUDY_EMBED set to an Ollama embedding model (nomic-embed-text),
    they are recalled by meaning; otherwise by the words they share."""

    text = text.strip()
    if not text:
        raise DesignsError("Say what to study, or what to remember.")
    if not STUDY.is_file():
        raise DesignsError(f"The study companion is not in this copy of Kosika ({STUDY}).")
    memory = Path(os.environ.get("KOSIKA_STUDY_MEMORY", str(Path.home() / ".kosika" / "study")))
    memory.mkdir(parents=True, exist_ok=True)
    extra = ["--memory", str(memory)]
    if os.environ.get("KOSIKA_STUDY_EMBED"):
        extra += ["--embed-model", os.environ["KOSIKA_STUDY_EMBED"]]
    return _run(STUDY, "main", {"msg": {"text": text}}, extra=extra, first="text", timeout=timeout)


def framed(embed: str) -> str:
    """An embed as a page of its own, for an iframe's srcdoc: its script then
    runs, and its styles cannot reach the page around it."""

    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>html,body{margin:0;background:transparent}</style></head>"
        f"<body>{embed}</body></html>"
    )
