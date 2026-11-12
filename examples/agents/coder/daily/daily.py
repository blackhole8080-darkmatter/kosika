"""Kosika's daily development: the coder takes the next request in todo.md.

    python daily.py              # the next open request
    python daily.py --dry-run    # say which request it would take

Each run works on a fresh copy of Kosika as studio has it on GitHub, in
~/Aryan_Private/kosika-daily/DATE-N, never in the checkout itself. The coder
runs there with every one of its steps allowed, because nothing it does can
leave that copy. What it changed is saved beside the copy as change.patch,
with report.md: the request, what the coder said, and what was checked
afterwards (the test files it wrote, and go vet for the Go engine). A
change reaches Kosika only when its owner reads it and applies it:

    git apply ~/Aryan_Private/kosika-daily/DATE-N/change.patch

The model is the virtual model router (examples/virtual_models), started
here when it is not already running, so a day's free allowance used up on
one model moves the work to the next free one, and at last to a model on
this computer, instead of stopping it.

In todo.md, an open request is a line "- [ ] ...". A run marks the line it
took "- [x]" when the checks passed and "- [!]" when they did not, with the
date and the folder, so the next run takes the next request and a failed one
is not tried again until its line is opened once more.
"""

from __future__ import annotations

import argparse
import datetime
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
sys.path.insert(0, str(REPO))

from kosika.app import App  # noqa: E402
from kosika.model import OpenAICompatibleProvider  # noqa: E402

TODO = HERE / "todo.md"
WORK = Path.home() / "Aryan_Private" / "kosika-daily"
COPIED = ["kosika", "native", "tests/test_short_methods.py", "KOSIKA.md"]
ROUTER = REPO / "examples" / "virtual_models" / "learning_router.py"
PORT = 8791


def next_request(lines: list[str]) -> int | None:
    for number, line in enumerate(lines):
        if line.startswith("- [ ] "):
            return number
    return None


def copy_studio(folder: Path) -> None:
    """Kosika as studio has it on GitHub: what others will build on too."""
    subprocess.run(["git", "fetch", "-q", "origin", "studio"], cwd=REPO, check=False)
    tar = subprocess.run(
        ["git", "archive", "--format=tar", "origin/studio", *COPIED],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as archive:
        archive.extractall(folder, filter="data")


def keep_answers(since: float) -> None:
    """Tell the router the change passed, so it keeps the answers it gave
    since `since` and gives them again at once if asked the same (see
    examples/virtual_models/smarter.py). Not in a swarm: there the other
    coders' answers, which may not pass, came in the same minutes."""
    import urllib.request

    try:
        request = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/kosika/passed",
                                         data=json.dumps({"since": since}).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as answer:
            print("kept", json.load(answer).get("kept", 0), "answers that passed")
    except OSError:
        pass


def router_up() -> bool:
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


def start_router() -> subprocess.Popen | None:
    if router_up():
        return None
    env = dict(os.environ)
    for name in ("GROQ_API_KEY", "OPENROUTER_API_KEY"):
        env.setdefault(name, user_variable(name))
    process = subprocess.Popen(
        [sys.executable, str(ROUTER), "--port", str(PORT)], env=env
    )
    for _ in range(40):
        if router_up():
            return process
        time.sleep(0.25)
    raise SystemExit("the model router did not start")


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


def run_coder(folder: Path, agents: Path, text: str) -> dict:
    shutil.copytree(
        HERE.parent,
        agents / "coder",
        ignore=shutil.ignore_patterns("memory", "runs", "daily"),
    )

    def binder(program):
        return {
            model.name: OpenAICompatibleProvider(
                f"http://127.0.0.1:{PORT}/v1",
                "kosika-virtual",
                api_key="none",
                protocol="openai",
                timeout=600,
            )
            for model in program.models
            if model.provider is None
        }

    app = App(
        agents,
        bind_models=binder,
        model_label="kosika-virtual",
        allow_folder=folder,
        allow_shell=True,
    )
    agent = next(key for key in app.agents() if key.startswith("coder/"))
    started = time.monotonic()
    job = app.start(agent, {"text": text})
    while job.state in ("running", "waiting") and time.monotonic() - started < 3600:
        if job.state == "waiting":
            app.decide(job.id, True)
        time.sleep(0.5)
    return {
        "state": job.state,
        "seconds": round(time.monotonic() - started),
        "said": getattr(job, "text", "") or "",
        "error": job.error or "",
    }


def changed_files(before: Path, after: Path) -> list[str]:
    names = []
    for path in sorted(after.rglob("*")):
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or path.suffix in (".exe", ".jsonl")
        ):
            continue
        relative = path.relative_to(after)
        old = before / relative
        # A file written back with other line ends, or not changed at all,
        # is not a change.
        if not old.exists() or old.read_bytes().replace(
            b"\r\n", b"\n"
        ) != path.read_bytes().replace(b"\r\n", b"\n"):
            names.append(relative.as_posix())
    return names


def checked(after: Path, files: list[str]) -> list[tuple[str, bool, str]]:
    """The checks a person can trust without reading the model's words."""
    results = []
    tests = [f for f in files if f.startswith("tests/") and f.endswith(".py")]
    if tests:
        ran = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
            cwd=after,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        results.append(
            (
                "pytest " + " ".join(tests),
                ran.returncode == 0,
                (ran.stdout + ran.stderr)[-1500:],
            )
        )
    if any(f.endswith(".go") for f in files) and shutil.which("go"):
        ran = subprocess.run(
            ["go", "-C", "native", "vet", "."],
            cwd=after,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        results.append(
            (
                "go vet (the Go engine compiles)",
                ran.returncode == 0,
                (ran.stdout + ran.stderr)[-1500:],
            )
        )
    return results


#: Every change that passed its checks, as a training example for a Kosika
#: coder model: what was asked, each changed file before and after. Only
#: what passed is kept, so the examples teach what works.
EXAMPLES = WORK / "examples.jsonl"


def save_example(request: str, before: Path, after: Path, files: list[str]) -> None:
    def text_of(path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n") if path.exists() else ""

    example = {
        "request": request,
        "files": [{"path": name, "before": text_of(before / name), "after": text_of(after / name)} for name in files],
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with EXAMPLES.open("a", encoding="utf-8") as out:
        out.write(json.dumps(example) + "\n")


#: A request a swarm member has taken and is working on.
TAKEN = "- [~] "


def mark_line(text: str, mark: str, note: str = "") -> None:
    """Mark the line holding `text`, open or taken, in todo.md as it is now:
    several swarm members write it, so each reads it again just before, under
    a lock file."""
    lock = TODO.with_suffix(".lock")
    for _ in range(600):
        try:
            lock.mkdir()
            break
        except FileExistsError:
            time.sleep(0.1)
    try:
        lines = TODO.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line[6:].split(" (20")[0].strip() == text and line[:6] in ("- [ ] ", TAKEN):
                lines[i] = mark + text + note
                break
        TODO.write_text("\n".join(lines) + "\n", encoding="utf-8")
    finally:
        lock.rmdir()


def swarm(size: int) -> None:
    """Several coders at once, one request each, sharing the virtual model
    pool. Each works in its own copy and is checked on its own; nothing waits
    for the others but the end of the run."""
    lines = TODO.read_text(encoding="utf-8").splitlines()
    open_ones = [line[6:].strip() for line in lines if line.startswith("- [ ] ")][:size]
    if not open_ones:
        print("Nothing is open in todo.md.")
        return
    for text in open_ones:
        mark_line(text, TAKEN)
    router = start_router()
    started = time.monotonic()
    alone = dict(os.environ, KOSIKA_SWARM="1") if len(open_ones) > 1 else dict(os.environ)
    members = [subprocess.Popen([sys.executable, __file__, "--take", text], env=alone) for text in open_ones]
    for member in members:
        member.wait()
    if router is not None:
        router.terminate()
    print(f"Swarm of {len(members)}: {round(time.monotonic() - started)} s for all of them together.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--swarm", type=int, default=0, help="run this many coders at once, one request each")
    parser.add_argument("--take", help="(a swarm member) work on this request, already marked as taken")
    args = parser.parse_args()
    if args.swarm:
        swarm(args.swarm)
        return
    lines = TODO.read_text(encoding="utf-8").splitlines()
    if args.take:
        text = args.take
    else:
        number = next_request(lines)
        if number is None:
            print("Nothing is open in todo.md.")
            return
        text = lines[number][len("- [ ] ") :].strip()
    if args.dry_run:
        print("Next:", text)
        return

    today = datetime.date.today().isoformat()
    count = 1
    while True:
        place = WORK / f"{today}-{count}"
        try:
            place.mkdir(parents=True)
            break
        except FileExistsError:
            count += 1
    # What it was asked, for the live board (swarm_watch.py) while it works.
    (place / "request.txt").write_text(text, encoding="utf-8")
    before, after = place / "before", place / "kosika"
    before.mkdir(parents=True)
    copy_studio(before)
    shutil.copytree(before, after)

    router = start_router()
    try:
        ran = run_coder(after, place / "_agents", text)
    finally:
        if router is not None:
            router.terminate()

    # What running the tests and the engines left behind is not the change:
    # compiled Python, traces and built engines stay out of the patch.
    for left in [*after.rglob("__pycache__"), *after.rglob(".pytest_cache")]:
        shutil.rmtree(left, ignore_errors=True)
    for left in [*after.rglob("*.jsonl"), *after.rglob("*.exe")]:
        if not (before / left.relative_to(after)).exists():
            left.unlink(missing_ok=True)
    files = changed_files(before, after)
    diff = subprocess.run(
        ["git", "diff", "--no-index", "--no-prefix", "before", "kosika"],
        cwd=place,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    diff = diff.replace("--- before/", "--- a/").replace("+++ kosika/", "+++ b/")
    diff = (
        "\n".join(
            line.replace("diff --git before/", "diff --git a/")
            .replace("diff --git kosika/", "diff --git a/", 1)
            .replace(" kosika/", " b/", 1)
            if line.startswith("diff --git")
            else line
            for line in diff.splitlines()
        )
        + "\n"
    )
    (place / "change.patch").write_text(diff, encoding="utf-8", newline="\n")
    checks = checked(after, files)
    # The coder's own words count against it, never for it: a run that says
    # its tests failed, or that the engines disagree, has not passed.
    said = ran["said"]
    owned_up = any(words in said for words in ("do NOT agree", "did not go on")) or (
        "engines agree" not in said and "The tests failed" in said[-800:]
    )
    passed = (
        bool(files)
        and ran["state"] == "done"
        and not owned_up
        and all(ok for _, ok, _ in checks)
    )

    report = [
        f"# {text}",
        "",
        f"{today}, {ran['seconds']} s, {'passed its checks' if passed else 'did not pass'}",
        "",
        "## Files changed",
        *(f"- {name}" for name in files or ["(none)"]),
        "",
        "## Checked afterwards",
    ]
    report += [f"- {'PASS' if ok else 'FAIL'} {name}" for name, ok, _ in checks] or [
        "- nothing to check"
    ]
    report += ["", "## What the coder said", "", ran["said"] or ran["error"], ""]
    for name, ok, output in checks:
        if not ok:
            report += [f"## {name}", "", "```", output, "```", ""]
    report += [
        "To bring it into Kosika: `git apply " + str(place / "change.patch") + "`",
        "",
    ]
    (place / "report.md").write_text("\n".join(report), encoding="utf-8")
    (place / "run.json").write_text(
        json.dumps({**ran, "files": files, "passed": passed}, indent=1),
        encoding="utf-8",
    )
    if passed:
        save_example(text, before, after, files)
        if not os.environ.get("KOSIKA_SWARM"):
            keep_answers(time.time() - float(ran["seconds"]) - 5)
    shutil.rmtree(before, ignore_errors=True)

    mark_line(text, "- [x] " if passed else "- [!] ", f" ({today}, {place.name})")
    print(("PASSED " if passed else "DID NOT PASS ") + text)
    print(place / "report.md")


if __name__ == "__main__":
    main()
