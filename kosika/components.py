from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

from .ast import Program, ToolDecl
from .model_steps import strict_json
from .schema import (
    SchemaValidationError,
    schema_for_record,
    schema_for_tool_input,
    schemas_equal,
    validate,
)

COMPONENT_SCHEMA = "kosika.component/v1"
COMPONENT_CALL_SCHEMA = "kosika.component-call/v1"
COMPONENT_RESULT_SCHEMA = "kosika.component-result/v1"
COMPONENT_PROTOCOL = "json-stdio/v1"
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_TIMEOUT_MS = 600_000

_ACCESS_LEVELS = frozenset({"observe", "analyze", "prepare", "execute"})
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_AUTHORITY_RE = re.compile(r"^[a-z][a-z0-9_.-]*:[a-z][a-z0-9_.-]*$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class ComponentContractError(ValueError):
    """Raised when a component contract cannot be trusted or bound."""


class ComponentExecutionError(RuntimeError):
    """Raised when a component process fails at a host-controlled boundary."""

    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ComponentReportedError(ComponentExecutionError):
    """A structured error declared by and returned from a component."""


@dataclass(frozen=True)
class ComponentImplementation:
    artifact: str
    sha256: str
    protocol: str = COMPONENT_PROTOCOL


@dataclass(frozen=True)
class ComponentErrorSpec:
    code: str
    retryable: bool


@dataclass(frozen=True)
class ComponentCancellation:
    mode: str
    timeout_ms: int


@dataclass(frozen=True)
class ComponentContract:
    component_id: str
    version: str
    tool_name: str
    operation: str
    implementation: ComponentImplementation
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    access: str
    effects: tuple[str, ...]
    capabilities: tuple[str, ...]
    errors: tuple[ComponentErrorSpec, ...]
    cancellation: ComponentCancellation
    schema: str = COMPONENT_SCHEMA

    @property
    def identity(self) -> dict[str, str]:
        return {
            "id": self.component_id,
            "version": self.version,
            "artifactSha256": self.implementation.sha256,
            "contractDigest": component_contract_digest(self),
        }


@dataclass(frozen=True)
class ComponentToolBinding:
    """Callable host binding for one already-validated Kosika tool declaration."""

    declaration: ToolDecl
    contract: ComponentContract
    artifact_root: Path
    runtime_command: tuple[str, ...]

    @property
    def identity(self) -> dict[str, str]:
        return self.contract.identity

    def __call__(self, declaration: ToolDecl, arguments: dict[str, Any]) -> Any:
        if declaration != self.declaration:
            raise ComponentContractError(
                f"Component {self.contract.component_id!r} was bound to "
                f"{self.declaration.name!r}, not {declaration.name!r}"
            )
        return execute_component(
            self.contract,
            self.artifact_root,
            self.runtime_command,
            arguments,
        )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ComponentContractError(f"{label} must be an object")
    return value


def _keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing:
        raise ComponentContractError(f"{label} is missing fields: {missing!r}")
    if unknown:
        raise ComponentContractError(f"{label} contains unknown fields: {unknown!r}")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ComponentContractError(f"{label} must be a non-empty string")
    return value


def _name(value: Any, label: str) -> str:
    result = _string(value, label)
    if not _NAME_RE.fullmatch(result):
        raise ComponentContractError(f"{label} is not a valid component name")
    return result


def _authority(value: Any, label: str) -> str:
    result = _string(value, label)
    if not _AUTHORITY_RE.fullmatch(result):
        raise ComponentContractError(
            f"{label} must use a namespace:action authority name"
        )
    return result


def _authorities(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ComponentContractError(f"{label} must be an array")
    result = tuple(
        _authority(item, f"{label}[{index}]") for index, item in enumerate(value)
    )
    if len(set(result)) != len(result):
        raise ComponentContractError(f"{label} must not contain duplicates")
    return tuple(sorted(result))


def _artifact(value: Any) -> str:
    result = _string(value, "implementation.artifact")
    if "\\" in result or ":" in result:
        raise ComponentContractError(
            "implementation.artifact must be a relative POSIX path"
        )
    path = PurePosixPath(result)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ComponentContractError(
            "implementation.artifact must stay inside the component root"
        )
    return result


def _schema(value: Any, label: str) -> dict[str, Any]:
    result = _object(value, label)
    if result.get("type") != "object":
        raise ComponentContractError(f"{label} must describe an object")
    return result


def parse_component_contract(data: Any) -> ComponentContract:
    """Validate and normalize a ``kosika.component/v1`` contract."""

    item = _object(data, "Component contract")
    _keys(
        item,
        {
            "schema",
            "id",
            "version",
            "tool",
            "implementation",
            "inputSchema",
            "outputSchema",
            "access",
            "effects",
            "capabilities",
            "errors",
            "cancellation",
        },
        "Component contract",
    )
    if item["schema"] != COMPONENT_SCHEMA:
        raise ComponentContractError(
            f"Unsupported component schema {item['schema']!r}; expected {COMPONENT_SCHEMA!r}"
        )
    component_id = _name(item["id"], "id")
    version = _string(item["version"], "version")
    if not _VERSION_RE.fullmatch(version):
        raise ComponentContractError("version must be semantic version")

    tool = _object(item["tool"], "tool")
    _keys(tool, {"name", "operation"}, "tool")
    tool_name = _name(tool["name"], "tool.name")
    operation = _name(tool["operation"], "tool.operation")

    implementation = _object(item["implementation"], "implementation")
    _keys(implementation, {"artifact", "sha256", "protocol"}, "implementation")
    artifact = _artifact(implementation["artifact"])
    artifact_sha256 = _string(implementation["sha256"], "implementation.sha256")
    if not _DIGEST_RE.fullmatch(artifact_sha256):
        raise ComponentContractError(
            "implementation.sha256 must be a lowercase SHA-256 digest"
        )
    if implementation["protocol"] != COMPONENT_PROTOCOL:
        raise ComponentContractError(
            f"implementation.protocol must be {COMPONENT_PROTOCOL!r}"
        )

    access = _string(item["access"], "access")
    if access not in _ACCESS_LEVELS:
        raise ComponentContractError(
            f"access must be one of {sorted(_ACCESS_LEVELS)!r}"
        )

    raw_errors = item["errors"]
    if not isinstance(raw_errors, list):
        raise ComponentContractError("errors must be an array")
    errors: list[ComponentErrorSpec] = []
    seen_errors: set[str] = set()
    for index, raw in enumerate(raw_errors):
        error = _object(raw, f"errors[{index}]")
        _keys(error, {"code", "retryable"}, f"errors[{index}]")
        code = _authority(error["code"], f"errors[{index}].code")
        if code in seen_errors:
            raise ComponentContractError(f"errors must not repeat {code!r}")
        if not isinstance(error["retryable"], bool):
            raise ComponentContractError(f"errors[{index}].retryable must be boolean")
        seen_errors.add(code)
        errors.append(ComponentErrorSpec(code, error["retryable"]))

    cancellation = _object(item["cancellation"], "cancellation")
    _keys(cancellation, {"mode", "timeoutMs"}, "cancellation")
    if cancellation["mode"] != "terminate":
        raise ComponentContractError("cancellation.mode must be 'terminate'")
    timeout_ms = cancellation["timeoutMs"]
    if (
        isinstance(timeout_ms, bool)
        or not isinstance(timeout_ms, int)
        or not 1 <= timeout_ms <= MAX_TIMEOUT_MS
    ):
        raise ComponentContractError(
            f"cancellation.timeoutMs must be an integer from 1 to {MAX_TIMEOUT_MS}"
        )

    return ComponentContract(
        component_id=component_id,
        version=version,
        tool_name=tool_name,
        operation=operation,
        implementation=ComponentImplementation(artifact, artifact_sha256),
        input_schema=_schema(item["inputSchema"], "inputSchema"),
        output_schema=_schema(item["outputSchema"], "outputSchema"),
        access=access,
        effects=_authorities(item["effects"], "effects"),
        capabilities=_authorities(item["capabilities"], "capabilities"),
        errors=tuple(sorted(errors, key=lambda error: error.code)),
        cancellation=ComponentCancellation("terminate", timeout_ms),
    )


def component_contract_to_dict(contract: ComponentContract) -> dict[str, Any]:
    return {
        "schema": contract.schema,
        "id": contract.component_id,
        "version": contract.version,
        "tool": {"name": contract.tool_name, "operation": contract.operation},
        "implementation": {
            "artifact": contract.implementation.artifact,
            "sha256": contract.implementation.sha256,
            "protocol": contract.implementation.protocol,
        },
        "inputSchema": contract.input_schema,
        "outputSchema": contract.output_schema,
        "access": contract.access,
        "effects": list(contract.effects),
        "capabilities": list(contract.capabilities),
        "errors": [
            {"code": error.code, "retryable": error.retryable}
            for error in contract.errors
        ],
        "cancellation": {
            "mode": contract.cancellation.mode,
            "timeoutMs": contract.cancellation.timeout_ms,
        },
    }


def component_contract_digest(contract: ComponentContract) -> str:
    payload = json.dumps(
        component_contract_to_dict(contract),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_component_contract(path: str | Path) -> ComponentContract:
    contract_path = Path(path)
    try:
        data = strict_json(contract_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ComponentContractError(
            f"Could not read component contract {contract_path}: {exc}"
        ) from exc
    return parse_component_contract(data)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_component_artifact(
    contract: ComponentContract, artifact_root: str | Path
) -> Path:
    """Resolve and hash the exact executable artifact without following it outside root."""

    root = Path(artifact_root).resolve()
    artifact = (
        root / Path(*PurePosixPath(contract.implementation.artifact).parts)
    ).resolve()
    try:
        artifact.relative_to(root)
    except ValueError as exc:
        raise ComponentContractError("Component artifact escapes its root") from exc
    try:
        size = artifact.stat().st_size
    except OSError as exc:
        raise ComponentContractError(
            f"Could not inspect component artifact: {exc}"
        ) from exc
    if not artifact.is_file():
        raise ComponentContractError("Component artifact must be a regular file")
    if size > MAX_ARTIFACT_BYTES:
        raise ComponentContractError(
            f"Component artifact exceeds {MAX_ARTIFACT_BYTES} bytes"
        )
    observed = _sha256_file(artifact)
    if observed != contract.implementation.sha256:
        raise ComponentContractError(
            "Component artifact digest does not match implementation.sha256"
        )
    return artifact


def _run_bounded_process(
    command: Sequence[str], request: bytes, *, cwd: Path, timeout: float
) -> bytes:
    if len(request) > MAX_MESSAGE_BYTES:
        raise ComponentExecutionError(
            f"Component request exceeds {MAX_MESSAGE_BYTES} bytes",
            code="component/request-too-large",
        )
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise ComponentExecutionError(
            f"Could not start component: {exc}",
            code="component/unavailable",
            retryable=True,
        ) from exc
    stdin = process.stdin
    stdout = process.stdout
    assert stdin is not None
    assert stdout is not None

    output = bytearray()
    output_too_large = False
    reader_error: BaseException | None = None
    writer_error: BaseException | None = None

    def read_stdout() -> None:
        nonlocal output_too_large, reader_error
        try:
            while chunk := stdout.read(64 * 1024):
                remaining = MAX_MESSAGE_BYTES - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    output_too_large = True
        except BaseException as exc:  # pragma: no cover - defensive thread boundary
            reader_error = exc

    def write_stdin() -> None:
        nonlocal writer_error
        try:
            stdin.write(request)
            stdin.flush()
        except BaseException as exc:  # pragma: no cover - defensive thread boundary
            writer_error = exc
        finally:
            stdin.close()

    reader = threading.Thread(
        target=read_stdout, name="kosika-component-stdout", daemon=True
    )
    writer = threading.Thread(
        target=write_stdin, name="kosika-component-stdin", daemon=True
    )
    reader.start()
    writer.start()
    try:
        return_code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.wait()
        # Cancellation latency must not be extended by blocked pipe workers.
        # They are daemon threads and the killed process can no longer produce
        # useful output; give them one short scheduling window, then return.
        reader.join(timeout=0.1)
        writer.join(timeout=0.1)
        if not reader.is_alive():
            stdout.close()
        raise ComponentExecutionError(
            f"Component exceeded its {timeout:g}s termination deadline",
            code="component/timeout",
            retryable=True,
        ) from exc
    reader.join(timeout=1)
    writer.join(timeout=1)
    stdout.close()

    if reader.is_alive() or writer.is_alive():
        raise ComponentExecutionError(
            "Component process streams did not close",
            code="component/protocol",
        )
    if reader_error is not None:
        raise ComponentExecutionError(
            f"Could not read component output: {reader_error}",
            code="component/protocol",
        ) from reader_error
    if return_code != 0:
        raise ComponentExecutionError(
            f"Component process exited with status {return_code}",
            code="component/process-failed",
            retryable=True,
        )
    if writer_error is not None:
        raise ComponentExecutionError(
            f"Could not send component request: {writer_error}",
            code="component/protocol",
        ) from writer_error
    if output_too_large:
        raise ComponentExecutionError(
            f"Component response exceeds {MAX_MESSAGE_BYTES} bytes",
            code="component/response-too-large",
        )
    return bytes(output)


def execute_component(
    contract: ComponentContract,
    artifact_root: str | Path,
    runtime_command: Sequence[str],
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute one pinned component artifact through the bounded JSON stdio protocol."""

    try:
        validate(arguments, contract.input_schema)
    except SchemaValidationError as exc:
        raise ComponentExecutionError(
            f"Invalid component input: {exc}", code="component/input"
        ) from exc
    artifact = verify_component_artifact(contract, artifact_root)
    runtime = tuple(runtime_command)
    if any(not isinstance(item, str) or not item for item in runtime):
        raise ComponentContractError("Component runtime command contains an empty item")
    command = (*runtime, str(artifact))
    request = (
        json.dumps(
            {
                "schema": COMPONENT_CALL_SCHEMA,
                "component": contract.component_id,
                "version": contract.version,
                "operation": contract.operation,
                "input": arguments,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    raw = _run_bounded_process(
        command,
        request,
        cwd=Path(artifact_root).resolve(),
        timeout=contract.cancellation.timeout_ms / 1000,
    )
    try:
        response = strict_json(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ComponentExecutionError(
            f"Component returned invalid JSON: {exc}", code="component/protocol"
        ) from exc
    if not isinstance(response, dict):
        raise ComponentExecutionError(
            "Component response must be an object", code="component/protocol"
        )
    if response.get("schema") != COMPONENT_RESULT_SCHEMA or not isinstance(
        response.get("ok"), bool
    ):
        raise ComponentExecutionError(
            "Component response has an invalid schema or status",
            code="component/protocol",
        )
    if response["ok"] is False:
        if set(response) != {"schema", "ok", "error"}:
            raise ComponentExecutionError(
                "Component error response contains unexpected fields",
                code="component/protocol",
            )
        error = response["error"]
        if not isinstance(error, dict) or set(error) != {"code", "message"}:
            raise ComponentExecutionError(
                "Component error response is malformed", code="component/protocol"
            )
        code = error["code"]
        message = error["message"]
        declared = {item.code: item for item in contract.errors}.get(code)
        if declared is None or not isinstance(message, str) or not message:
            raise ComponentExecutionError(
                "Component returned an undeclared or malformed error",
                code="component/protocol",
            )
        raise ComponentReportedError(
            message, code=declared.code, retryable=declared.retryable
        )
    if set(response) != {"schema", "ok", "output"}:
        raise ComponentExecutionError(
            "Component success response contains unexpected fields",
            code="component/protocol",
        )
    output = response["output"]
    try:
        validate(output, contract.output_schema)
    except SchemaValidationError as exc:
        raise ComponentExecutionError(
            f"Invalid component output: {exc}", code="component/output"
        ) from exc
    if not isinstance(output, dict):
        raise ComponentExecutionError(
            "Component output must be an object", code="component/output"
        )
    return output


def bind_component(
    program: Program,
    contract: ComponentContract,
    artifact_root: str | Path,
    runtime_command: Sequence[str] = (),
    *,
    allowed_effects: Iterable[str] = (),
    allowed_capabilities: Iterable[str] = (),
) -> ComponentToolBinding:
    """Validate a contract against checked Kosika declarations before any process starts."""

    tool = next(
        (item for item in program.tools if item.name == contract.tool_name), None
    )
    if tool is None:
        raise ComponentContractError(
            f"Component contract references undeclared tool {contract.tool_name!r}"
        )
    expected_source = f"component:{contract.component_id}"
    if tool.source != expected_source:
        raise ComponentContractError(
            f"Kosika tool source {tool.source!r} does not match {expected_source!r}"
        )
    if tool.operation != contract.operation:
        raise ComponentContractError(
            "Component operation does not match the Kosika tool"
        )
    if tool.access != contract.access:
        raise ComponentContractError("Component access does not match the Kosika tool")
    input_schema = schema_for_tool_input(program, tool.input)
    output_schema = schema_for_record(program, tool.output)
    if not schemas_equal(input_schema, contract.input_schema):
        raise ComponentContractError(
            "Component input schema does not match the Kosika tool input record"
        )
    if not schemas_equal(output_schema, contract.output_schema):
        raise ComponentContractError(
            "Component output schema does not match the Kosika tool output record"
        )
    denied_effects = sorted(set(contract.effects) - set(allowed_effects))
    if denied_effects:
        raise ComponentContractError(
            f"Component effects were not granted by the host: {denied_effects!r}"
        )
    denied_capabilities = sorted(set(contract.capabilities) - set(allowed_capabilities))
    if denied_capabilities:
        raise ComponentContractError(
            "Component capabilities were not granted by the host: "
            f"{denied_capabilities!r}"
        )
    verify_component_artifact(contract, artifact_root)
    return ComponentToolBinding(
        declaration=tool,
        contract=contract,
        artifact_root=Path(artifact_root),
        runtime_command=tuple(runtime_command),
    )
