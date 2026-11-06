"""Kosika improving Kosika, one change that passes its checks at a time.

The problem this exists for: a person's access to a hosted coding assistant is
not permanent and not unlimited. The language should keep moving when that
access is gone, on this computer, with no bill and no quota.

What that can honestly mean, and what it cannot:

* A local model is not as able as a hosted one. It will propose worse changes,
  and many of its proposals are wrong. So nothing it says is trusted.
* Trust comes from the tree, not the model. Every proposal is applied to the
  real files, then the *changed files* are checked: they must compile, they
  must import, they must pass the project's linter and any test command the
  owner named. A change that fails any of those is undone from bytes captured
  before it was applied, and the failure text becomes the note for the next
  attempt at the same task.
* A change that passes is kept, appended to a ledger, and the next survey no
  longer sees the marker it removed. That is the whole learning loop: attempts
  and outcomes, per task kind, in one append-only file. Kinds that keep failing
  fall in the ranking; kinds that keep passing rise.

Three pieces, deliberately separate:

1. ``survey`` reads the tree and names the work. No model is asked anything.
   It finds TODO/FIXME markers in code, ``raise NotImplementedError`` stubs,
   and the unchecked items in the roadmap documents. Every task has a stable
   id derived from its kind, file and title, so the same work keeps the same
   history across runs and machines.
2. A *proposer* answers one task. Two are provided: ``ModelProposer`` asks a
   local OpenAI-compatible or Ollama server (no quota, no network beyond this
   computer), and ``DelegateProposer`` runs a command-line agent that is
   already installed -- Claude Code, Codex, an aider or a devin -- and reads
   the change it made. Either way the answer is only a proposal.
3. ``gate`` decides. Compile, import, lint, and whatever ``--gate`` adds.

The limits are the point, and they are the language's own principle -- unlimited
expression, bounded authority:

* A proposal may only name a path inside this project and inside ``allow``.
* ``find``/``replace`` edits must match exactly one place in the file, so a
  small model cannot quietly reformat a file it did not understand.
* Every attempt is done twice at most by default, then given up on.
* Nothing is committed unless the caller asked for it (``--commit``), and a
  failing change never survives an attempt.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tokenize
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .model import ChatMessage, ModelProvider, ModelRequest

#: Where a run keeps its memory, beside the tree it is improving: one
#: append-only ledger of attempts and their outcomes.
STATE_FOLDER = Path(".kosika") / "selfdev"
LEDGER_NAME = "ledger.jsonl"

#: Other tools' own stores and build output: never surveyed, never snapshotted.
SKIPPED = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        "_trash",
    }
)

#: What a run may edit, unless the caller says otherwise. Documentation is left
#: out on purpose: no check can tell a finished paragraph from a deleted
#: checkbox, so a documentation change is not something this loop can verify.
DEFAULT_ALLOW = ("kosika", "tests")

#: The folders ``survey`` reads for markers, unless the caller says otherwise.
DEFAULT_FOLDERS = ("kosika", "tests")

#: Documents whose unchecked items are work: the reconciled roadmaps.
ROADMAP_DOCUMENTS = (
    "AI_SUPERIORITY_ROADMAP.md",
    "AI_SUPERIORITY_PROGRESS.md",
    "KOSIKA_ROADMAP.md",
    "KOSIKA_PLAN.md",
)

MARKERS = ("TODO", "FIXME", "XXX", "HACK")
#: A marker is work when it *opens* a comment: ``# TODO: ...`` and not a
#: sentence that merely mentions the word. The convention is near universal,
#: and it keeps this scanner from reporting its own documentation as a job.
_MARKER = re.compile(r"^(TODO|FIXME|XXX|HACK)\b[:\s-]*(.{0,140})", re.ASCII)
#: And a stub is work only where it is the whole statement, at the start of a
#: line, with nothing to say. ``raise NotImplementedError("no skill")`` is a
#: deliberate error path in finished code, not an unfinished function.
_UNIMPLEMENTED = re.compile(r"^[ \t]*raise\s+NotImplementedError\s*(?:#.*)?$")
_CHECKBOX = re.compile(r"^\s*[-*]\s*\[ \]\s+(.+?)\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

#: Bytes read from one file before it is treated as data rather than source.
MAX_SCAN_BYTES = 512 * 1024
#: Bytes a single edit may produce, and a single file may already be.
MAX_FILE_BYTES = 1024 * 1024
#: Files a whole-tree snapshot will hold, for a delegate that edits in place.
MAX_SNAPSHOT_FILES = 20000
#: Characters of one file put in front of a proposer.
MAX_EXCERPT_CHARS = 24000
#: Characters of one gate step's output kept for the ledger and the screen.
MAX_OUTPUT_CHARS = 4000

_CODE_SUFFIXES = (".py", ".kos")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _task_id(kind: str, path: str, title: str) -> str:
    """A task keeps its identity when it moves but not when it changes.

    The line is left out deliberately: an unrelated edit above a marker must not
    orphan that marker's history. The title is included, so a different remark
    in the same place is a different task.
    """

    material = f"{kind}\x00{path}\x00{title}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:12]


# ---------------------------------------------------------------------------
# The work the tree names
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    """One thing worth changing, with where it was found."""

    id: str
    kind: str
    title: str
    detail: str
    path: str
    line: int | None = None
    weight: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "path": self.path,
            "line": self.line,
            "weight": self.weight,
        }

    def describe(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{self.id}  {self.kind:<12} {where}  {self.title}"


def _make(
    kind: str, path: str, line: int | None, title: str, detail: str, weight: float
) -> Task:
    return Task(
        id=_task_id(kind, path, title),
        kind=kind,
        title=title,
        detail=detail,
        path=path,
        line=line,
        weight=weight,
    )


def _walk(root: Path, folders: Sequence[str] | None = None) -> Iterable[tuple[str, Path]]:
    """Every source file under ``folders``, by its path relative to ``root``."""

    bases = [root / folder for folder in folders] if folders else [root]
    for base in bases:
        if not base.exists():
            continue
        if base.is_file():
            yield base.relative_to(root).as_posix(), base
            continue
        for current, directories, files in os.walk(base):
            directories[:] = sorted(
                name for name in directories if name not in SKIPPED
            )
            for name in sorted(files):
                path = Path(current) / name
                yield path.relative_to(root).as_posix(), path


def _read_text(path: Path) -> str | None:
    """One file as text with \\n endings, or None if it is too big or binary."""

    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return None
        return path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return None


def survey(
    root: Path,
    *,
    folders: Sequence[str] = DEFAULT_FOLDERS,
    max_findings: int = 200,
    roadmap_documents: Sequence[str] = ROADMAP_DOCUMENTS,
) -> list[Task]:
    """Everything the tree itself says is unfinished, with no model involved.

    Markers are read from code; roadmap items are read from the documents named
    in ``roadmap_documents`` under ``root``. A file already read is not read
    again, so a folder that is also named file by file costs one pass.
    """

    root = Path(root)
    found: dict[str, Task] = {}

    for relative, path in _walk(root, folders):
        if path.suffix not in _CODE_SUFFIXES:
            continue
        text = _read_text(path)
        if text is None:
            continue
        for number, comment in _notes(path.suffix, text):
            match = _MARKER.search(comment)
            if match is None:
                continue
            marker, rest = match.group(1), match.group(2).strip()
            task = _make(
                "marker",
                relative,
                number,
                f"{marker} {rest}".strip(),
                comment,
                1.0,
            )
            found.setdefault(task.id, task)
        for number, line in enumerate(text.split("\n"), start=1):
            if _UNIMPLEMENTED.match(line) is None:
                continue
            name = _enclosing_name(text, number) or "module level"
            task = _make(
                "unimplemented",
                relative,
                number,
                "unimplemented: " + name,
                line.strip(),
                1.2,
            )
            found.setdefault(task.id, task)

    for name in roadmap_documents:
        document = root / name
        text = _read_text(document)
        if text is None:
            continue
        for number, item in _roadmap_items(text):
            title = item[:140]
            task = _make("roadmap", name, number, title, item, 0.7)
            found.setdefault(task.id, task)

    ordered = sorted(found.values(), key=lambda task: (-task.weight, task.path, task.line or 0))
    return ordered[: max(1, max_findings)]


def _notes(suffix: str, text: str) -> Iterable[tuple[int, str]]:
    """Every comment in a file as (line, text), with the comment openers gone.

    A Python file is read by its own tokenizer, so a ``# TODO`` inside a string
    literal -- a test fixture, a template, a generated program -- is a string
    and not a note. A file that no longer parses falls back to reading whole
    comment lines, which is where a marker would be anyway.
    """

    if suffix == ".py":
        try:
            for token in tokenize.generate_tokens(io.StringIO(text).readline):
                if token.type == tokenize.COMMENT:
                    yield token.start[0], token.string.lstrip("#").strip()
            return
        except (tokenize.TokenError, IndentationError, SyntaxError):
            pass
    for number, line in enumerate(text.split("\n"), start=1):
        stripped = line.strip()
        for opener in ("#", "//"):
            if stripped.startswith(opener):
                yield number, stripped[len(opener) :].strip()
                break


def _enclosing_name(text: str, line: int) -> str:
    """The nearest ``def`` (or ``class``) above a line, for a title."""

    lines = text.split("\n")
    for index in range(min(line, len(lines)) - 1, -1, -1):
        stripped = lines[index].strip()
        if stripped.startswith(("def ", "class ", "async def ")):
            word = stripped.split()[0] if not stripped.startswith("async") else "def"
            rest = stripped[stripped.index(word) + len(word) :].strip()
            name = re.split(r"[\s(:]", rest, maxsplit=1)[0]
            if name:
                return name
    return ""


def _roadmap_items(text: str) -> Iterable[tuple[int, str]]:
    """Unchecked boxes in a roadmap, each with the heading it sits under."""

    heading = ""
    for number, line in enumerate(text.split("\n"), start=1):
        title = _HEADING.match(line)
        if title is not None:
            heading = title.group(2).strip()
            continue
        box = _CHECKBOX.match(line)
        if box is not None:
            item = box.group(1).strip()
            yield number, f"{heading}: {item}" if heading else item


# ---------------------------------------------------------------------------
# What has been tried, and what came of it
# ---------------------------------------------------------------------------


class Ledger:
    """One append-only file of attempts, and the little that is read back."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def append(self, record: Mapping[str, Any]) -> dict[str, Any]:
        entry = {"at": _now(), **dict(record)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def records(self) -> list[dict[str, Any]]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for line in text.split("\n"):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries

    def stats(self) -> dict[str, dict[str, float]]:
        """Per kind of task: how often a change of that kind survived its checks."""

        grouped: dict[str, dict[str, float]] = {}
        for entry in self.records():
            kind = str(entry.get("kind", "?"))
            bucket = grouped.setdefault(
                kind, {"attempts": 0.0, "kept": 0.0, "reverted": 0.0, "refused": 0.0}
            )
            bucket["attempts"] += 1
            status = entry.get("status")
            if status in bucket:
                bucket[status] += 1
        for bucket in grouped.values():
            decided = bucket["kept"] + bucket["reverted"] + bucket["refused"]
            bucket["rate"] = bucket["kept"] / decided if decided else 0.0
        return grouped

    def history(self, task_id: str | None = None) -> list[dict[str, Any]]:
        """Every attempt at one task, oldest first, or at all tasks."""

        return [
            entry
            for entry in self.records()
            if task_id is None or entry.get("task") == task_id
        ]

    def outcomes(self) -> dict[str, dict[str, int]]:
        """Per task id: how many attempts were kept and how many were not."""

        grouped: dict[str, dict[str, int]] = {}
        for entry in self.records():
            task_id = str(entry.get("task", ""))
            if not task_id:
                continue
            bucket = grouped.setdefault(task_id, {"kept": 0, "reverted": 0, "refused": 0})
            status = entry.get("status")
            if status in bucket:
                bucket[status] += 1
        return grouped


def rank(
    tasks: Sequence[Task],
    ledger: Ledger,
    *,
    max_task_failures: int = 3,
) -> list[Task]:
    """Order the work, and leave out what is done or has stopped paying.

    A task whose change was kept is done, whatever the tree still says -- a
    marker a passing change left in place is not work worth a second pass. A
    task that has been given up on ``max_task_failures`` times is left alone so
    a run cannot spend the night on one stubborn line.
    """

    stats = ledger.stats()
    outcomes = ledger.outcomes()
    scored: list[tuple[float, Task]] = []
    for task in tasks:
        past = outcomes.get(task.id, {"kept": 0, "reverted": 0, "refused": 0})
        if past["kept"]:
            continue
        given_up = past["reverted"] + past["refused"]
        if given_up >= max_task_failures:
            continue
        rate = stats.get(task.kind, {}).get("rate", 0.5)
        score = task.weight * (0.3 + 0.9 * rate) - 0.5 * given_up
        scored.append((score, task))
    scored.sort(key=lambda pair: (-pair[0], pair[1].path, pair[1].line or 0))
    return [task for _, task in scored]


# ---------------------------------------------------------------------------
# A proposal, and the rules any proposal obeys
# ---------------------------------------------------------------------------


class ProposalError(RuntimeError):
    """A proposal could not be read, or broke one of the rules above."""


@dataclass(frozen=True)
class Edit:
    """One change: the whole new text of a file, or one exact span replaced."""

    path: str
    find: str | None = None
    replace: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": self.path}
        if self.find is None:
            payload["content"] = self.replace
        else:
            payload["find"] = self.find
            payload["replace"] = self.replace
        return payload


@dataclass(frozen=True)
class Proposal:
    edits: tuple[Edit, ...] = ()
    note: str = ""


def _json_object(text: str) -> dict[str, Any]:
    """The one JSON object in an answer, fences and chatter aside."""

    stripped = text.strip()
    attempts = [stripped]
    if "```" in stripped:
        for block in stripped.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            if block:
                attempts.append(block)
    decoder = json.JSONDecoder()
    for candidate in attempts:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            start = candidate.find("{")
            if start < 0:
                continue
            try:
                parsed, _ = decoder.raw_decode(candidate[start:])
            except ValueError:
                continue
        if isinstance(parsed, dict):
            return parsed
    raise ProposalError("the answer was not one JSON object")


def parse_proposal(text: str) -> Proposal:
    """Read an answer as a proposal, or say precisely what was wrong with it."""

    payload = _json_object(text)
    note = payload.get("note") or ""
    if not isinstance(note, str):
        raise ProposalError("the note must be text")
    raw_edits = payload.get("edits", [])
    if not isinstance(raw_edits, list):
        raise ProposalError("edits must be a list")
    edits: list[Edit] = []
    for index, item in enumerate(raw_edits):
        if not isinstance(item, dict):
            raise ProposalError(f"edit {index + 1} is not an object")
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ProposalError(f"edit {index + 1} has no path")
        if "content" in item:
            content = item["content"]
            if not isinstance(content, str):
                raise ProposalError(f"edit {index + 1} content must be text")
            edits.append(Edit(path=path.strip(), find=None, replace=content))
            continue
        find = item.get("find")
        replace = item.get("replace", "")
        if not isinstance(find, str) or not find:
            raise ProposalError(f"edit {index + 1} needs find and replace, or content")
        if not isinstance(replace, str):
            raise ProposalError(f"edit {index + 1} replace must be text")
        edits.append(Edit(path=path.strip(), find=find, replace=replace))
    return Proposal(edits=tuple(edits), note=note)


def allowed(path: str, allow: Sequence[str]) -> bool:
    """Whether a relative path is inside one of the prefixes a run may edit."""

    relative = path.replace("\\", "/")
    for entry in allow:
        prefix = entry.replace("\\", "/").strip()
        if prefix.startswith("./"):
            prefix = prefix[2:]
        prefix = prefix.rstrip("/")
        if not prefix:
            continue
        if relative == prefix or relative.startswith(prefix + "/"):
            return True
    return False


def safe_target(root: Path, relative: str, allow: Sequence[str]) -> Path:
    """The file a proposal names, if naming it is allowed at all."""

    candidate = Path(relative)
    if candidate.is_absolute() or candidate.drive:
        raise ProposalError(
            f"{relative!r} is an absolute path; a change names a path inside the project"
        )
    normalized = candidate.as_posix()
    parts = normalized.split("/")
    if normalized in ("", ".") or ".." in parts:
        raise ProposalError(f"{relative!r} is not a file inside the project")
    boundary = Path(root).resolve()
    target = (boundary / candidate).resolve()
    try:
        target.relative_to(boundary)
    except ValueError:
        raise ProposalError(f"{relative!r} leaves the project folder") from None
    inside = target.relative_to(boundary).as_posix()
    if not allowed(inside, allow):
        raise ProposalError(
            f"{inside!r} is outside what this run may edit "
            f"({', '.join(allow) or 'nothing'})"
        )
    return target


def apply_edits(root: Path, edits: Sequence[Edit], *, allow: Sequence[str]) -> list[str]:
    """Write a proposal's edits, after checking every one of them.

    Nothing is written until every edit has been read and validated, so an
    answer that names one bad path cannot leave half a change behind.
    """

    root = Path(root).resolve()
    texts: dict[str, str] = {}
    endings: dict[str, str] = {}
    for edit in edits:
        target = safe_target(root, edit.path, allow)
        relative = target.relative_to(root).as_posix()
        if relative not in texts:
            if target.exists():
                raw = target.read_bytes()
                if len(raw) > MAX_FILE_BYTES:
                    raise ProposalError(f"{relative} is larger than one edit may touch")
                try:
                    decoded = raw.decode("utf-8")
                except UnicodeDecodeError:
                    raise ProposalError(f"{relative} is not text") from None
                endings[relative] = "\r\n" if "\r\n" in decoded else "\n"
                texts[relative] = decoded.replace("\r\n", "\n")
            else:
                endings[relative] = "\n"
                texts[relative] = ""
        text = texts[relative]
        if edit.find is None:
            updated = edit.replace.replace("\r\n", "\n")
        else:
            span = edit.find.replace("\r\n", "\n")
            count = text.count(span)
            if count == 0:
                raise ProposalError(
                    f"the text to replace was not found in {relative}; "
                    "copy it exactly from the file as shown"
                )
            if count > 1:
                raise ProposalError(
                    f"the text to replace appears {count} times in {relative}; "
                    "include more of the surrounding lines so it is unique"
                )
            updated = text.replace(span, edit.replace.replace("\r\n", "\n"), 1)
        if len(updated.encode("utf-8")) > MAX_FILE_BYTES:
            raise ProposalError(f"the change to {relative} is larger than a file may be")
        texts[relative] = updated

    written: list[str] = []
    for relative, text in texts.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")
        if endings.get(relative) == "\r\n":
            data = data.replace(b"\n", b"\r\n")
        target.write_bytes(data)
        written.append(relative)
    return written


# ---------------------------------------------------------------------------
# Undoing, exactly
# ---------------------------------------------------------------------------


def snapshot(
    root: Path,
    paths: Sequence[str] = (),
    *,
    whole_tree: bool = False,
) -> dict[str, bytes | None]:
    """The bytes of what a change may touch, so a failure can be undone.

    ``None`` means the file did not exist, which is how a change that adds a
    file is undone as well as one that alters it.
    """

    root = Path(root)
    kept: dict[str, bytes | None] = {}
    if whole_tree:
        for relative, path in _walk(root):
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                kept[relative] = path.read_bytes()
            except OSError:
                continue
            if len(kept) >= MAX_SNAPSHOT_FILES:
                break
        return kept
    for relative in paths:
        target = root / relative
        try:
            kept[relative] = target.read_bytes() if target.is_file() else None
        except OSError:
            kept[relative] = None
    return kept


def changed_since(root: Path, before: Mapping[str, bytes | None]) -> list[str]:
    """Every path whose bytes differ from the snapshot, including new ones."""

    now = snapshot(Path(root), whole_tree=True)
    changed = [
        relative
        for relative in set(before) | set(now)
        if before.get(relative) != now.get(relative)
    ]
    return sorted(changed)


def restore(root: Path, before: Mapping[str, bytes | None]) -> list[str]:
    """Put every snapshotted file back, and remove the ones that were new."""

    root = Path(root)
    undone: list[str] = []
    for relative, data in before.items():
        target = root / relative
        if data is None:
            try:
                if target.exists():
                    target.unlink()
                    undone.append(relative)
            except OSError:
                continue
            continue
        try:
            current = target.read_bytes()
        except OSError:
            current = None
        if current == data:
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            undone.append(relative)
        except OSError:
            continue
    return undone


# ---------------------------------------------------------------------------
# What decides whether a change is kept
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateStep:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class GateResult:
    ok: bool
    steps: tuple[GateStep, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "steps": [step.to_dict() for step in self.steps]}

    def first_failure(self) -> GateStep | None:
        return next((step for step in self.steps if not step.ok), None)


_COMPILE = (
    "import pathlib, sys\n"
    "problem = None\n"
    "for name in sys.argv[1:]:\n"
    "    try:\n"
    "        source = pathlib.Path(name).read_bytes().decode('utf-8')\n"
    "        compile(source.replace('\\r\\n', '\\n'), name, 'exec')\n"
    "    except Exception as exc:  # noqa: BLE001 - reported, not raised\n"
    "        problem = f'{name}: {type(exc).__name__}: {exc}'\n"
    "        break\n"
    "print(problem or '')\n"
    "sys.exit(1 if problem else 0)\n"
)


def _run_step(
    root: Path, name: str, argv: Sequence[str], *, timeout: float = 300.0
) -> GateStep:
    try:
        done = subprocess.run(  # noqa: S603 - an argv this module built
            list(argv),
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except FileNotFoundError:
        return GateStep(name=name, ok=True, detail="not installed, skipped")
    except subprocess.TimeoutExpired:
        return GateStep(name=name, ok=False, detail=f"timed out after {timeout:.0f}s")
    except OSError as exc:
        return GateStep(name=name, ok=True, detail=f"could not run, skipped: {exc}")
    output = (done.stdout or "") + (done.stderr or "")
    output = output.strip()
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n[... truncated]"
    return GateStep(name=name, ok=done.returncode == 0, detail=output)


def _module_name(relative: str) -> str | None:
    if not relative.startswith("kosika/") or not relative.endswith(".py"):
        return None
    stem = relative[: -len(".py")].replace("/", ".")
    if stem.endswith(".__init__"):
        stem = stem[: -len(".__init__")]
    if all(part.isidentifier() for part in stem.split(".")):
        return stem
    return None


def run_gate(
    root: Path,
    changed: Sequence[str],
    *,
    extra: Sequence[str] = (),
    timeout: float = 300.0,
) -> GateResult:
    """Check the changed files: compile, import, lint, then the owner's commands.

    Only what changed is checked, and only cheaply: this is the floor a change
    must clear to be kept, not the project's whole suite. ``extra`` is where a
    person adds the test command that matters to them.
    """

    root = Path(root)
    steps: list[GateStep] = []
    python_files = [name for name in changed if name.endswith(".py")]

    if python_files:
        steps.append(
            _run_step(root, "compile", [sys.executable, "-c", _COMPILE, *python_files])
        )
        modules = [
            module for module in (_module_name(name) for name in changed) if module
        ]
        if modules:
            steps.append(
                _run_step(
                    root,
                    "import",
                    [
                        sys.executable,
                        "-c",
                        (
                            "import importlib, sys\n"
                            "for name in sys.argv[1:]:\n"
                            "    importlib.import_module(name)\n"
                        ),
                        *modules,
                    ],
                    timeout=timeout,
                )
            )

    linter = shutil.which("ruff")
    lint_argv = [linter] if linter else None
    if lint_argv is None:
        probe = subprocess.run(  # noqa: S603 - asking whether ruff can be run at all
            [sys.executable, "-m", "ruff", "--version"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if probe.returncode == 0:
            lint_argv = [sys.executable, "-m", "ruff"]
    if lint_argv is not None and python_files:
        steps.append(
            _run_step(
                root,
                "lint",
                [*lint_argv, "check", "--no-cache", "--quiet", *python_files],
                timeout=timeout,
            )
        )

    for command in extra:
        argv = shlex.split(command)
        if not argv:
            continue
        steps.append(_run_step(root, command, argv, timeout=timeout))

    ok = all(step.ok for step in steps)
    return GateResult(ok=ok, steps=tuple(steps))


# ---------------------------------------------------------------------------
# Who proposes a change
# ---------------------------------------------------------------------------


class Proposer(Protocol):
    """One answer to one task. Nothing it returns is trusted on its own."""

    backend: str
    in_place: bool

    def propose(self, task: Task, prompt: str) -> Proposal:
        """Return a proposal for this task, or raise ``ProposalError``."""


SYSTEM_EDITS = (
    "You are improving the source of Kosika, a Python-hosted language for "
    "auditable AI agents. You answer with one JSON object and nothing else. "
    "You make the smallest change that removes the problem you are shown. You "
    "never invent helpers, dependencies, configuration or new files unless the "
    "change cannot be made without them. If the right change is not clear from "
    "what you are shown, you answer with no edits and say what you would need."
)

SYSTEM_IN_PLACE = (
    "You are improving the source of Kosika in this folder. Edit files directly. "
    "Make the smallest change that removes the problem you are shown. Do not run "
    "git, do not install packages, do not reformat files you are not changing, "
    "and do not touch tests to make a failing check pass."
)


def _excerpt(root: Path, task: Task, max_chars: int = MAX_EXCERPT_CHARS) -> str:
    """The task's file, or the part of it around the reported line."""

    path = root / task.path
    text = _read_text(path)
    if text is None:
        return f"--- {task.path} ---\n(the file could not be read as text)"
    lines = text.split("\n")
    if len(text) <= max_chars:
        body = "\n".join(f"{number:6d}  {line}" for number, line in enumerate(lines, 1))
    else:
        centre = (task.line or 1) - 1
        low = max(0, centre - 120)
        high = min(len(lines), centre + 120)
        body = "\n".join(
            f"{number:6d}  {lines[number - 1]}" for number in range(low + 1, high + 1)
        )
        body = f"(lines {low + 1} to {high} of {len(lines)})\n" + body
    return f"--- {task.path} (line numbers are not part of the file) ---\n{body}"


def _purpose(text: str) -> str:
    """The first line of a file's own docstring, so a map can say what it is for."""

    body = text.lstrip()
    for opener in ('"""', "'''"):
        if not body.startswith(opener):
            continue
        end = body.find(opener, 3)
        if end <= 3:
            return ""
        first = body[3:end].strip().split("\n")[0].strip()
        return first[:110]
    return ""


def repo_map(
    root: Path, allow: Sequence[str] = DEFAULT_ALLOW, *, limit: int = 150
) -> list[str]:
    """A file list with one line each, for a task named outside the code.

    A roadmap item points at a document, not at a file. Without this, the model
    would be shown the document it may not edit and asked to change the code it
    has never seen; with it, it can name a file the run is allowed to edit.
    """

    lines: list[str] = []
    for relative, path in _walk(Path(root)):
        if path.suffix not in _CODE_SUFFIXES or not allowed(relative, allow):
            continue
        text = _read_text(path)
        if text is None:
            continue
        count = text.count("\n") + 1
        purpose = _purpose(text)
        lines.append(
            f"{relative} ({count} lines)" + (f"  {purpose}" if purpose else "")
        )
        if len(lines) >= limit:
            break
    return lines


def build_prompt(
    root: Path,
    task: Task,
    *,
    allow: Sequence[str] = DEFAULT_ALLOW,
    notes: Sequence[str] = (),
    in_place: bool = False,
    excerpt_chars: int = MAX_EXCERPT_CHARS,
) -> str:
    """What a proposer is told: the task, the file, the rules, and last time."""

    where = f"{task.path}:{task.line}" if task.line else task.path
    parts = [
        "TASK",
        f"Make this true: {task.title}",
        f"Found at {where}",
        f"The line read: {task.detail}",
        "",
    ]
    if allowed(task.path, allow):
        parts += ["THE FILE AS IT IS NOW", _excerpt(root, task, excerpt_chars), ""]
    else:
        parts += [
            "WHERE THE WORK BELONGS",
            f"{task.path} names the work; it is not the file to change. "
            "The file to change is one of these. Read the task, choose the one "
            "file whose job this is, and change only that:",
            *repo_map(root, allow),
            "",
        ]
    parts += [
        "WHAT MAY BE CHANGED",
        ", ".join(allow),
        "No other path. No new dependency. No change to how the project is configured.",
    ]
    if in_place:
        parts += [
            "",
            "HOW TO WORK",
            "Edit the files in this folder yourself, inside the paths above. "
            "When you are finished, stop; the change is read from the folder.",
        ]
    else:
        parts += [
            "",
            "HOW TO ANSWER",
            "One JSON object, nothing else:",
            '{"edits": [{"path": "kosika/example.py", "find": "exact text", '
            '"replace": "new text"}], "note": "one sentence"}',
            "- `find` must appear exactly once in the file and be copied "
            "character for character from the text above, without the line numbers.",
            "- To replace a whole file instead, use "
            '{"path": "kosika/example.py", "content": "the whole new file"}.',
            "- If no safe change is possible, answer "
            '{"edits": [], "note": "what is missing"}.',
        ]
    if notes:
        parts += ["", "WHAT WENT WRONG LAST TIME, DO NOT REPEAT IT"]
        parts += [f"- {note}" for note in notes]
    return "\n".join(parts)


class ModelProposer:
    """A local model, asked for one patch. No quota, no network off this machine."""

    backend = "model"
    in_place = False

    def __init__(
        self,
        provider: ModelProvider,
        *,
        temperature: float = 0.1,
        max_output_tokens: int = 4096,
    ) -> None:
        self.provider = provider
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def propose(self, task: Task, prompt: str) -> Proposal:
        response = self.provider.complete(
            ModelRequest(
                messages=(
                    ChatMessage("system", SYSTEM_EDITS),
                    ChatMessage("user", prompt),
                ),
                temperature=self.temperature,
                max_output_tokens=self.max_output_tokens,
                json_object=True,
            )
        )
        content = (response.content or "").strip()
        if not content:
            reasoning = (response.reasoning or "").strip()
            hint = " (the model spent its whole answer on reasoning)" if reasoning else ""
            raise ProposalError(f"the model answered nothing{hint}")
        return parse_proposal(content)


class SilentProposer:
    """A dry run's placeholder: the request is built, and nothing is asked."""

    backend = "dry-run"
    in_place = False

    def propose(self, task: Task, prompt: str) -> Proposal:
        return Proposal(note="dry run")


class DelegateProposer:
    """A command-line agent already on this machine, editing the folder itself.

    This is how a working setup stays useful when a hosted assistant is still
    available: point ``--command`` at it, and the same checks, the same revert
    and the same ledger apply to what it does.
    """

    backend = "delegate"
    in_place = True

    def __init__(
        self,
        argv: Sequence[str],
        *,
        root: Path,
        timeout: float = 1800.0,
    ) -> None:
        if not argv:
            raise ValueError("A delegate needs a command to run")
        self.argv = tuple(argv)
        self.root = Path(root)
        self.timeout = timeout

    def propose(self, task: Task, prompt: str) -> Proposal:
        try:
            done = subprocess.run(  # noqa: S603 - the owner named this command
                list(self.argv),
                cwd=self.root,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
            )
        except FileNotFoundError:
            raise ProposalError(f"{self.argv[0]!r} is not installed") from None
        except subprocess.TimeoutExpired:
            raise ProposalError(
                f"{self.argv[0]!r} did not finish within {self.timeout:.0f}s"
            ) from None
        except OSError as exc:
            raise ProposalError(f"{self.argv[0]!r} could not be started: {exc}") from None
        if done.returncode != 0:
            output = ((done.stdout or "") + (done.stderr or "")).strip()
            raise ProposalError(
                f"{self.argv[0]!r} exited {done.returncode}: "
                f"{output[:MAX_OUTPUT_CHARS] or 'no output'}"
            )
        return Proposal(note=f"{self.argv[0]} finished")


# ---------------------------------------------------------------------------
# One attempt, and a night of them
# ---------------------------------------------------------------------------


@dataclass
class Attempt:
    task: Task
    status: str
    files: tuple[str, ...] = ()
    gate: GateResult | None = None
    note: str = ""
    seconds: float = 0.0
    backend: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task.to_dict(),
            "status": self.status,
            "files": list(self.files),
            "gate": self.gate.to_dict() if self.gate is not None else None,
            "note": self.note,
            "seconds": round(self.seconds, 2),
            "backend": self.backend,
        }

    def describe(self) -> str:
        head = f"{self.status:<9} {self.task.id}  {self.task.title}"
        lines = [head]
        if self.files:
            lines.append("          changed: " + ", ".join(self.files))
        if self.gate is not None:
            for step in self.gate.steps:
                mark = "ok  " if step.ok else "FAIL"
                lines.append(f"          {mark} {step.name}")
                if not step.ok and step.detail:
                    for line in step.detail.split("\n")[:6]:
                        lines.append(f"               {line}")
        if self.note:
            lines.append(f"          note: {self.note[:400]}")
        lines.append(f"          {self.seconds:.1f}s")
        return "\n".join(lines)


def default_state_folder(root: Path) -> Path:
    return Path(root) / STATE_FOLDER


class SelfDevEngine:
    """The loop: survey, propose, check, keep or undo, remember, repeat."""

    def __init__(
        self,
        root: Path,
        proposer: Proposer,
        *,
        allow: Sequence[str] = DEFAULT_ALLOW,
        gate_commands: Sequence[str] = (),
        state_folder: Path | None = None,
        max_attempts: int = 2,
        max_task_failures: int = 3,
        commit: bool = False,
        dry_run: bool = False,
        sleep_seconds: float = 0.0,
        gate_timeout: float = 300.0,
        excerpt_chars: int = MAX_EXCERPT_CHARS,
        log: Any = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.proposer = proposer
        self.allow = tuple(allow) or DEFAULT_ALLOW
        self.gate_commands = tuple(gate_commands)
        self.state_folder = state_folder or default_state_folder(self.root)
        self.ledger = Ledger(self.state_folder / LEDGER_NAME)
        self.max_attempts = max(1, max_attempts)
        self.max_task_failures = max(1, max_task_failures)
        self.commit = commit
        self.dry_run = dry_run
        self.sleep_seconds = max(0.0, sleep_seconds)
        self.gate_timeout = gate_timeout
        self.excerpt_chars = max(2000, excerpt_chars)
        self._log = log

    # -- telling a person what is happening ---------------------------------

    def tell(self, message: str) -> None:
        if self._log is not None:
            self._log(message)

    # -- what is left to do -------------------------------------------------

    def tasks(
        self,
        *,
        folders: Sequence[str] = DEFAULT_FOLDERS,
        limit: int = 200,
    ) -> list[Task]:
        return rank(
            survey(self.root, folders=folders, max_findings=limit),
            self.ledger,
            max_task_failures=self.max_task_failures,
        )

    # -- one task -----------------------------------------------------------

    def attempt(self, task: Task) -> Attempt:
        """Try one task until it passes, is given up on, or is refused."""

        started = time.monotonic()
        notes = [
            str(entry.get("note", "")).strip()
            for entry in self.ledger.history(task.id)
            if str(entry.get("note", "")).strip()
        ][-3:]

        if self.dry_run:
            # A dry run asks nothing and records nothing: it shows the request
            # the next task would get, and leaves the ledger alone.
            attempt = self._once(task, notes)
            attempt.seconds = time.monotonic() - started
            return attempt

        last: Attempt | None = None
        for index in range(self.max_attempts):
            attempt = self._once(task, notes)
            attempt.seconds = time.monotonic() - started
            if attempt.status == "kept":
                self._record(attempt)
                return attempt
            notes = [*notes, f"{attempt.status}: {attempt.note}"][-3:]
            last = attempt
            if index + 1 < self.max_attempts:
                self.tell(f"          retry {index + 2}/{self.max_attempts}")
        assert last is not None
        self._record(last)
        return last

    def _once(self, task: Task, notes: Sequence[str]) -> Attempt:
        in_place = bool(getattr(self.proposer, "in_place", False))
        backend = str(getattr(self.proposer, "backend", "?"))
        prompt = build_prompt(
            self.root,
            task,
            allow=self.allow,
            notes=notes,
            in_place=in_place,
            excerpt_chars=self.excerpt_chars,
        )
        if self.dry_run:
            return Attempt(
                task=task, status="proposed", note=prompt, backend=backend
            )

        before: dict[str, bytes | None] = {}
        try:
            if in_place:
                before = snapshot(self.root, whole_tree=True)
                self.proposer.propose(task, prompt)
                changed = changed_since(self.root, before)
                outside = [name for name in changed if not allowed(name, self.allow)]
                if outside:
                    restore(self.root, before)
                    return Attempt(
                        task=task,
                        status="refused",
                        note=(
                            "changed paths this run may not edit: "
                            + ", ".join(outside[:6])
                        ),
                        backend=backend,
                    )
            else:
                proposal = self.proposer.propose(task, prompt)
                if not proposal.edits:
                    return Attempt(
                        task=task,
                        status="refused",
                        note=proposal.note or "the proposer offered no change",
                        backend=backend,
                    )
                paths = [edit.path for edit in proposal.edits]
                try:
                    before = snapshot(self.root, paths)
                except OSError as exc:
                    return Attempt(
                        task=task, status="refused", note=str(exc), backend=backend
                    )
                changed = apply_edits(self.root, proposal.edits, allow=self.allow)
        except ProposalError as exc:
            restore(self.root, before)
            return Attempt(task=task, status="refused", note=str(exc), backend=backend)
        except Exception as exc:  # noqa: BLE001 - a proposer must not end a run
            restore(self.root, before)
            return Attempt(
                task=task,
                status="refused",
                note=f"{type(exc).__name__}: {exc}",
                backend=backend,
            )

        if not changed:
            return Attempt(
                task=task,
                status="refused",
                note="the proposal changed nothing on disk",
                backend=backend,
            )

        gate = run_gate(
            self.root,
            changed,
            extra=self.gate_commands,
            timeout=self.gate_timeout,
        )
        if gate.ok:
            note = "checks passed"
            if self.commit:
                note += "; " + self._commit(task, changed)
            return Attempt(
                task=task,
                status="kept",
                files=tuple(changed),
                gate=gate,
                note=note,
                backend=backend,
            )

        failure = gate.first_failure()
        restore(self.root, before)
        detail = (failure.detail if failure else "").strip().split("\n")
        reason = (failure.name + ": " + (detail[0] if detail else "failed")) if failure else ""
        return Attempt(
            task=task,
            status="reverted",
            files=tuple(changed),
            gate=gate,
            note=reason,
            backend=backend,
        )

    def _commit(self, task: Task, files: Sequence[str]) -> str:
        message = (
            f"selfdev: {task.title[:60]}\n\n"
            f"Task {task.id} ({task.kind}) at {task.path}"
            f"{f':{task.line}' if task.line else ''}, proposed by "
            f"{getattr(self.proposer, 'backend', '?')} and kept after its checks."
        )
        added = subprocess.run(  # noqa: S603 - git, asked to stage named files
            ["git", "add", "--", *files],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if added.returncode != 0:
            return "not committed: " + (added.stderr or added.stdout).strip()[:200]
        done = subprocess.run(  # noqa: S603 - git, asked for one commit
            ["git", "commit", "-m", message, "--", *files],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        output = ((done.stdout or "") + (done.stderr or "")).strip()
        return ("committed" if done.returncode == 0 else "not committed") + (
            f": {output.splitlines()[0][:160]}" if output else ""
        )

    def _record(self, attempt: Attempt) -> None:
        self.ledger.append(
            {
                "task": attempt.task.id,
                "kind": attempt.task.kind,
                "title": attempt.task.title,
                "path": attempt.task.path,
                "line": attempt.task.line,
                "status": attempt.status,
                "files": list(attempt.files),
                "backend": attempt.backend,
                "seconds": round(attempt.seconds, 2),
                "note": attempt.note[:MAX_OUTPUT_CHARS],
                "gate": attempt.gate.to_dict() if attempt.gate is not None else None,
            }
        )

    # -- a night of them ----------------------------------------------------

    def run(
        self,
        *,
        cycles: int | None = 1,
        until_hours: float | None = None,
        max_consecutive_failures: int = 3,
        folders: Sequence[str] = DEFAULT_FOLDERS,
    ) -> dict[str, Any]:
        """Work through the backlog, stopping before it becomes a stuck loop.

        ``cycles=None`` with ``until_hours`` is the unattended shape: keep going
        until the time budget runs out or the work does. A run also stops after
        ``max_consecutive_failures`` attempts in a row that were not kept, since
        that is a broken setup rather than a hard task.
        """

        if self.dry_run:
            # One task is shown, once. Nothing is asked, so there is nothing
            # for a second cycle to learn from.
            cycles = 1
            until_hours = None
        deadline = (
            time.monotonic() + until_hours * 3600.0
            if until_hours is not None and until_hours > 0
            else None
        )
        summary: dict[str, Any] = {
            "attempts": [],
            "kept": 0,
            "reverted": 0,
            "refused": 0,
            "proposed": 0,
            "cycles": 0,
            "stopped": "cycles finished",
            "started": _now(),
        }
        consecutive = 0
        while cycles is None or summary["cycles"] < cycles:
            if deadline is not None and time.monotonic() >= deadline:
                summary["stopped"] = "time budget reached"
                break
            options = self.tasks(folders=folders)
            if not options:
                summary["stopped"] = "no task left"
                break
            task = options[0]
            self.tell(f"[{summary['cycles'] + 1}] {task.describe()}")
            attempt = self.attempt(task)
            summary["attempts"].append(attempt.to_dict())
            if attempt.status in summary:
                summary[attempt.status] += 1
            else:
                summary["refused"] += 1
            summary["cycles"] += 1
            self.tell(attempt.describe())
            if attempt.status == "kept":
                consecutive = 0
            else:
                consecutive += 1
                if consecutive >= max_consecutive_failures:
                    summary["stopped"] = (
                        f"{consecutive} attempts in a row were not kept; "
                        "the setup needs a person"
                    )
                    break
            if self.sleep_seconds and (cycles is None or summary["cycles"] < cycles):
                time.sleep(self.sleep_seconds)
        summary["finished"] = _now()
        return summary
