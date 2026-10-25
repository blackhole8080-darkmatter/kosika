"""Services: Kosika agents that keep running, each with only what it was given.

A services file lists them. Each `service` names its program, what it is
told each time it runs, how often, and exactly what it may reach:

    service lookout {
      program "examples/agents/lookout/main.kos"
      say "check"
      every 15 minutes
      allow site "github.com"
      memory "lookout/memory"
      notify from "digest/topic.txt"
    }

    service gatherer {
      program "examples/agents/gatherer/main.kos"
      allow site "news.google.com"
      allow site "en.wikipedia.org"
      model "qwen/qwen3.8-27b" at "https://api.groq.com/openai/v1" key "GROQ_API_KEY"
    }

The runner reads the file and starts each service that has an `every` when
it falls due, as `kosika run` with exactly the grants written there and no
others: the sites, the one folder, the memory folder, the phone. A service
with no `every` runs only when asked (`once`). Grants are written out in
the file the owner keeps, so what each service may touch can be read in
one place, and a service cannot widen it.

What may be granted:

* `allow site "HOST"`, any number: pages it may read (see web.py).
* `allow folder "PATH"`, at most one: a folder it may look in. Writing in
  it still needs the owner's yes at each write, which a service that runs
  by itself never has, so a service can only read there.
* `memory "PATH"`: the folder its memories are kept in.
* `notify from "PATH"`: a file holding the one address notes go to, or an
  ntfy topic name. The file, not the services file, holds it, so the
  services file can be shown without giving the address away.
* `model "NAME" at "URL" key "VARIABLE"`: the model that answers, and the
  name of the environment variable that holds its key. The key itself is
  never in the file.

Paths are read from the services file's own folder. Nothing here starts a
program other than Kosika itself, and the shell is never granted.

    python -m kosika.services run FILE       keep every service running
    python -m kosika.services status FILE    what each may touch, and how it last went
    python -m kosika.services once FILE NAME [TEXT]
                                             run one service now
    python -m kosika.services pause FILE NAME / resume FILE NAME

What the runner knows is kept beside the file, in `FILE.state.json`: when
each service last ran, whether it worked, and what it said. The same is
written for people, and for agents that read plain text, in
`FILE.status.txt`. Which services are paused is the plain list in
`FILE.paused.txt`, one name a line, so a Kosika agent that may write that
one file can pause and resume services, with the owner's yes each time.

Each run also keeps its evidence, in `FILE.runs/<service>/`: the run's
trace as `<time>.jsonl`, which `kosika replay` reads, and its journal as
`<time>.journal`, which is what an approval pause would resume from. The
newest `KEPT_RUNS` runs of a service are kept and the rest are deleted, so
a service left running for a year does not fill a drive. `status` names
the last trace, so a run nobody watched can still be replayed afterwards.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .environment import _Reader
from .lexer import lex

KOSIKA_ROOT = Path(__file__).resolve().parent.parent
#: The longest one run of a service may take.
RUN_SECONDS = 600
#: How many finished runs of each service keep their trace and journal.
KEPT_RUNS = 20
#: What a service's last answer is cut to when kept.
KEPT_CHARACTERS = 2000
UNITS = {"minute": 60, "minutes": 60, "hour": 3600, "hours": 3600, "day": 86400, "days": 86400}


class ServicesError(ValueError):
    """A services file that cannot be read, or a service not in it."""


@dataclass(frozen=True)
class ModelGrant:
    name: str
    url: str | None
    key: str | None


@dataclass
class Service:
    name: str
    program: Path
    say: str = ""
    every: int | None = None
    sites: list[str] = field(default_factory=list)
    folder: Path | None = None
    memory: Path | None = None
    notify: Path | None = None
    model: ModelGrant | None = None

    def touches(self) -> list[str]:
        """What it may reach, in words, for a person to read."""

        said: list[str] = [f"reads {site}" for site in self.sites]
        if self.folder is not None:
            said.append(f"looks in {self.folder}")
        if self.memory is not None:
            said.append("keeps its own memory")
        if self.notify is not None:
            said.append("sends notes to your phone")
        if self.model is not None:
            said.append(f"asks the model {self.model.name}")
        return said or ["nothing outside itself"]


def read_services(path: Path) -> dict[str, Service]:
    """Every service in a services file."""

    try:
        source = path.read_text(encoding="utf-8")
        tokens = lex(source)
    except (OSError, ValueError) as exc:
        raise ServicesError(f"Cannot read {path}: {exc}") from exc
    reader = _Reader(tokens, path)
    folder = path.parent.resolve()
    found: dict[str, Service] = {}
    try:
        while reader.current.kind != "EOF":
            reader.expect("service")
            name = reader.name()
            if name in found:
                reader.fail(f"Service {name!r} is listed twice")
            reader.expect("{")
            program: Path | None = None
            service = Service(name, Path())
            while not reader.accept("}"):
                token = reader.current
                if reader.accept("program"):
                    program = (folder / reader.text()).resolve()
                elif reader.accept("say"):
                    service.say = reader.text()
                elif reader.accept("every"):
                    amount = reader.number()
                    unit = reader.name()
                    if unit not in UNITS or not (type(amount) is int and amount >= 1):
                        reader.fail(f"Service {name!r}: every needs a whole number of minutes, hours or days")
                    service.every = amount * UNITS[unit]  # type: ignore[assignment]
                    if service.every is not None and service.every < 60:  # type: ignore[operator]
                        reader.fail(f"Service {name!r} may run at most once a minute")
                elif reader.accept("allow"):
                    if reader.accept("site"):
                        service.sites.append(reader.text())
                    elif reader.accept("folder"):
                        if service.folder is not None:
                            reader.fail(f"Service {name!r} allows a second folder; it may allow one")
                        service.folder = (folder / reader.text()).resolve()
                    else:
                        reader.fail(f"Service {name!r}: a service may be allowed a site or a folder, never the shell")
                elif reader.accept("memory"):
                    service.memory = (folder / reader.text()).resolve()
                elif reader.accept("notify"):
                    reader.expect("from")
                    service.notify = (folder / reader.text()).resolve()
                elif reader.accept("model"):
                    model = reader.text()
                    url = reader.text() if reader.accept("at") else None
                    key = reader.text() if reader.accept("key") else None
                    service.model = ModelGrant(model, url, key)
                else:
                    reader.fail(
                        f"Unexpected {token.value!r} at {token.line}:{token.column} in service "
                        f"{name!r}; a service has program, say, every, allow, memory, notify and model"
                    )
            if program is None:
                reader.fail(f"Service {name!r} names no program")
            service.program = program  # type: ignore[assignment]
            found[name] = service
    except ValueError as exc:
        raise ServicesError(str(exc)) from exc
    return found


def notify_address(service: Service) -> str | None:
    """The one address a service's notes go to, from its file: a whole
    https address, or an ntfy topic name."""

    if service.notify is None:
        return None
    try:
        written = service.notify.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ServicesError(f"Service {service.name!r} cannot read {service.notify}: {exc}") from exc
    if not written:
        raise ServicesError(f"Service {service.name!r}: {service.notify} is empty")
    return written if written.startswith("https://") else f"https://ntfy.sh/{written}"


def binding_id(service: Service) -> str:
    """The identity a run's journal is tied to: this service's name, program
    and grants, and nothing else. Change a service and its next journal is a
    different one, which is what durable execution asks for."""

    parts = [f"name={service.name}", f"program={service.program}"]
    parts += [f"site={site}" for site in service.sites]
    if service.folder is not None:
        parts.append(f"folder={service.folder}")
    if service.memory is not None:
        parts.append(f"memory={service.memory}")
    if service.notify is not None:
        parts.append(f"notify={service.notify}")
    if service.model is not None:
        parts.append(f"model={service.model.name}@{service.model.url}")
    return "service:" + hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def runs_folder(path: Path) -> Path:
    """Where every service's runs are kept, beside the services file."""

    return path.with_name(path.name + ".runs")


def run_folder(path: Path, name: str) -> Path:
    """Where one service's runs are kept."""

    return runs_folder(path) / name


def _run_stem(folder: Path, started: datetime) -> str:
    """A name for this run's files that no earlier run of the service holds."""

    stem = started.strftime("%Y%m%dT%H%M%S")
    taken = stem
    count = 1
    while any(folder.glob(f"{taken}.*")):
        count += 1
        taken = f"{stem}-{count}"
    return taken


def command_for(
    service: Service,
    inputs: Path,
    trace: Path | None = None,
    journal: Path | None = None,
) -> list[str]:
    """`kosika run` with the service's grants and no others, keeping this
    run's trace and journal where the runner can show and replay them."""

    command = [sys.executable, "-m", "kosika.cli", "run", str(service.program), "--input", str(inputs)]
    if trace is not None:
        command += ["--trace", str(trace)]
    if journal is not None:
        command += ["--journal", str(journal), "--binding-id", binding_id(service)]
    for site in service.sites:
        command += ["--allow-site", site]
    if service.folder is not None:
        command += ["--allow-folder", str(service.folder)]
    if service.memory is not None:
        service.memory.mkdir(parents=True, exist_ok=True)
        command += ["--memory", str(service.memory)]
    address = notify_address(service)
    if address is not None:
        command += ["--notify", address]
    if service.model is not None:
        command += ["--model", service.model.name]
        if service.model.url:
            command += ["--model-url", service.model.url]
    return command


def environment_for(service: Service) -> dict[str, str]:
    """The run's environment: this one, with the model's key under the name
    Kosika reads, and every other model key left out."""

    env = {name: value for name, value in os.environ.items() if not name.endswith("_API_KEY")}
    env["PYTHONPATH"] = str(KOSIKA_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    if service.model is not None and service.model.key:
        key = os.environ.get(service.model.key) or _user_variable(service.model.key)
        if not key:
            raise ServicesError(f"Service {service.name!r}: no key in {service.model.key}")
        env["KOSIKA_MODEL_API_KEY"] = key
    return env


def _user_variable(name: str) -> str | None:
    """A Windows user variable set with setx after this process began."""

    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return None


def answer_of(stdout: str) -> str:
    """What the run said: the text of the record `kosika run` printed."""

    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                value = ast.literal_eval(line)
            except (ValueError, SyntaxError):
                continue
            if isinstance(value, dict):
                return str(value.get("text", value))
    return ""


Runner = Callable[[list[str], dict[str, str], Path], subprocess.CompletedProcess]


def _run(command: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(
        command, env=env, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=RUN_SECONDS, creationflags=flags,
    )


def prune_runs(folder: Path, keep: int | None = None) -> list[str]:
    """Delete all but the newest `keep` runs in one service's folder. A run is
    a trace and every file sharing its name up to the first dot: the journal,
    the journal's lock, an approval ledger. Files of a run that never wrote a
    trace are debris, and go before any run that has evidence. Returns the
    names removed."""

    keep = KEPT_RUNS if keep is None else keep
    if keep < 0 or not folder.is_dir():
        return []
    runs: dict[str, list[Path]] = {}
    traced: set[str] = set()
    for file in folder.iterdir():
        if not file.is_file():
            continue
        stem = file.name.split(".")[0]
        runs.setdefault(stem, []).append(file)
        if file.suffix == ".jsonl":
            traced.add(stem)
    # A run is a stem with a trace. Anything else is what a run that never
    # reached the program left behind, and goes first.
    removed: list[str] = []
    order = sorted(traced)
    for stem in [each for each in runs if each not in traced]:
        _drop(runs[stem])
        removed.append(stem)
    for stem in order[:-keep] if keep else order:
        _drop(runs[stem])
        removed.append(stem)
    return removed


def _drop(files: list[Path]) -> None:
    for file in files:
        with contextlib.suppress(OSError):
            file.unlink()


def run_once(
    service: Service,
    said: str | None = None,
    runner: Runner = _run,
    folder: Path | None = None,
) -> dict[str, Any]:
    """Run a service one time, and say how it went. With a `folder`, the run
    keeps its trace and journal there and the oldest runs are dropped."""

    started = datetime.now()
    text = service.say if said is None else said
    trace = journal = None
    if folder is not None:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = None
    if folder is not None:
        stem = _run_stem(folder, started)
        trace = folder / f"{stem}.jsonl"
        journal = folder / f"{stem}.journal"
    with tempfile.TemporaryDirectory() as work:
        inputs = Path(work) / "input.json"
        inputs.write_text(json.dumps({"msg": {"text": text}}), encoding="utf-8")
        try:
            done = runner(command_for(service, inputs, trace, journal), environment_for(service), Path(work))
            ok = done.returncode == 0
            answer = answer_of(done.stdout) if ok else (done.stderr or done.stdout).strip()[-KEPT_CHARACTERS:]
        except subprocess.TimeoutExpired:
            ok, answer = False, f"It took longer than {RUN_SECONDS} seconds and was stopped."
        except ServicesError as exc:
            ok, answer = False, str(exc)
    # A run that never reached the program leaves no trace to point at.
    kept = trace if trace is not None and trace.exists() else None
    if kept is None and journal is not None:
        _drop([journal, journal.with_name(journal.name + ".lock.sqlite")])
    if folder is not None:
        prune_runs(folder)
    return {
        "at": started.isoformat(timespec="seconds"),
        "seconds": round((datetime.now() - started).total_seconds(), 1),
        "ok": ok,
        "said": text,
        "answer": answer[:KEPT_CHARACTERS],
        "trace": str(kept) if kept is not None else None,
    }


def state_path(path: Path) -> Path:
    return path.with_name(path.name + ".state.json")


def read_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(state_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.setdefault("last", {})
    state["paused"] = read_paused(path)
    return state


def paused_path(path: Path) -> Path:
    return path.with_name(path.name + ".paused.txt")


def read_paused(path: Path) -> list[str]:
    try:
        text = paused_path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    return sorted({line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("//")})


def write_state(path: Path, state: dict[str, Any]) -> None:
    kept = {key: value for key, value in state.items() if key != "paused"}
    _write(state_path(path), json.dumps(kept, indent=2, ensure_ascii=False))
    try:
        _write(path.with_name(path.name + ".status.txt"), status_text(path, kept) + "\n")
    except ServicesError:
        pass


def _write(target: Path, text: str) -> None:
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(target)


def due(service: Service, state: dict[str, Any], now: datetime) -> bool:
    if service.every is None or service.name in state["paused"]:
        return False
    last = state["last"].get(service.name)
    if not last:
        return True
    return now >= datetime.fromisoformat(last["at"]) + timedelta(seconds=service.every)


def run_due(path: Path, now: datetime | None = None, runner: Runner = _run) -> list[str]:
    """Run every service that has fallen due, once. The services file is
    read afresh each time, so a change to it is seen at the next pass."""

    services = read_services(path)
    ran: list[str] = []
    for service in services.values():
        state = read_state(path)
        if due(service, state, now or datetime.now()):
            result = run_once(service, runner=runner, folder=run_folder(path, service.name))
            state = read_state(path)
            state["last"][service.name] = result
            state["services"] = sorted(services)
            write_state(path, state)
            ran.append(service.name)
    return ran


def status(path: Path, state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Each service: what it may touch, how often it runs, how it last went."""

    state = read_state(path) if state is None else {**state, "paused": read_paused(path)}
    shown = []
    for service in read_services(path).values():
        last = state["last"].get(service.name)
        shown.append(
            {
                "name": service.name,
                "program": str(service.program),
                "every": service.every,
                "paused": service.name in state["paused"],
                "touches": service.touches(),
                "last": last,
                "trace": (last or {}).get("trace"),
            }
        )
    return shown


def _every_words(seconds: int | None) -> str:
    if seconds is None:
        return "when asked"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds % size == 0:
            count = seconds // size
            return f"every {count} {unit}{'' if count == 1 else 's'}"
    return f"every {seconds} seconds"


def _shown(path: Path, target: str) -> str:
    """A path in words: relative to the services file when it sits under it."""

    try:
        return Path(target).relative_to(path.parent).as_posix()
    except ValueError:
        return Path(target).as_posix()


def status_text(path: Path, state: dict[str, Any] | None = None) -> str:
    lines = []
    for item in status(path, state):
        when = "paused" if item["paused"] else _every_words(item["every"])
        lines.append(f"{item['name']} ({when}): {', '.join(item['touches'])}")
        last = item["last"]
        if last:
            how = "worked" if last["ok"] else "failed"
            first = (last["answer"].splitlines() or [""])[0][:120]
            lines.append(f"  last ran {last['at']}, {how}: {first}")
            trace = last.get("trace")
            if trace:
                lines.append(f"  trace {_shown(path, trace)}")
        else:
            lines.append("  has not run yet")
    return "\n".join(lines)


def set_paused(path: Path, name: str, paused: bool) -> None:
    if name not in read_services(path):
        raise ServicesError(f"There is no service {name!r} in {path}")
    names = set(read_paused(path))
    names.add(name) if paused else names.discard(name)
    _write(paused_path(path), "".join(f"{each}\n" for each in sorted(names)))
    write_state(path, read_state(path))


def keep_running(path: Path, tick: float = 30.0) -> None:
    """Run services as they fall due, until stopped."""

    while True:
        try:
            run_due(path)
        except ServicesError as exc:
            print(exc, file=sys.stderr, flush=True)
        time.sleep(tick)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    usage = "usage: python -m kosika.services run|status|once|pause|resume FILE [NAME [TEXT]]"
    if len(args) < 2:
        print(usage, file=sys.stderr)
        return 2
    action, path = args[0], Path(args[1]).resolve()
    try:
        if action == "run":
            keep_running(path)
        elif action == "status":
            print(status_text(path))
        elif action == "once" and len(args) >= 3:
            services = read_services(path)
            if args[2] not in services:
                raise ServicesError(f"There is no service {args[2]!r} in {path}")
            result = run_once(
                services[args[2]],
                " ".join(args[3:]) if len(args) > 3 else None,
                folder=run_folder(path, args[2]),
            )
            state = read_state(path)
            state["last"][args[2]] = result
            write_state(path, state)
            print(result["answer"])
            if result.get("trace"):
                print(f"Trace: {result['trace']}")
            return 0 if result["ok"] else 1
        elif action in ("pause", "resume") and len(args) == 3:
            set_paused(path, args[2], action == "pause")
            print(f"{args[2]} is {'paused' if action == 'pause' else 'running again'}.")
        else:
            print(usage, file=sys.stderr)
            return 2
    except ServicesError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
