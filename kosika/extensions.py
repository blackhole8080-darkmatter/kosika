from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ExtensionManifestError(ValueError):
    """Raised when an extension manifest is invalid or cannot be read."""


_EXTENSION_KINDS = frozenset(
    {
        "model",
        "tool",
        "workflow",
        "record",
        "memory",
        "cognitive",
        "language",
        "design",
        "deployment",
    }
)
_LIFECYCLE_HOOKS = frozenset(
    {
        "discover",
        "install",
        "activate",
        "deactivate",
        "remove",
        "healthCheck",
        "rollback",
    }
)
_ROLLBACK_STRATEGIES = frozenset({"disable", "restore", "compensate"})
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_PERMISSION_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[a-z][a-z0-9_.-]*$")
_ENTRYPOINT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*:[A-Za-z_][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class ExtensionCompatibility:
    min_version: str
    max_version: str


@dataclass(frozen=True)
class ExtensionContribution:
    kind: str
    name: str
    entrypoint: str | None
    permissions: tuple[str, ...]


@dataclass(frozen=True)
class ExtensionDependency:
    id: str
    constraint: str
    optional: bool


@dataclass(frozen=True)
class ExtensionHealthCheck:
    name: str
    entrypoint: str
    timeout_ms: int
    required: bool


@dataclass(frozen=True)
class ExtensionRollback:
    strategy: str
    entrypoint: str | None


@dataclass(frozen=True)
class ExtensionManifest:
    manifest_version: int
    id: str
    version: str
    author: str
    license: str
    compatibility: ExtensionCompatibility
    contributions: tuple[ExtensionContribution, ...]
    dependencies: tuple[ExtensionDependency, ...]
    permissions: tuple[str, ...]
    lifecycle: tuple[tuple[str, str], ...]
    health_checks: tuple[ExtensionHealthCheck, ...]
    rollback: ExtensionRollback


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExtensionManifestError(f"{label} must be an object")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExtensionManifestError(f"{label} must be a non-empty string")
    return value


def _name(value: Any, label: str) -> str:
    result = _string(value, label)
    if not _NAME_RE.fullmatch(result):
        raise ExtensionManifestError(
            f"{label} must contain only letters, numbers, '.', '_' or '-'; "
            "it must start with a letter"
        )
    return result


def _version(value: Any, label: str) -> str:
    result = _string(value, label)
    if not _VERSION_RE.fullmatch(result):
        raise ExtensionManifestError(f"{label} must be a semantic version")
    return result


def _entrypoint(value: Any, label: str) -> str:
    result = _string(value, label)
    if not _ENTRYPOINT_RE.fullmatch(result):
        raise ExtensionManifestError(f"{label} must use a module:function entrypoint")
    return result


def _permission(value: Any, label: str) -> str:
    result = _string(value, label)
    if not _PERMISSION_RE.fullmatch(result):
        raise ExtensionManifestError(f"{label} must use a namespace:action permission")
    return result


def _string_list(value: Any, label: str, validator: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ExtensionManifestError(f"{label} must be an array")
    result = tuple(
        validator(item, f"{label}[{index}]") for index, item in enumerate(value)
    )
    if len(set(result)) != len(result):
        raise ExtensionManifestError(f"{label} must not contain duplicates")
    return result


def _keys(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ExtensionManifestError(f"{label} contains unknown fields: {unknown!r}")


def _parse_contributions(
    value: Any, permissions: tuple[str, ...]
) -> tuple[ExtensionContribution, ...]:
    if not isinstance(value, list):
        raise ExtensionManifestError("contributions must be an array")
    result: list[ExtensionContribution] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        item = _object(raw, f"contributions[{index}]")
        _keys(
            item,
            {"kind", "name", "entrypoint", "permissions"},
            f"contributions[{index}]",
        )
        kind = _string(item.get("kind"), f"contributions[{index}].kind")
        if kind not in _EXTENSION_KINDS:
            raise ExtensionManifestError(
                f"contributions[{index}].kind is unsupported: {kind!r}"
            )
        name = _name(item.get("name"), f"contributions[{index}].name")
        identity = (kind, name)
        if identity in seen:
            raise ExtensionManifestError(
                f"duplicate extension contribution: {kind}:{name}"
            )
        seen.add(identity)
        entrypoint = item.get("entrypoint")
        parsed_entrypoint = (
            None
            if entrypoint is None
            else _entrypoint(entrypoint, f"contributions[{index}].entrypoint")
        )
        contribution_permissions = _string_list(
            item.get("permissions"),
            f"contributions[{index}].permissions",
            _permission,
        )
        undeclared = sorted(set(contribution_permissions) - set(permissions))
        if undeclared:
            raise ExtensionManifestError(
                f"contributions[{index}] requests undeclared permissions: {undeclared!r}"
            )
        result.append(
            ExtensionContribution(
                kind, name, parsed_entrypoint, contribution_permissions
            )
        )
    if not result:
        raise ExtensionManifestError("contributions must contain at least one item")
    return tuple(result)


def _parse_dependencies(value: Any) -> tuple[ExtensionDependency, ...]:
    if not isinstance(value, list):
        raise ExtensionManifestError("dependencies must be an array")
    result: list[ExtensionDependency] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item = _object(raw, f"dependencies[{index}]")
        _keys(item, {"id", "constraint", "optional"}, f"dependencies[{index}]")
        dependency_id = _name(item.get("id"), f"dependencies[{index}].id")
        if dependency_id in seen:
            raise ExtensionManifestError(
                f"dependencies must not repeat {dependency_id!r}"
            )
        seen.add(dependency_id)
        constraint = _string(
            item.get("constraint"), f"dependencies[{index}].constraint"
        )
        optional = item.get("optional", False)
        if not isinstance(optional, bool):
            raise ExtensionManifestError(
                f"dependencies[{index}].optional must be boolean"
            )
        result.append(ExtensionDependency(dependency_id, constraint, optional))
    return tuple(result)


def _parse_lifecycle(value: Any) -> tuple[tuple[str, str], ...]:
    item = _object(value, "lifecycle")
    _keys(item, set(_LIFECYCLE_HOOKS), "lifecycle")
    return tuple(
        (name, _entrypoint(item[name], f"lifecycle.{name}")) for name in sorted(item)
    )


def _parse_health_checks(value: Any) -> tuple[ExtensionHealthCheck, ...]:
    if not isinstance(value, list):
        raise ExtensionManifestError("healthChecks must be an array")
    result: list[ExtensionHealthCheck] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item = _object(raw, f"healthChecks[{index}]")
        _keys(
            item,
            {"name", "entrypoint", "timeoutMs", "required"},
            f"healthChecks[{index}]",
        )
        name = _name(item.get("name"), f"healthChecks[{index}].name")
        if name in seen:
            raise ExtensionManifestError(f"duplicate health check: {name!r}")
        seen.add(name)
        timeout_ms = item.get("timeoutMs")
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or timeout_ms <= 0
        ):
            raise ExtensionManifestError(
                f"healthChecks[{index}].timeoutMs must be a positive integer"
            )
        required = item.get("required", True)
        if not isinstance(required, bool):
            raise ExtensionManifestError(
                f"healthChecks[{index}].required must be boolean"
            )
        result.append(
            ExtensionHealthCheck(
                name,
                _entrypoint(
                    item.get("entrypoint"), f"healthChecks[{index}].entrypoint"
                ),
                timeout_ms,
                required,
            )
        )
    return tuple(result)


def _parse_rollback(value: Any) -> ExtensionRollback:
    item = _object(value, "rollback")
    _keys(item, {"strategy", "entrypoint"}, "rollback")
    strategy = _string(item.get("strategy"), "rollback.strategy")
    if strategy not in _ROLLBACK_STRATEGIES:
        raise ExtensionManifestError(
            f"rollback.strategy must be one of {sorted(_ROLLBACK_STRATEGIES)!r}"
        )
    entrypoint = item.get("entrypoint")
    parsed_entrypoint = (
        None if entrypoint is None else _entrypoint(entrypoint, "rollback.entrypoint")
    )
    if strategy != "disable" and parsed_entrypoint is None:
        raise ExtensionManifestError(
            f"rollback strategy {strategy!r} requires rollback.entrypoint"
        )
    return ExtensionRollback(strategy, parsed_entrypoint)


def parse_extension_manifest(data: Any) -> ExtensionManifest:
    item = _object(data, "Extension manifest")
    required = {
        "manifestVersion",
        "id",
        "version",
        "author",
        "license",
        "compatibility",
        "contributions",
        "dependencies",
        "permissions",
        "lifecycle",
        "healthChecks",
        "rollback",
    }
    _keys(item, required, "Extension manifest")
    missing = sorted(required - set(item))
    if missing:
        raise ExtensionManifestError(
            f"Extension manifest is missing required fields: {missing!r}"
        )
    manifest_version = item["manifestVersion"]
    if isinstance(manifest_version, bool) or manifest_version != 1:
        raise ExtensionManifestError("manifestVersion must be the integer 1")
    extension_id = _name(item["id"], "id")
    version = _version(item["version"], "version")
    author = _string(item["author"], "author")
    license_name = _string(item["license"], "license")

    compatibility = _object(item["compatibility"], "compatibility")
    _keys(compatibility, {"minVersion", "maxVersion"}, "compatibility")
    min_version = _version(compatibility.get("minVersion"), "compatibility.minVersion")
    max_version = _version(compatibility.get("maxVersion"), "compatibility.maxVersion")

    permissions = _string_list(item["permissions"], "permissions", _permission)
    contributions = _parse_contributions(item["contributions"], permissions)
    dependencies = _parse_dependencies(item["dependencies"])
    for dependency in dependencies:
        if dependency.id == extension_id:
            raise ExtensionManifestError("an extension cannot depend on itself")

    return ExtensionManifest(
        manifest_version=manifest_version,
        id=extension_id,
        version=version,
        author=author,
        license=license_name,
        compatibility=ExtensionCompatibility(min_version, max_version),
        contributions=contributions,
        dependencies=dependencies,
        permissions=permissions,
        lifecycle=_parse_lifecycle(item["lifecycle"]),
        health_checks=_parse_health_checks(item["healthChecks"]),
        rollback=_parse_rollback(item["rollback"]),
    )


def extension_manifest_to_dict(manifest: ExtensionManifest) -> dict[str, Any]:
    return {
        "manifestVersion": manifest.manifest_version,
        "id": manifest.id,
        "version": manifest.version,
        "author": manifest.author,
        "license": manifest.license,
        "compatibility": {
            "minVersion": manifest.compatibility.min_version,
            "maxVersion": manifest.compatibility.max_version,
        },
        "contributions": [
            {
                "kind": contribution.kind,
                "name": contribution.name,
                **(
                    {"entrypoint": contribution.entrypoint}
                    if contribution.entrypoint is not None
                    else {}
                ),
                "permissions": list(contribution.permissions),
            }
            for contribution in manifest.contributions
        ],
        "dependencies": [
            {
                "id": dependency.id,
                "constraint": dependency.constraint,
                "optional": dependency.optional,
            }
            for dependency in manifest.dependencies
        ],
        "permissions": list(manifest.permissions),
        "lifecycle": dict(manifest.lifecycle),
        "healthChecks": [
            {
                "name": health_check.name,
                "entrypoint": health_check.entrypoint,
                "timeoutMs": health_check.timeout_ms,
                "required": health_check.required,
            }
            for health_check in manifest.health_checks
        ],
        "rollback": {
            "strategy": manifest.rollback.strategy,
            **(
                {"entrypoint": manifest.rollback.entrypoint}
                if manifest.rollback.entrypoint is not None
                else {}
            ),
        },
    }


def extension_manifest_digest(manifest: ExtensionManifest) -> str:
    canonical = json.dumps(
        extension_manifest_to_dict(manifest),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_extension_manifest(path: str | Path) -> ExtensionManifest:
    manifest_path = Path(path)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtensionManifestError(
            f"Could not read extension manifest {manifest_path}: {exc}"
        ) from exc
    return parse_extension_manifest(data)
