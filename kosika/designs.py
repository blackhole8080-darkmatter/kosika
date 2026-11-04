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
# Kosika Brain thinks Designs through: it recalls what it drew for an ask
# like this one, reuses a canvas it kept when it is near enough, keeps what
# it drew, and takes a thumbs up or down. KOSIKA_BRAIN=0 draws without it.
BRAIN = Path(__file__).resolve().parent / "std" / "brain.kos"
KINDS = ("", "diagram", "illustration", "art", "canvas", "algorithm", "quiz", "worked", "chemistry", "units", "element")
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
    if _brain_on():
        return _run(BRAIN, "brain_design", ask, extra=_brain_memory(), timeout=timeout)
    return _run(FRONT_DOOR, "design", ask, timeout=timeout)


def _brain_on() -> bool:
    return BRAIN.is_file() and os.environ.get("KOSIKA_BRAIN", "1") != "0"


def _brain_memory() -> list[str]:
    # The Brain's memory stays on this computer, in KOSIKA_BRAIN_MEMORY,
    # by default ~/.kosika/brain. A canvas thought through by the Brain
    # needs more than the default 10,000 function calls.
    memory = Path(os.environ.get("KOSIKA_BRAIN_MEMORY", str(Path.home() / ".kosika" / "brain")))
    memory.mkdir(parents=True, exist_ok=True)
    extra = ["--memory", str(memory), "--max-function-calls", os.environ.get("KOSIKA_BRAIN_CALLS", "100000")]
    return extra + _brain_meaning()


def _brain_meaning() -> list[str]:
    # Recall by meaning, with an embedding model on this computer: Ollama's
    # nomic-embed-text when Ollama answers, unless KOSIKA_BRAIN_EMBED names
    # another (or is empty, to recall by words alone).
    model = os.environ.get("KOSIKA_BRAIN_EMBED", "nomic-embed-text")
    url = os.environ.get("KOSIKA_BRAIN_EMBED_URL", "http://127.0.0.1:11434")
    if not model or not _answers(url):
        return []
    return ["--embed-model", model, "--embed-url", url]


def _answers(url: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=2) as reply:  # noqa: S310 - the local embedding server
            return reply.status == 200
    except OSError:
        return False


def rate(text: str, liked: bool, *, timeout: float = 120) -> dict[str, Any]:
    """A thumbs up or down on the canvas Kosika Brain kept for `text`: up
    keeps it to give again, down means it is never reused. The answer holds
    rated and said."""

    text = text.strip()
    if not text:
        raise DesignsError("Say which ask to rate.")
    if not _brain_on():
        raise DesignsError("Kosika Brain is off here (KOSIKA_BRAIN=0), so there is nothing kept to rate.")
    return _run(BRAIN, "brain_rate", {"r": {"ask": text, "liked": bool(liked)}}, extra=_brain_memory(), first="rated", timeout=timeout)


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
    if done.returncode:
        _keep_failure(done.stdout + "\n" + done.stderr)
    return _result(done.stdout + ("\n" + done.stderr if done.returncode else ""), first)


def _keep_failure(printed: str) -> None:
    # The engine's whole output, kept where a person can read it: a crash
    # names its cause in its first lines, which the error message cuts.
    try:
        kept = Path.home() / ".kosika" / "designs-last-failure.txt"
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(printed, encoding="utf-8")
    except OSError:
        pass
    crash = next((line for line in printed.splitlines() if line.startswith("panic:")), "")
    if crash:
        raise DesignsError(f"The engine crashed: {crash} (the whole output is in ~/.kosika/designs-last-failure.txt)")


def gather(topic: str, *, timeout: float = 180) -> dict[str, Any]:
    """Have Kosika Brain read arXiv's search for `topic` and keep each paper
    as a fact with its source; later canvases on it show what it gathered.
    The answer holds kept, titles and said."""

    topic = topic.strip()
    if not topic:
        raise DesignsError("Say what to read up on.")
    if not _brain_on():
        raise DesignsError("Kosika Brain is off here (KOSIKA_BRAIN=0), so there is nowhere to keep what it reads.")
    extra = _brain_memory() + ["--allow-site", "export.arxiv.org"]
    return _run(BRAIN, "brain_gather", {"g": {"topic": topic}}, extra=extra, first="kept", timeout=timeout)


CREW = Path(__file__).resolve().parent.parent / "examples" / "agents" / "crew" / "main.kos"


def crew(topic: str, *, palette: str = "", timeout: float = 1500) -> dict[str, Any]:
    """Have a Kosika crew, run by Kosika Brain, make a study pack on `topic`:
    the Conductor plans, the Scientist works out what is exact, the Designer
    draws the canvas, the Tutor writes notes and the Examiner marks them,
    each checked by Kosika, the crew at work played on a Kosika canvas.
    The answer holds title, page, board and problems."""

    topic = topic.strip()
    if not topic:
        raise DesignsError("Say what the pack is about.")
    if not CREW.is_file():
        raise DesignsError(f"The crew is not in this copy of Kosika ({CREW}).")
    return _run(CREW, "pack", {"ask": {"topic": topic, "palette": palette}}, extra=_brain_memory(), first="title", timeout=timeout)


KOSI = Path(__file__).resolve().parent.parent / "examples" / "agents" / "kosi" / "main.kos"
GROQ_AUDIO = "https://api.groq.com/openai/v1/audio"


def kosi(text: str, *, timeout: float = 600) -> dict[str, Any]:
    """Say something to Kosi, the companion run by Kosika Brain. The answer
    holds text, suggest, canvas (a Kosika canvas or ""), kept and project."""

    text = text.strip()
    if not text:
        raise DesignsError("Say something to Kosi.")
    if not KOSI.is_file():
        raise DesignsError(f"Kosi is not in this copy of Kosika ({KOSI}).")
    return _run(KOSI, "chat", {"msg": {"text": text}}, extra=_brain_memory(), first="text", timeout=timeout)


def _groq_key() -> str:
    return os.environ.get("GROQ_API_KEY") or os.environ.get("KOSIKA_MODEL_API_KEY", "")


def hear(audio: bytes, mime: str = "audio/webm") -> str:
    """What was said in a recording, by Groq's free Whisper. The recording
    goes to Groq to be transcribed and nowhere else."""

    import urllib.request
    import uuid

    if not audio:
        raise DesignsError("Nothing was recorded.")
    key = _groq_key()
    if not key:
        raise DesignsError("Listening needs GROQ_API_KEY for Whisper.")
    ending = "wav" if "wav" in mime else "ogg" if "ogg" in mime else "mp4" if "mp4" in mime else "webm"
    edge = uuid.uuid4().hex
    body = (f"--{edge}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-large-v3-turbo\r\n"
            f"--{edge}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"said.{ending}\"\r\nContent-Type: {mime}\r\n\r\n").encode() + audio + f"\r\n--{edge}--\r\n".encode()
    asked = urllib.request.Request(GROQ_AUDIO + "/transcriptions", data=body, headers={"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={edge}", "User-Agent": "Kosika"})
    try:
        with urllib.request.urlopen(asked, timeout=60) as reply:  # noqa: S310 - Groq's own API
            return str(json.loads(reply.read()).get("text", "")).strip()
    except OSError as exc:
        raise DesignsError(f"Kosi could not hear that: {exc}") from exc


KOKORO_VOICES = ("bm_george", "bm_lewis", "bm_daniel", "bm_fable", "bf_emma", "bf_isabella")
_kokoro = None


def _speak_kokoro(text: str, voice: str = "") -> bytes:
    # Kokoro (Apache 2.0) on this computer, with no app and no network: the
    # kokoro-onnx package and its model in ~/.kosika/voice (KOSI_KOKORO_DIR).
    global _kokoro
    try:
        import io

        import soundfile
        from kokoro_onnx import Kokoro
    except ImportError:
        return b""
    home = Path(os.environ.get("KOSI_KOKORO_DIR", str(Path.home() / ".kosika" / "voice")))
    model = next((home / name for name in ("kokoro-v1.0.int8.onnx", "kokoro-v1.0.onnx") if (home / name).is_file()), None)
    if model is None or not (home / "voices-v1.0.bin").is_file():
        return b""
    if _kokoro is None:
        _kokoro = Kokoro(str(model), str(home / "voices-v1.0.bin"))
    chosen = voice if voice in KOKORO_VOICES else os.environ.get("KOSI_KOKORO_VOICE", "bm_george")
    samples, rate = _kokoro.create(text[:1500], voice=chosen, speed=float(os.environ.get("KOSI_VOICE_SPEED", "0.95")), lang="en-gb" if chosen.startswith("b") else "en-us")
    out = io.BytesIO()
    soundfile.write(out, samples, rate, format="WAV")
    return out.getvalue()


def speak(text: str, voice: str = "") -> bytes:
    """Kosi's voice for `text`, as WAV: first Kokoro on this computer, a calm
    British voice ("bm_george" by default, KOSI_KOKORO_VOICE), free for any
    use; then VoiceStudio on this computer,
    in a voice designed for Kosi (calm, middle-aged, low, British by default:
    KOSI_VOICE_STYLE), then Groq's free Orpheus (KOSI_VOICE, "dan" by
    default), else b"" and Studio speaks with the browser's best voice.
    VoiceStudio's OmniVoice and Groq's Orpheus each ask their owner to
    accept a licence once before they speak."""

    if not text.strip():
        return b""
    said = _speak_kokoro(text, voice) or _speak_voicestudio(text) or _speak_orpheus(text, "" if voice in KOKORO_VOICES else voice)
    return said


def _post_audio(url: str, body: dict, headers: dict, timeout: float) -> bytes:
    import urllib.request

    asked = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "User-Agent": "Kosika", **headers})
    try:
        with urllib.request.urlopen(asked, timeout=timeout) as reply:  # noqa: S310 - a speech server the person runs or Groq's own API
            return reply.read()
    except OSError:
        return b""


def _speak_voicestudio(text: str) -> bytes:
    url = os.environ.get("KOSI_VOICESTUDIO_URL", "http://127.0.0.1:3900/v1")
    if not url:
        return b""
    body = {"model": "tts-1", "voice": os.environ.get("KOSI_VOICESTUDIO_VOICE", "alloy"), "input": text[:1500], "response_format": "wav",
            "instructions": os.environ.get("KOSI_VOICE_STYLE", "male, middle-aged, low pitch, british accent")}
    return _post_audio(url.rstrip("/") + "/audio/speech", body, {}, 120)


def _speak_orpheus(text: str, voice: str = "") -> bytes:
    key = _groq_key()
    if not key:
        return b""
    body = {"model": "canopylabs/orpheus-v1-english", "voice": voice or os.environ.get("KOSI_VOICE", "dan"), "input": text[:1500], "response_format": "wav"}
    return _post_audio(GROQ_AUDIO + "/speech", body, {"Authorization": f"Bearer {key}"}, 60)


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
