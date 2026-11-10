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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    lines = TODO.read_text(encoding="utf-8").splitlines()
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
    while (WORK / f"{today}-{count}").exists():
        count += 1
    place = WORK / f"{today}-{count}"
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
    shutil.rmtree(before, ignore_errors=True)

    mark = "- [x] " if passed else "- [!] "
    lines[number] = mark + text + f" ({today}, {place.name})"
    TODO.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(("PASSED " if passed else "DID NOT PASS ") + text)
    print(place / "report.md")


if __name__ == "__main__":
    main()
