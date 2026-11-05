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
    built = _built_native()
    if built:
        return [built]
    return [sys.executable, "-m", "kosika.cli"]


def _built_native() -> str:
    """Kosika's Go engine, built from this copy of Kosika into ~/.kosika/bin
    when Go is installed and the build is older than the source. It runs the
    same programs ten to seventeen times faster than the Python engine. ""
    when there is no Go and no build, and the Python engine is used."""

    import shutil

    source = Path(__file__).resolve().parent.parent / "native"
    if not (source / "main.go").is_file():
        return ""
    target = Path.home() / ".kosika" / "bin" / ("kosika-native.exe" if os.name == "nt" else "kosika-native")
    try:
        newest = max(p.stat().st_mtime for p in [*source.glob("*.go"), *source.glob("std/*.kos")])
    except ValueError:
        return ""
    if target.is_file() and target.stat().st_mtime >= newest:
        return str(target)
    go = shutil.which("go")
    if not go:
        return str(target) if target.is_file() else ""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([go, "build", "-o", str(target), "."], cwd=source, capture_output=True, timeout=300, check=True)  # noqa: S603 - our own source
    except (OSError, subprocess.SubprocessError):
        return str(target) if target.is_file() else ""
    return str(target)


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


def _key_for(url: str, env: dict[str, str]) -> str:
    """The key for the model service at url: the service's own key when this
    computer has one (GROQ_API_KEY for Groq, OPENROUTER_API_KEY for
    OpenRouter), else KOSIKA_DESIGNS_API_KEY, else KOSIKA_MODEL_API_KEY.

    A general KOSIKA_MODEL_API_KEY may be another service's key (a Gemini key,
    say); sent to Groq it is refused, and every model step comes back empty,
    which Designs showed as a drawing with no title and no parts.
    """

    host = url.lower()
    own = ""
    if "groq.com" in host:
        own = env.get("GROQ_API_KEY", "")
    elif "openrouter.ai" in host:
        own = env.get("OPENROUTER_API_KEY", "")
    elif "127.0.0.1" in host or "localhost" in host:
        own = env.get("KOSIKA_DESIGNS_API_KEY", "") or "local"
    return own or env.get("KOSIKA_DESIGNS_API_KEY", "") or env.get("KOSIKA_MODEL_API_KEY", "")


#: The models Kosika knows, and how they compare: thinking and speed are
#: rough marks out of 100 from the hard test and its timings, for the
#: Models page and for choosing.
CLOUD_MODELS = (
    {"id": "openai/gpt-oss-120b", "name": "gpt-oss 120B", "where": "Groq · cloud · free daily allowance", "url": DEFAULT_MODEL_URL, "thinking": 92, "speed": 70},
    {"id": "openai/gpt-oss-20b", "name": "gpt-oss 20B", "where": "Groq · cloud · free daily allowance", "url": DEFAULT_MODEL_URL, "thinking": 74, "speed": 90},
)
LOCAL_MARKS = {"1.7b": (38, 30), "4b": (55, 15), "8b": (66, 8), "7b": (64, 9), "3b": (50, 20), "1b": (25, 40)}

#: The kinds of work, what each needs, and the order Kosika tries models in
#: when it chooses: the quick one first for talk, the strong one first for
#: drawing and research. A model on this laptop is always the last resort.
TASKS = (
    {"task": "talk", "label": "Talk", "needs": "a quick reply", "prefer": ("openai/gpt-oss-20b", "openai/gpt-oss-120b")},
    {"task": "designs", "label": "Designs", "needs": "a careful structure", "prefer": ("openai/gpt-oss-120b", "openai/gpt-oss-20b")},
    {"task": "research", "label": "Research", "needs": "reads and weighs papers", "prefer": ("openai/gpt-oss-120b", "openai/gpt-oss-20b")},
    {"task": "offline", "label": "Offline", "needs": "no internet, or limits used", "prefer": ()},
)


def _models_file() -> Path:
    return Path.home() / ".kosika" / "models.json"


def model_choices() -> dict[str, Any]:
    """The person's choices on the Models page: whether Kosika chooses, and
    any model pinned for a kind of work."""

    try:
        saved = json.loads(_models_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    pinned = saved.get("pinned") if isinstance(saved.get("pinned"), dict) else {}
    return {"auto": saved.get("auto", True) is not False, "pinned": {str(k): str(v) for k, v in pinned.items() if v}}


def choose_model(task: str, model: str) -> dict[str, Any]:
    """Pin a model for a kind of work, or "" to let Kosika choose."""

    if task not in {t["task"] for t in TASKS}:
        raise DesignsError(f"There is no kind of work called {task}.")
    choices = model_choices()
    if model:
        choices["pinned"][task] = model
    else:
        choices["pinned"].pop(task, None)
    _models_file().parent.mkdir(parents=True, exist_ok=True)
    _models_file().write_text(json.dumps(choices, indent=1), encoding="utf-8")
    return models_status()


def _local_models(url: str = "http://127.0.0.1:11434") -> list[str]:
    import urllib.request

    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=3) as answer:  # noqa: S310 - this computer
            return [str(m.get("name", "")).removesuffix(":latest") for m in json.loads(answer.read().decode("utf-8")).get("models", []) if m.get("name")]
    except (OSError, ValueError):
        return []


def _cloud_ready(env: dict[str, str]) -> set[str]:
    """The Groq models that answer for this computer's key: Groq lists the
    models a key may use, which costs nothing from the daily allowance."""

    import urllib.request

    key = _key_for(DEFAULT_MODEL_URL, env)
    if not key:
        return set()
    request = urllib.request.Request(DEFAULT_MODEL_URL + "/models", headers={"Authorization": "Bearer " + key, "User-Agent": "Kosika/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310 - Groq's own address
            return {str(m.get("id")) for m in json.loads(answer.read().decode("utf-8")).get("data", [])}
    except (OSError, ValueError):
        return set()


def _local_entry(name: str) -> dict[str, Any]:
    size = next((mark for mark in LOCAL_MARKS if mark in name.lower()), "")
    thinking, speed = LOCAL_MARKS.get(size, (45, 20))
    cpu = "cpu" in name.lower()
    return {"id": name, "name": name, "where": "Ollama · this laptop" + (" · CPU only" if cpu else "") + " · offline, no limit", "url": "http://127.0.0.1:11434", "thinking": thinking, "speed": speed, "local": True}


def _chosen(task: dict[str, Any], ready: list[dict[str, Any]], pinned: dict[str, str], auto: bool) -> list[dict[str, Any]]:
    """The models for a kind of work, in the order they are tried."""

    by_id = {m["id"]: m for m in ready}
    order: list[dict[str, Any]] = []
    want = pinned.get(task["task"], "")
    if want in by_id and (want or not auto):
        order.append(by_id[want])
    for model_id in task["prefer"]:
        if model_id in by_id and by_id[model_id] not in order:
            order.append(by_id[model_id])
    # Then two models on this laptop, the ones that suit a CPU first.
    for m in sorted((m for m in ready if m.get("local") and _talks(m["id"])), key=_local_rank)[:2]:
        if m not in order:
            order.append(m)
    return order


#: The laptop models that suit a CPU best, in order: the small brain was
#: measured on kosika-cpu-1.7b (35 of 41 on the hard test).
LOCAL_PREFER = ("kosika-cpu-1.7b", "qwen2.5:3b", "qwen2.5:1.5b", "qwen3:1.7b", "gemma3:1b", "kosika-cpu-4b", "gemma3:4b", "qwen2.5:7b")


def _talks(name: str) -> bool:
    """Whether a laptop model can answer in words: not one that only makes
    embeddings, writes code, or thinks aloud for minutes before it answers."""

    low = name.lower()
    return not any(mark in low for mark in ("embed", "coder", "deepseek-r1", "3d-"))


def _local_rank(m: dict[str, Any]) -> tuple[int, int]:
    return (LOCAL_PREFER.index(m["id"]) if m["id"] in LOCAL_PREFER else len(LOCAL_PREFER), -m["thinking"])


def models_status() -> dict[str, Any]:
    """Every model Kosika can reach, whether it answers now, and the one it
    would use for each kind of work, for the Models page."""

    env = dict(os.environ)
    cloud_ids = _cloud_ready(env)
    models: list[dict[str, Any]] = []
    for m in CLOUD_MODELS:
        models.append({**m, "local": False, "ready": m["id"] in cloud_ids, "state": "ready" if m["id"] in cloud_ids else ("no key" if not _key_for(DEFAULT_MODEL_URL, env) else "not answering")})
    local_names = _local_models()
    for name in local_names:
        models.append({**_local_entry(name), "ready": True, "state": "on this laptop"})
    if not local_names:
        models.append({**_local_entry(os.environ.get("KOSI_LOCAL_MODEL", "kosika-cpu-1.7b")), "ready": False, "state": "Ollama is not running"})
    choices = model_choices()
    ready = [m for m in models if m["ready"]]
    tasks = []
    for t in TASKS:
        order = _chosen(t, ready, choices["pinned"], choices["auto"])
        if t["task"] == "offline":
            order = [m for m in order if m.get("local")]
        tasks.append({"task": t["task"], "label": t["label"], "needs": t["needs"], "chosen": order[0]["id"] if order else "", "then": [m["id"] for m in order[1:3]], "pinned": choices["pinned"].get(t["task"], "")})
        for m in models:
            if order and m["id"] == order[0]["id"]:
                m.setdefault("uses", []).append(t["label"])
    return {"auto": choices["auto"], "models": models, "tasks": tasks}


def _models(env: dict[str, str], task: str = "designs") -> list[dict[str, str]]:
    """The models to try for a kind of work, in order, as engine settings:
    the model pinned on the Models page, else Kosika's choice for the task,
    the laptop's model last. A model named in KOSIKA_DESIGNS_MODEL is used
    alone."""

    if env.get("KOSIKA_DESIGNS_MODEL"):
        return [{}]
    plan = next((t for t in TASKS if t["task"] == task), TASKS[1])
    ready: list[dict[str, Any]] = []
    if _key_for(DEFAULT_MODEL_URL, env):
        ready.extend({**m, "local": False} for m in CLOUD_MODELS)
    local_names = _local_models()
    if not local_names and _start_ollama():
        local_names = _local_models()
    ready.extend(_local_entry(n) for n in local_names)
    choices = model_choices()
    chain: list[dict[str, str]] = []
    for m in _chosen(plan, ready, choices["pinned"], choices["auto"]):
        if m.get("local"):
            chain.append({"KOSIKA_DESIGNS_MODEL": m["id"], "KOSIKA_DESIGNS_MODEL_URL": m["url"], "KOSIKA_DESIGNS_MODEL_PROTOCOL": "ollama", "KOSIKA_MODEL_THINK": "off", "KOSIKA_DESIGNS_MAX_TOKENS": "1500"})
        else:
            chain.append({"KOSIKA_DESIGNS_MODEL": m["id"], "KOSIKA_DESIGNS_MODEL_URL": m["url"]})
    return chain or [{}]


def _start_ollama(wait: float = 12.0) -> bool:
    """Start Ollama on this computer when it is installed but not running,
    so the model on this laptop is there when the cloud ones are not.
    Whether it now answers."""

    import shutil
    import time
    import urllib.request

    def answers() -> bool:
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2):  # noqa: S310 - this computer
                return True
        except OSError:
            return False

    if answers():
        return True
    found = shutil.which("ollama")
    if not found and os.environ.get("LOCALAPPDATA"):
        guess = Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Ollama" / "ollama.exe"
        found = str(guess) if guess.is_file() else None
    if not found:
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen([found, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)  # noqa: S603 - the person's own Ollama
    except OSError:
        return False
    end = time.time() + wait
    while time.time() < end:
        if answers():
            return True
        time.sleep(0.5)
    return False


# What a model that could not answer leaves behind: the service refused or
# was out of its allowance, or every answer came back empty.
_UNANSWERED = ("Workflow stopped (provider)", "HTTP 401", "HTTP 403", "HTTP 429", "rate limit", "Rate limit", "quota")


def _unanswered(said: dict[str, Any] | None, error: str) -> bool:
    if error:
        return any(mark in error for mark in _UNANSWERED)
    text = str((said or {}).get("text", ""))
    if text.startswith(("Sorry, I lost my words", "I looked, but I couldn't put an answer together", "That one's got away from me")):
        return True
    problems = (said or {}).get("problems") or []
    return any(p == "give a title" or p.startswith("0 parts") or p.startswith("0 figures") or p.startswith("no story was written") for p in problems)


def _run(source: Path, workflow: str, given_input: dict, *, extra: list[str] | None = None, first: str = "kind", timeout: float = 600, env_more: dict[str, str] | None = None, task: str = "designs") -> dict[str, Any]:
    env = dict(os.environ)
    env.update(env_more or {})
    chain = _models(env, task)
    said: dict[str, Any] | None = None
    failed: DesignsError | None = None
    for k, model in enumerate(chain):
        error = ""
        try:
            said = _run_once(source, workflow, given_input, {**env, **model}, extra, first, timeout)
            failed = None
        except DesignsError as exc:
            error, failed, said = str(exc), exc, None
        if _too_large(said, error):
            # Groq's free tier counts the room asked for the answer against
            # its tokens a minute: asked for 8,000, a call is refused outright
            # (HTTP 413) and every ask fell to the slow laptop models. The
            # same model again, with room for a shorter answer.
            try:
                said = _run_once(source, workflow, given_input, {**env, **model, "KOSIKA_DESIGNS_MAX_TOKENS": "3500"}, extra, first, timeout)
                error, failed = "", None
            except DesignsError as exc:
                error, failed, said = str(exc), exc, None
        if not _unanswered(said, error) or k == len(chain) - 1:
            break
    if failed is not None:
        raise failed
    assert said is not None
    return said


SWARM = Path(__file__).resolve().parent / "std" / "designs_swarm.kos"


def swarm_board(states: list, pins: list, timeout: float = 60) -> dict[str, Any]:
    """The Swarm page's board, drawn by Kosika (std/designs_swarm.kos): the
    friends as Designs' figures, the work moving along wires, and the cards
    each one pinned. No model is asked."""

    board = {
        "states": [{"id": str(s.get("id", "")), "state": str(s.get("state", ""))} for s in states if isinstance(s, dict)][:20],
        "pins": [
            {key: str(p.get(key, ""))[:600] for key in ("who", "kind", "head", "text", "source")}
            for p in pins if isinstance(p, dict)
        ][:12],
    }
    return _run_once(SWARM, "swarm_board", {"b": board}, dict(os.environ), None, "svg", timeout)


def _too_large(said: dict[str, Any] | None, error: str) -> bool:
    text = error + (json.dumps(said)[:4000] if said else "")
    return "HTTP 413" in text or "Request too large" in text


def _run_once(source: Path, workflow: str, given_input: dict, env: dict[str, str], extra: list[str] | None, first: str, timeout: float) -> dict[str, Any]:
    env = dict(env)
    env["KOSIKA_MODEL_API_KEY"] = _key_for(env.get("KOSIKA_DESIGNS_MODEL_URL", DEFAULT_MODEL_URL), env)
    with tempfile.TemporaryDirectory() as work:
        given = Path(work) / "ask.json"
        given.write_text(json.dumps(given_input), encoding="utf-8")
        command = _engine() + [
            "run", str(source), "--workflow", workflow, "--input", str(given),
            "--model", env.get("KOSIKA_DESIGNS_MODEL", DEFAULT_MODEL),
            "--model-url", env.get("KOSIKA_DESIGNS_MODEL_URL", DEFAULT_MODEL_URL),
            "--model-protocol", env.get("KOSIKA_DESIGNS_MODEL_PROTOCOL", "openai"),
            "--model-timeout", env.get("KOSIKA_DESIGNS_MODEL_TIMEOUT", "400"),
            "--max-output-tokens", env.get("KOSIKA_DESIGNS_MAX_TOKENS", "8000"),
            # A canvas works out its layout, storyboard and pins in pure
            # Kosika functions; the 10,000 default stops a big one partway
            # ("pure function call budget exceeds 10000"). The Brain path
            # already asks for 100,000 for the same reason.
            "--max-function-calls", env.get("KOSIKA_DESIGNS_CALLS", "100000"),
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
    said = _result(done.stdout + ("\n" + done.stderr if done.returncode else ""), first)
    said.setdefault("model", env.get("KOSIKA_DESIGNS_MODEL", DEFAULT_MODEL))
    return said


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


#: The small model Kosi talks through on this laptop, made by Ollama from
#: qwen3:1.7b with the graphics card left out (PARAMETER num_gpu 0).
KOSI_LOCAL_MODEL = "kosika-cpu-1.7b"
KOSI_LOCAL_URL = "http://127.0.0.1:11434"


def _local_ready(model: str, url: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=3) as answer:  # noqa: S310 - this computer
            names = [m.get("name", "") for m in json.loads(answer.read().decode("utf-8")).get("models", [])]
    except (OSError, ValueError):
        return False
    return any(n == model or n == model + ":latest" for n in names)


def _kosi_task(text: str) -> str:
    """The kind of work a message to Kosi is: research for Iris; drawing
    when a picture is asked for or something is to be explained, which wants
    the strong model, as a drawing's structure does; else talk, which wants
    the quick one."""

    low = " " + text.lower().strip() + " "
    if low.lstrip().startswith(("iris", "ask iris", "hey iris")):
        return "research"
    if any(word in low for word in (" draw", " diagram", " picture", " sketch", " visual", " show me ", " canvas", " sol")):
        return "designs"
    return "talk"


def kosi(text: str, *, local: bool = False, timeout: float = 600) -> dict[str, Any]:
    """Say something to Kosi, the companion run by Kosika Brain. The answer
    holds text, suggest, canvas (a Kosika canvas or ""), kept and project.

    local talks on this laptop alone: the small brain on a small model in
    Ollama, on the CPU, with no cloud model and no daily limit."""

    text = text.strip()
    if not text:
        raise DesignsError("Say something to Kosi.")
    if not KOSI.is_file():
        raise DesignsError(f"Kosi is not in this copy of Kosika ({KOSI}).")
    env_more: dict[str, str] = {}
    if local:
        model = os.environ.get("KOSI_LOCAL_MODEL", KOSI_LOCAL_MODEL)
        url = os.environ.get("KOSI_LOCAL_URL", KOSI_LOCAL_URL)
        if not _local_ready(model, url):
            raise DesignsError(
                f"Kosi on this laptop needs Ollama running with the model {model}. "
                "Make it once with: ollama pull qwen3:1.7b, then ollama create "
                f"{model} from a Modelfile holding 'FROM qwen3:1.7b' and 'PARAMETER num_gpu 0'."
            )
        env_more = {
            "KOSIKA_DESIGNS_MODEL": model, "KOSIKA_DESIGNS_MODEL_URL": url,
            "KOSIKA_DESIGNS_MODEL_PROTOCOL": "ollama", "KOSIKA_MODEL_THINK": "off",
            "KOSIKA_DESIGNS_MAX_TOKENS": "400", "KOSIKA_MODEL_API_KEY": "ollama",
        }
    # The small brain, for a small model on a laptop's CPU: a shorter ask,
    # and Kosika's own exact working all the same. KOSI_BRAIN=small asks for
    # it with any model.
    small = local or os.environ.get("KOSI_BRAIN", "").strip().lower() == "small"
    # The talk goes to Kosi, or to the friend it is for: Iris the researcher
    # or Sol the scientist ("Iris, what's new on ...?"). The answer says who.
    workflow = "talk_small" if small else "talk"
    # Kosi and Iris read Wikipedia to check what a question names, and Iris
    # reads arXiv for papers: those two sites only.
    extra = _brain_memory() + ["--allow-site", "en.wikipedia.org", "--allow-site", "export.arxiv.org"]
    said = _run(KOSI, workflow, {"msg": {"text": text}}, extra=extra, first="who", timeout=timeout, env_more=env_more, task=_kosi_task(text))
    said["local"] = local
    return said

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
    # A line said once is kept, so an animation narrated again (or a caption
    # Kosika tells in every water cycle) is spoken at once: on a laptop's
    # CPU each new line takes seconds.
    import hashlib

    kept = Path.home() / ".kosika" / "voice" / "said" / (hashlib.sha256(f"{voice}|{text}".encode()).hexdigest()[:24] + ".wav")
    try:
        return kept.read_bytes()
    except OSError:
        pass
    said = _speak_kokoro(text, voice) or _speak_voicestudio(text) or _speak_orpheus(text, "" if voice in KOKORO_VOICES else voice)
    if said:
        try:
            kept.parent.mkdir(parents=True, exist_ok=True)
            kept.write_bytes(said)
        except OSError:
            pass
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
