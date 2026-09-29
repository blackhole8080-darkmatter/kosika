"""Local skill discovery and deterministic multi-skill selection.

Skills are advisory model context. They never grant tools, capabilities, or
approval authority. The catalog accepts directories containing ``skill.json``
files and optional instruction files.
"""

from __future__ import annotations

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
            raise SkillCatalogError(f"Skill catalog exceeds the {MAX_SKILLS}-skill limit")
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
                if any(part.startswith(".") for part in manifest.relative_to(root).parts):
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
                raise SkillRoutingError("Disabled skill policy cannot use explicit skills")
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
    return isinstance(value, str) and len(value) <= MAX_SKILL_ID_LENGTH and bool(
        _ID_RE.fullmatch(value)
    )


def _string(data: Mapping[str, object], key: str, *, required: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        if required:
            raise SkillCatalogError(f"Skill manifest field {key!r} must be non-empty text")
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
        raise SkillCatalogError(f"Skill manifest exceeds the {MAX_SKILL_BYTES}-byte limit")
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
            raise SkillCatalogError("Skill instructions_file must stay inside its skill directory") from exc
        try:
            instructions = candidate.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillCatalogError(f"Cannot read skill instructions {candidate}: {exc}") from exc
    if len(instructions.encode("utf-8")) > MAX_SKILL_BYTES:
        raise SkillCatalogError(f"Skill instructions exceed the {MAX_SKILL_BYTES}-byte limit")
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


__all__ = [
    "SkillCatalog",
    "SkillCatalogError",
    "SkillDefinition",
    "SkillRouter",
    "SkillRoutingError",
    "SkillSelection",
]
