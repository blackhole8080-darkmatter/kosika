"""Local skill discovery and deterministic multi-skill selection.

Skills are advisory model context. They never grant tools, capabilities, or
approval authority. The catalog accepts directories containing ``skill.json``
files and optional instruction files.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

SKILL_MANIFEST = "skill.json"
MAX_SKILLS = 256
MAX_SKILL_BYTES = 64 * 1024
MAX_SKILL_ID_LENGTH = 64
VALID_POLICIES = ("automatic", "explicit", "hybrid", "disabled")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_ID_RE = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?")


class SkillCatalogError(ValueError):
    """Raised when a local skill catalog is invalid or unsafe to load."""


class SkillRoutingError(ValueError):
    """Raised when skill selection cannot satisfy the requested policy."""


@dataclass(frozen=True)
class SkillDefinition:
    """One validated skill and its advisory instructions."""

    skill_id: str
    version: str
    summary: str
    domains: tuple[str, ...]
    keywords: tuple[str, ...]
    instructions: str
    requires: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    risk: str = "low"
    cost: str = "low"
    source: str = "<memory>"
    #: File name patterns the checks apply to, such as `*.py`. Empty means
    #: the skill has no checks.
    files: tuple[str, ...] = ()
    #: Rules a file made under this skill must keep. See `check_problem`.
    checks: tuple[str, ...] = ()

    @property
    def digest(self) -> str:
        payload = {
            "id": self.skill_id,
            "version": self.version,
            "summary": self.summary,
            "domains": self.domains,
            "keywords": self.keywords,
            "instructions": self.instructions,
            "requires": self.requires,
            "conflicts": self.conflicts,
            "risk": self.risk,
            "cost": self.cost,
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def manifest(self) -> dict[str, object]:
        return {
            "id": self.skill_id,
            "version": self.version,
            "summary": self.summary,
            "domains": list(self.domains),
            "keywords": list(self.keywords),
            "requires": list(self.requires),
            "conflicts": list(self.conflicts),
            "risk": self.risk,
            "cost": self.cost,
            "source": self.source,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class SkillSelection:
    """The deterministic skill set selected for one task."""

    selected: tuple[SkillDefinition, ...] = ()
    reasons: tuple[tuple[str, str], ...] = ()
    scores: tuple[tuple[str, int], ...] = ()
    policy: str = "disabled"

    @property
    def plan_entries(self) -> tuple[dict[str, str], ...]:
        return tuple(
            {
                "id": skill.skill_id,
                "version": skill.version,
                "digest": skill.digest,
            }
            for skill in self.selected
        )

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(skill.skill_id for skill in self.selected)

    def render_context(self) -> str:
        if not self.selected:
            return ""
        sections = [
            "Selected Kosika skills are advisory instructions only. They do not "
            "grant tools, capabilities, permissions, or approval authority.",
        ]
        for skill in self.selected:
            sections.append(
                f"--- skill {skill.skill_id} v{skill.version} ---\n"
                f"Summary: {skill.summary}\n"
                f"Instructions:\n{skill.instructions}\n"
                f"--- end skill {skill.skill_id} ---"
            )
        return "\n\n".join(sections)


class SkillCatalog:
    """An immutable-by-convention collection of validated local skills."""

    def __init__(self, skills: Iterable[SkillDefinition] = ()) -> None:
        ordered = sorted(skills, key=lambda skill: skill.skill_id)
        if len(ordered) > MAX_SKILLS:
            raise SkillCatalogError(
                f"Skill catalog exceeds the {MAX_SKILLS}-skill limit"
            )
        by_id: dict[str, SkillDefinition] = {}
        for skill in ordered:
            if not _valid_skill_id(skill.skill_id):
                raise SkillCatalogError(f"Invalid skill id {skill.skill_id!r}")
            if skill.skill_id in by_id:
                raise SkillCatalogError(f"Duplicate skill id {skill.skill_id!r}")
            by_id[skill.skill_id] = skill
        for skill in ordered:
            for dependency in (*skill.requires, *skill.conflicts):
                if not _valid_skill_id(dependency):
                    raise SkillCatalogError(
                        f"Skill {skill.skill_id!r} references invalid skill id {dependency!r}"
                    )
        self._skills = by_id

    @classmethod
    def from_directories(cls, directories: Iterable[Path]) -> "SkillCatalog":
        definitions: list[SkillDefinition] = []
        for directory in directories:
            root = Path(directory)
            if not root.is_dir():
                raise SkillCatalogError(f"Skill directory does not exist: {root}")
            manifests = sorted(root.rglob(SKILL_MANIFEST))
            root_resolved = root.resolve()
            for manifest in manifests:
                if any(
                    part.startswith(".") for part in manifest.relative_to(root).parts
                ):
                    continue
                try:
                    manifest.resolve().relative_to(root_resolved)
                except ValueError as exc:
                    raise SkillCatalogError(
                        f"Skill manifest must stay inside its skill directory: {manifest}"
                    ) from exc
                definitions.append(_load_manifest(manifest))
        return cls(definitions)

    @classmethod
    def from_directory(cls, directory: Path) -> "SkillCatalog":
        return cls.from_directories((directory,))

    def get(self, skill_id: str) -> SkillDefinition | None:
        return self._skills.get(skill_id)

    def require(self, skill_id: str) -> SkillDefinition:
        skill = self.get(skill_id)
        if skill is None:
            raise SkillRoutingError(f"Unknown skill {skill_id!r}")
        return skill

    def __iter__(self):
        return iter(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)


class SkillRouter:
    """Select explicit and relevant supporting skills without model guesses."""

    def __init__(self, catalog: SkillCatalog) -> None:
        self.catalog = catalog

    def select(
        self,
        task: str,
        *,
        explicit: Iterable[str] = (),
        policy: str = "hybrid",
        max_skills: int = 6,
    ) -> SkillSelection:
        if not isinstance(task, str) or not task.strip():
            raise SkillRoutingError("Skill routing task cannot be empty")
        if policy not in VALID_POLICIES:
            raise SkillRoutingError(
                f"Unknown skill policy {policy!r}; expected one of {VALID_POLICIES}"
            )
        if max_skills <= 0:
            raise SkillRoutingError("Skill routing max_skills must be positive")
        explicit_ids = tuple(dict.fromkeys(explicit))
        if policy == "disabled":
            if explicit_ids:
                raise SkillRoutingError(
                    "Disabled skill policy cannot use explicit skills"
                )
            return SkillSelection(policy=policy)

        selected: list[SkillDefinition] = []
        reasons: dict[str, str] = {}
        scores = self._scores(task)

        def add(skill_id: str, reason: str, required: bool) -> bool:
            if skill_id in {skill.skill_id for skill in selected}:
                return True
            skill = self.catalog.require(skill_id)
            dependency_order: list[SkillDefinition] = []
            visiting: set[str] = set()

            def collect(current: SkillDefinition) -> None:
                if current.skill_id in {item.skill_id for item in dependency_order}:
                    return
                if current.skill_id in visiting:
                    raise SkillRoutingError(
                        f"Skill dependency cycle includes {current.skill_id!r}"
                    )
                visiting.add(current.skill_id)
                for dependency_id in current.requires:
                    dependency = self.catalog.require(dependency_id)
                    collect(dependency)
                visiting.remove(current.skill_id)
                dependency_order.append(current)

            collect(skill)
            new_items = [
                item
                for item in dependency_order
                if item.skill_id not in {existing.skill_id for existing in selected}
            ]
            if len(selected) + len(new_items) > max_skills:
                if required:
                    raise SkillRoutingError(
                        f"Skill selection exceeds the {max_skills}-skill limit"
                    )
                return False
            for item in new_items:
                conflicting = {
                    existing.skill_id
                    for existing in selected
                    if item.skill_id in existing.conflicts
                    or existing.skill_id in item.conflicts
                }
                if conflicting:
                    if required:
                        raise SkillRoutingError(
                            f"Skill {item.skill_id!r} conflicts with "
                            f"{', '.join(sorted(conflicting))}"
                        )
                    return False
                selected.append(item)
                reasons[item.skill_id] = (
                    reason if item.skill_id == skill_id else f"required by {skill_id}"
                )
            return True

        for skill_id in explicit_ids:
            add(skill_id, "explicitly requested", True)

        if policy in ("automatic", "hybrid"):
            for skill_id, score in sorted(
                scores.items(), key=lambda item: (-item[1], item[0])
            ):
                if score <= 0 or skill_id in {skill.skill_id for skill in selected}:
                    continue
                add(skill_id, f"matched task vocabulary (score {score})", False)

        return SkillSelection(
            selected=tuple(selected),
            reasons=tuple(sorted(reasons.items())),
            scores=tuple(sorted(scores.items())),
            policy=policy,
        )

    def _scores(self, task: str) -> dict[str, int]:
        task_terms = set(_tokens(task))
        scores: dict[str, int] = {}
        for skill in self.catalog:
            score = 0
            score += 4 * len(task_terms & set(_tokens(" ".join(skill.keywords))))
            score += 3 * len(task_terms & set(_tokens(" ".join(skill.domains))))
            score += len(task_terms & set(_tokens(skill.skill_id)))
            score += len(task_terms & set(_tokens(skill.summary)))
            scores[skill.skill_id] = score
        return scores


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(value.lower()))


def _valid_skill_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= MAX_SKILL_ID_LENGTH
        and bool(_ID_RE.fullmatch(value))
    )


def _string(data: Mapping[str, object], key: str, *, required: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        if required:
            raise SkillCatalogError(
                f"Skill manifest field {key!r} must be non-empty text"
            )
        return ""
    return value.strip()


def _strings(data: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise SkillCatalogError(f"Skill manifest field {key!r} must be a text list")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _load_manifest(path: Path) -> SkillDefinition:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SkillCatalogError(f"Cannot read skill manifest {path}: {exc}") from exc
    if len(raw) > MAX_SKILL_BYTES:
        raise SkillCatalogError(
            f"Skill manifest exceeds the {MAX_SKILL_BYTES}-byte limit"
        )
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillCatalogError(f"Invalid skill manifest {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillCatalogError(f"Skill manifest {path} must be a JSON object")
    skill_id = _string(data, "id", required=True)
    if not _valid_skill_id(skill_id):
        raise SkillCatalogError(f"Invalid skill id {skill_id!r}")
    instructions = _string(data, "instructions")
    instruction_file = data.get("instructions_file", "SKILL.md")
    if not isinstance(instruction_file, str) or not instruction_file:
        raise SkillCatalogError("Skill instructions_file must be text")
    if not instructions:
        candidate = (path.parent / instruction_file).resolve()
        try:
            candidate.relative_to(path.parent.resolve())
        except ValueError as exc:
            raise SkillCatalogError(
                "Skill instructions_file must stay inside its skill directory"
            ) from exc
        try:
            instructions = candidate.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillCatalogError(
                f"Cannot read skill instructions {candidate}: {exc}"
            ) from exc
    if len(instructions.encode("utf-8")) > MAX_SKILL_BYTES:
        raise SkillCatalogError(
            f"Skill instructions exceed the {MAX_SKILL_BYTES}-byte limit"
        )
    return SkillDefinition(
        skill_id=skill_id,
        version=_string(data, "version", required=True),
        summary=_string(data, "summary", required=True),
        domains=_strings(data, "domains"),
        keywords=_strings(data, "keywords"),
        instructions=instructions.strip(),
        requires=_strings(data, "requires"),
        conflicts=_strings(data, "conflicts"),
        risk=_string(data, "risk") or "low",
        cost=_string(data, "cost") or "low",
        source=path.as_posix(),
    )


#: The folder beside a program that holds its skills, one note each.
SKILLS_FOLDER = "skills"
#: At most this many skills are given to an agent for one request.
MAX_PICKED = 3
_WHEN_RE = re.compile(r"^\s*when\s*:\s*(.+)$", re.IGNORECASE)
_HEAD_RE = re.compile(r"^\s*(files|check)\s*:\s*(.+)$", re.IGNORECASE)
_CHECK_RE = re.compile(
    r'^(starts with|contains|every line starts with)\s+"(.+)"\s*$', re.IGNORECASE
)


def _stem(word: str) -> str:
    """A word without a plural ending, so "readmes" finds "readme"."""

    if word.endswith("ies") and len(word) > 5:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def load_skill_note(path: Path) -> SkillDefinition:
    """Read one skill written as a note.

        # What a good readme has
        When: readme, explain a folder

        Start with one line that says what the folder is for. ...

    The first line is what the skill is. The `When:` line lists the words
    that, found in a request, mean the skill fits. The rest is the know-how
    given to the model. The file's name is the skill's name.
    """

    skill_id = path.stem.lower().replace(" ", "-").replace("_", "-")
    if not _valid_skill_id(skill_id):
        raise SkillCatalogError(
            f"Skill file {path.name!r} needs a name of letters, digits and dashes"
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SkillCatalogError(f"Cannot read skill {path.name}: {exc}") from exc
    if len(raw) > MAX_SKILL_BYTES:
        raise SkillCatalogError(
            f"Skill {path.name} is larger than {MAX_SKILL_BYTES} bytes"
        )
    try:
        lines = raw.decode("utf-8").replace("\r\n", "\n").split("\n")
    except UnicodeDecodeError as exc:
        raise SkillCatalogError(f"Skill {path.name} is not UTF-8 text") from exc
    summary = ""
    keywords: tuple[str, ...] = ()
    files: tuple[str, ...] = ()
    checks: list[str] = []
    body: list[str] = []
    for line in lines:
        when = _WHEN_RE.match(line)
        # `Files:` and `Check:` lines belong to the top of the note, before
        # the know-how begins, so know-how may itself use those words.
        heading = None if body else _HEAD_RE.match(line)
        if not summary and line.strip():
            summary = line.strip().lstrip("#").strip()
        elif when and not keywords:
            keywords = tuple(
                dict.fromkeys(
                    part.strip().lower()
                    for part in when.group(1).split(",")
                    if part.strip()
                )
            )
        elif heading and heading.group(1).lower() == "files":
            files = tuple(
                part.strip().lower()
                for part in heading.group(2).split(",")
                if part.strip()
            )
        elif heading:
            rule = heading.group(2).strip()
            if _CHECK_RE.match(rule) is None:
                raise SkillCatalogError(
                    f"Skill {path.name} has a check Kosika cannot read: {rule!r}. "
                    'A check is `starts with "..."`, `contains "..."` or '
                    '`every line starts with "..."`'
                )
            checks.append(rule)
        elif line.strip() or body:
            body.append(line)
    if checks and not files:
        raise SkillCatalogError(
            f"Skill {path.name} has checks, so it needs a line such as "
            "`Files: *.py` saying which files they are for"
        )
    instructions = "\n".join(body).strip()
    if not summary:
        raise SkillCatalogError(f"Skill {path.name} is empty")
    if not keywords:
        raise SkillCatalogError(
            f"Skill {path.name} needs a line such as `When: readme, notes` "
            "naming the words that mean it fits"
        )
    if not instructions:
        raise SkillCatalogError(f"Skill {path.name} has no know-how under its title")
    return SkillDefinition(
        skill_id=skill_id,
        version="1",
        summary=summary,
        domains=(),
        keywords=keywords,
        instructions=instructions,
        source=path.as_posix(),
        files=files,
        checks=tuple(checks),
    )


def check_problem(rule: str, text: str) -> str | None:
    """Why a text breaks one rule, or None if it keeps it.

    A rule is `starts with "..."`, `contains "..."`, or
    `every line starts with "..."`, where empty lines do not count.
    """

    found = _CHECK_RE.match(rule)
    if found is None:
        return None
    kind, wanted = found.group(1).lower(), found.group(2)
    if kind == "starts with":
        return (
            None
            if text.lstrip().startswith(wanted)
            else f"it must start with {wanted!r}"
        )
    if kind == "contains":
        return None if wanted in text else f"it must contain {wanted!r}"
    lines = [line for line in text.split("\n") if line.strip()]
    if all(line.startswith(wanted) for line in lines):
        return None
    return f"every line must start with {wanted!r}"


def plan_problems(
    selection: SkillSelection | None,
    request: str,
    actions: Iterable[tuple[str, Mapping[str, object]]],
) -> list[str]:
    """What is wrong with the files a plan would write, in plain words.

    Only actions that write text to a path are looked at. A file must not be
    empty, and must not just repeat the request. A file whose name fits a
    picked skill's `Files:` pattern must keep that skill's checks.
    """

    problems: list[str] = []
    asked = " ".join(_tokens(request))
    seen: set[str] = set()
    for _tool, arguments in actions:
        path, text = arguments.get("path"), arguments.get("text")
        if not isinstance(path, str) or not isinstance(text, str):
            continue
        name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
        where = path.replace("\\", "/").removeprefix("./").lower()
        if where in seen:
            problems.append(f"{path}: the plan writes this file twice; write it once")
            continue
        seen.add(where)
        if not text.strip():
            problems.append(f"{path}: the file would be empty")
            continue
        if "\\n" in text and "\n" not in text:
            problems.append(
                f"{path}: line breaks were written as the two characters "
                "backslash and n; use real line breaks"
            )
            continue
        said = _tokens(text)
        if len(said) >= 4 and " ".join(said) in asked:
            problems.append(
                f"{path}: the file only repeats the request; write what was asked for"
            )
            continue
        # When the name fits more than one picked skill, the one whose
        # pattern says the most about the name is the one that applies:
        # readme.txt is a readme before it is a text file.
        fitting = [
            (max(len(pattern.replace("*", "")) for pattern in matched), skill)
            for skill in (selection.selected if selection is not None else ())
            if (
                matched := [
                    pattern
                    for pattern in skill.files
                    if fnmatch.fnmatchcase(name, pattern)
                ]
            )
        ]
        if not fitting:
            continue
        skill = max(fitting, key=lambda item: item[0])[1]
        for rule in skill.checks:
            why = check_problem(rule, text)
            if why is not None:
                problems.append(f"{path}: {why} ({skill.summary})")
    return problems


def load_skill_notes(folder: Path) -> SkillCatalog:
    """Every skill note in a folder, or an empty catalog if there is none."""

    if not folder.is_dir():
        return SkillCatalog()
    return SkillCatalog(
        load_skill_note(path) for path in sorted(folder.glob("*.md")) if path.is_file()
    )


def pick_skills(
    catalog: SkillCatalog, request: str, limit: int = MAX_PICKED
) -> SkillSelection:
    """The skills that fit a request, best first, with the words that chose them.

    A skill fits when one of its `When:` words or phrases is in the request.
    Nothing is guessed and no model is asked: the same request always picks
    the same skills.
    """

    words = [_stem(word) for word in _tokens(request)]
    said = " " + " ".join(words) + " "
    found: list[tuple[int, str, SkillDefinition, list[str]]] = []
    for skill in catalog:
        stems = {
            " ".join(_stem(word) for word in _tokens(keyword)): keyword
            for keyword in reversed(skill.keywords)
        }
        hits = [
            keyword
            for keyword in skill.keywords
            if stems.get(" ".join(_stem(word) for word in _tokens(keyword))) == keyword
            and " " + " ".join(_stem(word) for word in _tokens(keyword)) + " " in said
        ]
        if hits:
            found.append((-len(hits), skill.skill_id, skill, hits))
    found.sort(key=lambda item: item[:2])
    chosen = found[:limit]
    return SkillSelection(
        selected=tuple(item[2] for item in chosen),
        reasons=tuple(
            (item[1], "the request says: " + ", ".join(item[3])) for item in chosen
        ),
        scores=tuple((item[1], -item[0]) for item in found),
        policy="automatic",
    )


#: What the model is told when it is asked which skills fit.
CHOOSE = (
    "You choose which notes of know-how fit a request. Choose a note only if "
    "the request clearly asks for the kind of thing the note is about. Most "
    "requests fit none; then answer with an empty list. Answer with one JSON "
    'object and nothing else: {"skills": ["name", ...]}'
)


def choose_question(catalog: SkillCatalog, request: str) -> tuple[str, str]:
    """What to ask a model so that it chooses skills: system words, then the ask."""

    menu = "\n".join(
        f"- {skill.skill_id}: {skill.summary}. Used for: {', '.join(skill.keywords)}"
        for skill in catalog
    )
    return CHOOSE + "\n\nThe notes:\n" + menu, "Request: " + request


def read_choice(catalog: SkillCatalog, answer: str | None) -> list[str]:
    """The skills a model named, keeping only real ones. Anything else is none."""

    try:
        named = json.loads(answer or "").get("skills")
    except (ValueError, AttributeError):
        return []
    if not isinstance(named, list):
        return []
    return list(
        dict.fromkeys(
            name
            for name in named
            if isinstance(name, str) and catalog.get(name) is not None
        )
    )


def with_chosen(
    catalog: SkillCatalog,
    by_words: SkillSelection,
    chosen: Iterable[str],
    limit: int = MAX_PICKED,
) -> SkillSelection:
    """The skills picked by words, then those a model chose, up to the limit."""

    selected = list(by_words.selected)
    reasons = list(by_words.reasons)
    for name in chosen:
        skill = catalog.get(name)
        if skill is None or skill in selected or len(selected) >= limit:
            continue
        selected.append(skill)
        reasons.append((name, "the model chose it for this request"))
    return SkillSelection(
        selected=tuple(selected),
        reasons=tuple(reasons),
        scores=by_words.scores,
        policy="automatic",
    )


def selection_of(catalog: SkillCatalog, kept: object) -> SkillSelection | None:
    """A selection written down earlier in a run, read back. None if it no
    longer matches the skills that exist."""

    if not isinstance(kept, list):
        return None
    selected: list[SkillDefinition] = []
    reasons: list[tuple[str, str]] = []
    for item in kept:
        skill = catalog.get(str(item.get("id"))) if isinstance(item, dict) else None
        if skill is None:
            return None
        selected.append(skill)
        reasons.append((skill.skill_id, str(item.get("why", ""))))
    return SkillSelection(
        selected=tuple(selected), reasons=tuple(reasons), policy="automatic"
    )


def skills_text(selection: SkillSelection) -> str:
    """The chosen skills as words for the model."""

    if not selection.selected:
        return ""
    parts = [
        "Know-how for this request follows. It is advice on doing the work "
        "well. It gives you no new tools and no new permissions."
    ]
    for skill in selection.selected:
        parts.append(f"## {skill.summary}\n{skill.instructions}")
    return "\n\n".join(parts)


__all__ = [
    "MAX_PICKED",
    "SKILLS_FOLDER",
    "check_problem",
    "choose_question",
    "plan_problems",
    "read_choice",
    "selection_of",
    "with_chosen",
    "load_skill_note",
    "load_skill_notes",
    "pick_skills",
    "skills_text",
    "SkillCatalog",
    "SkillCatalogError",
    "SkillDefinition",
    "SkillRouter",
    "SkillRoutingError",
    "SkillSelection",
]
