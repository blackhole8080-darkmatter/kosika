"""A bounded stdlib-only Language Server Protocol bridge for Kosika."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from .editor_features import (
    MAX_SOURCE_BYTES,
    EditorFeatureError,
    build_editor_features,
)
from .lexer import lex

MAX_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_WORKSPACE_FILES = 256
MAX_WORKSPACE_BYTES = 16 * 1024 * 1024
WATCH_CREATED = 1
WATCH_CHANGED = 2
WATCH_DELETED = 3


class LspError(RuntimeError):
    """Raised when the LSP transport or request is invalid."""


@dataclass
class Document:
    uri: str
    text: str
    version: int | None = None
    is_open: bool = False
    features: dict[str, Any] | None = None


class WorkspaceIndex:
    """Bounded source index for open and workspace-local Kosika documents."""

    def __init__(self) -> None:
        self.root: Path | None = None
        self.documents: dict[str, Document] = {}

    def initialize(self, root_uri: str | None) -> None:
        self.documents.clear()
        self.root = _path_from_uri(root_uri) if root_uri else None
        if self.root is None:
            return
        if not self.root.is_dir():
            raise LspError(f"Workspace root is not a directory: {self.root}")
        total_bytes = 0
        for current, directories, filenames in os.walk(self.root):
            directories[:] = sorted(
                name
                for name in directories
                if not name.startswith(".")
                and name not in {"node_modules", "__pycache__"}
            )
            for filename in sorted(filenames):
                if not filename.endswith(".kos"):
                    continue
                if len(self.documents) >= MAX_WORKSPACE_FILES:
                    return
                document = self._read_disk(Path(current) / filename)
                if document is None:
                    continue
                size = len(document.text.encode("utf-8"))
                if total_bytes + size > MAX_WORKSPACE_BYTES:
                    return
                total_bytes += size
                self.documents[document.uri] = document

    def refresh(self, uri: str, change_type: int) -> list[str]:
        """Refresh one disk file and return open documents needing republish."""
        before = self._open_dependents(uri)
        if self.root is None:
            return []
        path = _path_from_uri(uri)
        if path.suffix != ".kos" or not self._inside_root(path):
            return []
        existing = self.documents.get(uri)
        if existing is not None and existing.is_open:
            existing.features = None
        elif change_type == WATCH_DELETED:
            self.documents.pop(uri, None)
        else:
            document = self._read_disk(path)
            if document is None:
                self.documents.pop(uri, None)
            elif (
                uri not in self.documents and len(self.documents) >= MAX_WORKSPACE_FILES
            ):
                return sorted(set(before))
            else:
                self._ensure_capacity(uri, document.text)
                self.documents[uri] = document
        affected = sorted(set(before) | set(self._open_dependents(uri)))
        for affected_uri in affected:
            self.documents[affected_uri].features = None
        return affected

    def _open_dependents(self, uri: str) -> list[str]:
        return [
            document.uri
            for document in self.documents.values()
            if document.is_open
            and (
                document.uri == uri
                or any(imported.uri == uri for imported in self._imports(document))
            )
        ]

    def invalidate(self, uri: str) -> list[str]:
        """Invalidate an open document and direct import dependants."""

        affected = sorted(set(self._open_dependents(uri)) | {uri})
        for affected_uri in affected:
            document = self.documents.get(affected_uri)
            if document is not None:
                document.features = None
        return affected

    def open(self, uri: str, text: str, version: int | None) -> Document:
        document = self.documents.get(uri)
        if document is None and len(self.documents) >= MAX_WORKSPACE_FILES:
            raise LspError("Kosika LSP workspace file limit reached")
        self._ensure_capacity(uri, text)
        if document is None:
            document = Document(uri=uri, text=text)
            self.documents[uri] = document
        document.text = text
        document.version = version
        document.is_open = True
        document.features = None
        return document

    def change(self, uri: str, text: str, version: int | None) -> Document:
        document = self.get(uri)
        if not document.is_open:
            raise LspError("Change received for unopened document")
        self._ensure_capacity(uri, text)
        document.text = text
        document.version = version
        document.features = None
        return document

    def close(self, uri: str) -> None:
        self.get(uri)
        if self.root is not None:
            disk_document = self._read_disk(_path_from_uri(uri))
            if disk_document is not None:
                self._ensure_capacity(uri, disk_document.text)
                self.documents[uri] = disk_document
                return
        self.documents.pop(uri, None)

    def get(self, uri: str) -> Document:
        document = self.documents.get(uri)
        if document is not None:
            return document
        if self.root is not None:
            document = self._read_disk(_path_from_uri(uri))
            if document is not None:
                if len(self.documents) >= MAX_WORKSPACE_FILES:
                    raise LspError("Kosika LSP workspace file limit reached")
                self._ensure_capacity(uri, document.text)
                self.documents[uri] = document
                return document
        raise LspError(f"Unknown document {uri!r}")

    def _ensure_capacity(self, uri: str, text: str) -> None:
        total = sum(
            len(document.text.encode("utf-8"))
            for key, document in self.documents.items()
            if key != uri
        )
        if total + len(text.encode("utf-8")) > MAX_WORKSPACE_BYTES:
            raise LspError("Kosika LSP workspace source limit reached")

    def features(self, document: Document) -> dict[str, Any]:
        if document.features is None:
            options: dict[str, Any] = {}
            if self.root is not None:
                source_path = _path_from_uri(document.uri)
                if self._inside_root(source_path) and source_path.exists():
                    overrides: dict[Path, str] = {}
                    for candidate in self.documents.values():
                        candidate_path = _path_from_uri(candidate.uri)
                        if candidate_path.exists() and self._inside_root(
                            candidate_path
                        ):
                            overrides[candidate_path] = candidate.text
                    options = {
                        "source_path": source_path,
                        "root": self.root,
                        "source_overrides": overrides,
                    }
            document.features = build_editor_features(
                document.text,
                path=document.uri,
                **options,
            )
        return document.features

    def definitions(
        self, document: Document, name: str
    ) -> list[tuple[Document, dict[str, Any]]]:
        local = _symbol_named(self.features(document)["symbols"], name)
        if local is not None:
            return [(document, local)]
        candidates: list[tuple[Document, dict[str, Any]]] = []
        for imported in self._imports(document):
            if name not in self.features(imported)["exports"]:
                continue
            symbol = _symbol_named(self.features(imported)["symbols"], name)
            if symbol is not None:
                candidates.append((imported, symbol))
        return candidates if len(candidates) == 1 else []

    def references(
        self,
        document: Document,
        name: str,
        include_declaration: bool,
    ) -> list[dict[str, Any]]:
        definitions = self.definitions(document, name)
        if len(definitions) != 1:
            return []
        owner, symbol = definitions[0]
        result: list[dict[str, Any]] = []
        for candidate in self.documents.values():
            if not self._can_reference(candidate, owner, name):
                continue
            try:
                tokens = lex(candidate.text)
            except ValueError:
                continue
            declarations = {
                _range_key(item["selectionRange"])
                for item in self.features(candidate)["symbols"]
                if item["name"] == name
            }
            for token in tokens:
                if token.kind != "IDENT" or token.value != name:
                    continue
                token_range = _token_range(token)
                is_declaration = _range_key(token_range) in declarations
                if is_declaration and not include_declaration:
                    continue
                result.append({"uri": candidate.uri, "range": token_range})
        if include_declaration and not any(
            item["uri"] == owner.uri and item["range"] == symbol["selectionRange"]
            for item in result
        ):
            result.append({"uri": owner.uri, "range": symbol["selectionRange"]})
        return result

    def _can_reference(self, candidate: Document, owner: Document, name: str) -> bool:
        if candidate.uri == owner.uri:
            return True
        if _symbol_named(self.features(candidate)["symbols"], name) is not None:
            return False
        return any(
            imported.uri == owner.uri and name in self.features(owner)["exports"]
            for imported in self._imports(candidate)
        )

    def _imports(self, document: Document) -> list[Document]:
        if self.root is None:
            return []
        documents: list[Document] = []
        base = _path_from_uri(document.uri).parent
        for import_name in self.features(document)["imports"]:
            relative = Path(import_name)
            if relative.is_absolute() or relative.drive or relative.suffix != ".kos":
                continue
            candidate = (base / relative).resolve()
            if not self._inside_root(candidate):
                continue
            try:
                imported = self.get(_uri_for_path(candidate))
            except LspError:
                continue
            documents.append(imported)
        return documents

    def _read_disk(self, path: Path) -> Document | None:
        if self.root is None or path.suffix != ".kos" or path.is_symlink():
            return None
        try:
            resolved = path.resolve(strict=True)
            if not self._inside_root(resolved):
                return None
            data = resolved.read_bytes()
        except (OSError, ValueError):
            return None
        if len(data) > MAX_SOURCE_BYTES:
            return None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
        return Document(uri=_uri_for_path(resolved), text=text)

    def _inside_root(self, path: Path) -> bool:
        if self.root is None:
            return False
        try:
            path.relative_to(self.root.resolve())
        except ValueError:
            return False
        return True


class JsonRpcTransport:
    """Read and write Content-Length framed JSON-RPC messages."""

    def __init__(self, reader: BinaryIO, writer: BinaryIO) -> None:
        self.reader = reader
        self.writer = writer

    def read(self) -> dict[str, Any] | None:
        headers: dict[str, str] = {}
        while True:
            line = self.reader.readline()
            if not line:
                return None
            if line in {b"\r\n", b"\n"}:
                break
            try:
                key, value = line.decode("ascii").split(":", 1)
            except (UnicodeDecodeError, ValueError) as exc:
                raise LspError("Malformed LSP header") from exc
            headers[key.lower().strip()] = value.strip()
        try:
            length = int(headers["content-length"])
        except (KeyError, ValueError) as exc:
            raise LspError("LSP message lacks a valid Content-Length") from exc
        if length < 0 or length > MAX_MESSAGE_BYTES:
            raise LspError("LSP message exceeds the bounded message limit")
        payload = self.reader.read(length)
        if len(payload) != length:
            raise LspError("LSP message ended before Content-Length")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LspError("LSP message is not valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise LspError("LSP message must be a JSON object")
        return value

    def write(self, message: dict[str, Any]) -> None:
        try:
            payload = json.dumps(
                message, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise LspError("LSP response is not JSON-serializable") from exc
        if len(payload) > MAX_MESSAGE_BYTES:
            raise LspError("LSP response exceeds the bounded message limit")
        self.writer.write(
            f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
        )
        self.writer.flush()


class KosikaLanguageServer:
    """Serve Kosika editor features over one long-lived JSON-RPC connection."""

    def __init__(self, transport: JsonRpcTransport) -> None:
        self.transport = transport
        self.workspace = WorkspaceIndex()
        self.cancelled_requests: set[str | int] = set()
        self._entry_diagnostics: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self.shutdown_requested = False
        self.exit_code = 0

    def serve(self) -> int:
        while True:
            message = self.transport.read()
            if message is None:
                return self.exit_code
            try:
                self._dispatch(message)
            except _ExitServer:
                return self.exit_code
            except LspError as exc:
                request_id = message.get("id")
                if request_id is not None:
                    self._error(request_id, -32600, str(exc))
            except Exception as exc:  # pragma: no cover - final process guard
                request_id = message.get("id")
                if request_id is not None:
                    self._error(request_id, -32603, f"Kosika LSP failed safely: {exc}")

    def _dispatch(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        if not isinstance(method, str):
            raise LspError("LSP message requires a method")
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise LspError("LSP parameters must be an object")
        request_id = message.get("id")
        if isinstance(request_id, (str, int)) and request_id in self.cancelled_requests:
            self.cancelled_requests.remove(request_id)
            return
        result = self._handle(method, params)
        if isinstance(request_id, (str, int)) and request_id in self.cancelled_requests:
            self.cancelled_requests.remove(request_id)
            return
        if request_id is not None:
            self._response(request_id, result)

    def _handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            self.workspace.initialize(params.get("rootUri"))
            return {
                "capabilities": {
                    "textDocumentSync": {
                        "openClose": True,
                        "change": 1,
                        "save": {},
                    },
                    "documentSymbolProvider": True,
                    "hoverProvider": True,
                    "definitionProvider": True,
                    "referencesProvider": True,
                    "completionProvider": {
                        "resolveProvider": False,
                        "triggerCharacters": [" ", ":"],
                    },
                },
                "serverInfo": {"name": "kosika-lsp", "version": "0.1"},
            }
        if method == "initialized":
            return None
        if method == "shutdown":
            self.shutdown_requested = True
            return None
        if method == "exit":
            self.exit_code = 0 if self.shutdown_requested else 1
            raise _ExitServer
        if method == "textDocument/didOpen":
            self._open(params)
            return None
        if method == "textDocument/didChange":
            self._change(params)
            return None
        if method == "textDocument/didSave":
            self._publish(params["textDocument"]["uri"])
            return None
        if method == "textDocument/didClose":
            uri = params["textDocument"]["uri"]
            self.workspace.close(uri)
            self._publish_empty(uri)
            return None
        if method == "workspace/didChangeWatchedFiles":
            changes = params.get("changes", [])
            if not isinstance(changes, list):
                raise LspError("Watched file changes must be a list")
            for change in changes:
                if not isinstance(change, dict):
                    continue
                uri = change.get("uri")
                change_type = change.get("type")
                if not isinstance(uri, str) or not isinstance(change_type, int):
                    continue
                for affected_uri in self.workspace.refresh(uri, change_type):
                    self._publish(affected_uri)
            return None
        if method == "textDocument/documentSymbol":
            return self._features(params)["symbols"]
        if method == "textDocument/hover":
            return self._hover(params)
        if method == "textDocument/definition":
            return self._definition(params)
        if method == "textDocument/references":
            return self._references(params)
        if method == "textDocument/completion":
            return {
                "isIncomplete": False,
                "items": self._features(params)["completions"],
            }
        if method == "$/cancelRequest":
            request_id = params.get("id")
            if isinstance(request_id, (str, int)):
                if len(self.cancelled_requests) >= 256:
                    self.cancelled_requests.pop()
                self.cancelled_requests.add(request_id)
            return None
        raise LspError(f"Unsupported LSP method {method!r}")

    @staticmethod
    def _bounded_text(value: Any) -> str:
        if not isinstance(value, str):
            raise LspError("Kosika LSP document text must be a string")
        if len(value.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise LspError("Kosika LSP document exceeds the source-size limit")
        return value

    def _open(self, params: dict[str, Any]) -> None:
        text_document = params["textDocument"]
        uri = text_document["uri"]
        self.workspace.open(
            uri,
            self._bounded_text(text_document.get("text")),
            text_document.get("version"),
        )
        for affected_uri in self.workspace.invalidate(uri):
            self._publish(affected_uri)

    def _change(self, params: dict[str, Any]) -> None:
        text_document = params["textDocument"]
        changes = params.get("contentChanges", [])
        if not isinstance(changes, list) or not changes:
            raise LspError("Document change requires contentChanges")
        change = changes[-1]
        if not isinstance(change, dict):
            raise LspError("Kosika LSP supports full-text document changes only")
        uri = text_document["uri"]
        self.workspace.change(
            uri,
            self._bounded_text(change.get("text")),
            text_document.get("version"),
        )
        for affected_uri in self.workspace.invalidate(uri):
            self._publish(affected_uri)

    def _document(self, params: dict[str, Any]) -> Document:
        return self.workspace.get(params["textDocument"]["uri"])

    def _features(self, params: dict[str, Any]) -> dict[str, Any]:
        document = self._document(params)
        try:
            return self.workspace.features(document)
        except (EditorFeatureError, UnicodeError) as exc:
            raise LspError(str(exc)) from exc

    def _publish(self, uri: str) -> None:
        try:
            document = self.workspace.get(uri)
            features = self.workspace.features(document)
            grouped: dict[str, list[dict[str, Any]]] = {uri: []}
            for item in features["diagnostics"]:
                target = self._diagnostic_uri(item.get("path"), uri)
                payload = {
                    "range": item.get("range")
                    or {
                        "start": {"line": 0, "character": 0},
                        "end": {"line": 0, "character": 0},
                    },
                    "severity": 2 if item.get("severity") == "warning" else 1,
                    "source": "kosika",
                    "message": item["message"],
                }
                if item.get("code"):
                    payload["code"] = item["code"]
                if item.get("data"):
                    payload["data"] = item["data"]
                grouped.setdefault(target, []).append(payload)
        except (EditorFeatureError, UnicodeError, LspError) as exc:
            grouped = {
                uri: [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 0},
                        },
                        "severity": 1,
                        "source": "kosika",
                        "message": str(exc),
                    }
                ]
            }
        previous = set(self._entry_diagnostics.get(uri, {}))
        self._entry_diagnostics[uri] = grouped
        self._republish(previous | set(grouped))

    @staticmethod
    def _diagnostic_uri(path: object, fallback: str) -> str:
        if not isinstance(path, str) or path == "<memory>":
            return fallback
        if path.startswith("file:"):
            return path
        candidate = Path(path)
        return _uri_for_path(candidate) if candidate.is_absolute() else fallback

    def _republish(self, targets: set[str]) -> None:
        for target in sorted(targets):
            diagnostics: list[dict[str, Any]] = []
            seen: set[str] = set()
            for grouped in self._entry_diagnostics.values():
                for item in grouped.get(target, []):
                    key = json.dumps(item, sort_keys=True)
                    if key not in seen:
                        seen.add(key)
                        diagnostics.append(item)
            self.transport.write(
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/publishDiagnostics",
                    "params": {"uri": target, "diagnostics": diagnostics},
                }
            )

    def _publish_empty(self, uri: str) -> None:
        previous = set(self._entry_diagnostics.pop(uri, {}))
        self._republish(previous | {uri})

    def _hover(self, params: dict[str, Any]) -> dict[str, Any] | None:
        features = self._features(params)
        position = params["position"]
        for item in features["hovers"]:
            if _contains(item["range"], position):
                return {
                    "contents": {"kind": "markdown", "value": item["contents"]},
                    "range": item["range"],
                }
        return None

    def _definition(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        document = self._document(params)
        word = _word_at(document.text, params["position"])
        if not word:
            return []
        return [
            {"uri": owner.uri, "range": symbol["selectionRange"]}
            for owner, symbol in self.workspace.definitions(document, word)
        ]

    def _references(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        document = self._document(params)
        word = _word_at(document.text, params["position"])
        if not word:
            return []
        context = params.get("context", {})
        include_declaration = bool(context.get("includeDeclaration", False))
        return self.workspace.references(document, word, include_declaration)

    def _response(self, request_id: Any, result: Any) -> None:
        self.transport.write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _error(self, request_id: Any, code: int, message: str) -> None:
        self.transport.write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )


class _ExitServer(Exception):
    """Internal control flow for the LSP exit notification."""


def _path_from_uri(uri: str) -> Path:
    if not isinstance(uri, str):
        raise LspError("LSP URI must be a string")
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise LspError("Kosika workspace URI must use the file scheme")
    path = unquote(parsed.path)
    if parsed.netloc and parsed.netloc != "localhost":
        path = f"//{parsed.netloc}{path}"
    path = url2pathname(path)
    if os.name == "nt" and path.startswith("\\") and len(path) > 2 and path[2] == ":":
        path = path[1:]
    return Path(path).resolve()


def _uri_for_path(path: Path) -> str:
    return path.resolve().as_uri()


def _symbol_named(symbols: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((symbol for symbol in symbols if symbol["name"] == name), None)


def _range_key(value: dict[str, Any]) -> tuple[int, int, int, int]:
    start = value["start"]
    end = value["end"]
    return (start["line"], start["character"], end["line"], end["character"])


def _token_range(token: Any) -> dict[str, dict[str, int]]:
    return {
        "start": {"line": token.line - 1, "character": token.column - 1},
        "end": {
            "line": token.line - 1,
            "character": token.column - 1 + len(token.value),
        },
    }


def _contains(value: dict[str, Any], position: dict[str, int]) -> bool:
    start = value["start"]
    end = value["end"]
    line = position["line"]
    character = position["character"]
    if line < start["line"] or line > end["line"]:
        return False
    if line == start["line"] and character < start["character"]:
        return False
    return not (line == end["line"] and character > end["character"])


def _word_at(source: str, position: dict[str, int]) -> str:
    lines = source.splitlines()
    line = position.get("line", -1)
    character = position.get("character", -1)
    if line < 0 or line >= len(lines) or character < 0:
        return ""
    value = lines[line]
    index = min(character, len(value))
    start = index
    while start > 0 and (value[start - 1].isalnum() or value[start - 1] == "_"):
        start -= 1
    end = index
    while end < len(value) and (value[end].isalnum() or value[end] == "_"):
        end += 1
    return value[start:end]


def serve_stdio() -> int:
    transport = JsonRpcTransport(sys.stdin.buffer, sys.stdout.buffer)
    try:
        return KosikaLanguageServer(transport).serve()
    except _ExitServer:
        return 0


def main() -> int:
    return serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
