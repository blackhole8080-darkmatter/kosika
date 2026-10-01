"""Tests for the standalone kosika-lsp process wrapper.

The JSON-RPC protocol itself is covered by ``test_lsp.py``. These tests cover
what this package adds on top: the installable entry point, the version flag,
and the stdout guard that keeps stray text writes from corrupting the protocol
stream.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.lsp import JsonRpcTransport
from kosika_lsp import SERVER_NAME, SERVER_VERSION, main, serve_stdio
from kosika_lsp.server import _reserved_stdout

REPO_ROOT = Path(__file__).resolve().parent.parent
HELLO_WORLD = REPO_ROOT / "examples" / "hello_world.kos"


def frame(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload


def read_messages(stream) -> list[dict[str, Any]]:
    """Decode a framed LSP stream using the server's own transport parser."""
    transport = JsonRpcTransport(stream, io.BytesIO())
    messages: list[dict[str, Any]] = []
    while True:
        message = transport.read()
        if message is None:
            return messages
        messages.append(message)


def full_session() -> bytes:
    return b"".join(
        [
            frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}}),
            frame({"jsonrpc": "2.0", "method": "exit", "params": {}}),
        ]
    )


class ReservedStdoutTests(unittest.TestCase):
    def test_text_writes_are_diverted_but_buffer_still_reaches_stdout(self) -> None:
        binary = io.BytesIO()
        wrapper = io.TextIOWrapper(binary)
        original = sys.stdout
        sys.stdout = wrapper
        try:
            with _reserved_stdout():
                print("stray output")
                # The transport writes framed bytes through the buffer.
                sys.stdout.buffer.write(b"protocol-bytes")
        finally:
            sys.stdout = original
            # Detach so the wrapper's finalizer does not close `binary`.
            wrapper.detach()

        # The protocol stream is untouched by the stray text write.
        self.assertEqual(binary.getvalue(), b"protocol-bytes")

    def test_original_stdout_is_restored_after_exit(self) -> None:
        original = sys.stdout
        with _reserved_stdout():
            self.assertIsNot(sys.stdout, original)
        self.assertIs(sys.stdout, original)

    def test_guarded_stdout_exposes_binary_buffer(self) -> None:
        with _reserved_stdout():
            self.assertIsNotNone(getattr(sys.stdout, "buffer", None))


class EntryPointTests(unittest.TestCase):
    def test_version_flag_prints_name_and_version(self) -> None:
        import io
        from contextlib import redirect_stdout

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--version"])
        self.assertEqual(code, 0)
        self.assertEqual(buffer.getvalue().strip(), f"{SERVER_NAME} {SERVER_VERSION}")

    def test_stdio_and_version_are_mutually_exclusive(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main(["--stdio", "--version"])
        self.assertNotEqual(raised.exception.code, 0)

    def test_module_exposes_serve_stdio(self) -> None:
        self.assertTrue(callable(serve_stdio))


class SubprocessSessionTests(unittest.TestCase):
    """Drive the real process, which is how editors actually launch the server."""

    def _run(self, payload: bytes) -> list[dict[str, Any]]:
        process = subprocess.run(
            [sys.executable, "-m", "kosika_lsp", "--stdio"],
            input=payload,
            capture_output=True,
            cwd=str(REPO_ROOT),
            timeout=60,
        )
        self.assertEqual(
            process.returncode, 0, process.stderr.decode("utf-8", "replace")
        )
        return read_messages(io.BytesIO(process.stdout))

    def test_initialize_advertises_expected_capabilities(self) -> None:
        messages = self._run(full_session())
        responses = [m for m in messages if m.get("id") == 1]
        self.assertTrue(responses, f"no initialize response in {messages!r}")
        capabilities = responses[0]["result"]["capabilities"]
        for capability in (
            "hoverProvider",
            "definitionProvider",
            "referencesProvider",
            "documentSymbolProvider",
            "completionProvider",
        ):
            self.assertTrue(capabilities.get(capability), f"missing {capability}")

    def test_open_document_publishes_diagnostics(self) -> None:
        text = HELLO_WORLD.read_text(encoding="utf-8")
        uri = HELLO_WORLD.as_uri()
        payload = b"".join(
            [
                frame(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
                ),
                frame(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didOpen",
                        "params": {
                            "textDocument": {
                                "uri": uri,
                                "languageId": "kosika",
                                "version": 1,
                                "text": text,
                            }
                        },
                    }
                ),
                frame(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "textDocument/documentSymbol",
                        "params": {"textDocument": {"uri": uri}},
                    }
                ),
                frame({"jsonrpc": "2.0", "id": 3, "method": "shutdown", "params": {}}),
                frame({"jsonrpc": "2.0", "method": "exit", "params": {}}),
            ]
        )
        messages = self._run(payload)
        published = [
            m for m in messages if m.get("method") == "textDocument/publishDiagnostics"
        ]
        self.assertTrue(published, f"no diagnostics published in {messages!r}")
        # The valid example must produce no error diagnostics.
        self.assertEqual(published[0]["params"]["diagnostics"], [])
        symbols = [m for m in messages if m.get("id") == 2][0]["result"]
        self.assertEqual(
            sorted(s["name"] for s in symbols), ["Greeting", "greeting", "main"]
        )

    def test_invalid_document_reports_a_diagnostic(self) -> None:
        payload = b"".join(
            [
                frame(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
                ),
                frame(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didOpen",
                        "params": {
                            "textDocument": {
                                "uri": "file:///workspace/broken.kos",
                                "languageId": "kosika",
                                "version": 1,
                                "text": "record Oops { value: Missing\n",
                            }
                        },
                    }
                ),
                frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}}),
                frame({"jsonrpc": "2.0", "method": "exit", "params": {}}),
            ]
        )
        messages = self._run(payload)
        published = [
            m for m in messages if m.get("method") == "textDocument/publishDiagnostics"
        ]
        self.assertTrue(published)
        self.assertTrue(published[0]["params"]["diagnostics"])

    def test_version_flag_via_subprocess(self) -> None:
        process = subprocess.run(
            [sys.executable, "-m", "kosika_lsp", "--version"],
            capture_output=True,
            cwd=str(REPO_ROOT),
            timeout=60,
        )
        self.assertEqual(process.returncode, 0)
        self.assertEqual(
            process.stdout.decode("utf-8").strip(), f"{SERVER_NAME} {SERVER_VERSION}"
        )

    def test_diagnostics_carry_stable_code_and_data(self) -> None:
        source = (
            "record Greeting {\n"
            "  text: Text\n"
            "  name: Text\n"
            "}\n"
            "\n"
            "fn greeting() -> Greeting {\n"
            '  return {text: "hi"}\n'
            "}\n"
        )
        payload = b"".join(
            [
                frame(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
                ),
                frame(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didOpen",
                        "params": {
                            "textDocument": {
                                "uri": "file:///workspace/broken.kos",
                                "languageId": "kosika",
                                "version": 1,
                                "text": source,
                            }
                        },
                    }
                ),
                frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}}),
                frame({"jsonrpc": "2.0", "method": "exit", "params": {}}),
            ]
        )
        messages = self._run(payload)
        published = [
            m for m in messages if m.get("method") == "textDocument/publishDiagnostics"
        ]
        self.assertTrue(published, f"no diagnostics published in {messages!r}")
        diagnostic = published[0]["params"]["diagnostics"][0]
        # `code` and `data` are standard LSP fields and are what let a client
        # choose a repair without matching on message prose.
        self.assertEqual(diagnostic["code"], "check/missing-field")
        self.assertEqual(diagnostic["data"]["missing_fields"], ["name"])
        self.assertEqual(diagnostic["source"], "kosika")
        # The span must point at `greeting`, not at the top of the file.
        self.assertEqual(diagnostic["range"]["start"]["line"], 5)

    def test_imported_diagnostic_is_published_for_imported_uri(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "library.kos"
            library.write_text(
                "export [Result, broken]\n"
                "record Result { text: Text name: Text }\n"
                'fn broken() -> Result { return {text: "x"} }\n',
                encoding="utf-8",
            )
            entry = root / "main.kos"
            source = (
                'import "library.kos"\n'
                "workflow main() -> Result {\n"
                "  step call { let result = broken() return result }\n"
                "}\n"
            )
            entry.write_text(source, encoding="utf-8")
            payload = b"".join(
                [
                    frame(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {"rootUri": root.as_uri()},
                        }
                    ),
                    frame(
                        {
                            "jsonrpc": "2.0",
                            "method": "textDocument/didOpen",
                            "params": {
                                "textDocument": {
                                    "uri": entry.as_uri(),
                                    "languageId": "kosika",
                                    "version": 1,
                                    "text": source,
                                }
                            },
                        }
                    ),
                    frame(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "shutdown",
                            "params": {},
                        }
                    ),
                    frame({"jsonrpc": "2.0", "method": "exit", "params": {}}),
                ]
            )
            messages = self._run(payload)

        publications: list[dict[str, object]] = []
        for message in messages:
            if message.get("method") != "textDocument/publishDiagnostics":
                continue
            params = message.get("params")
            if not isinstance(params, dict) or params.get("uri") != library.as_uri():
                continue
            diagnostics = params.get("diagnostics")
            if isinstance(diagnostics, list) and diagnostics:
                publications.append(params)
        self.assertTrue(publications, messages)
        diagnostics = publications[-1]["diagnostics"]
        assert isinstance(diagnostics, list)
        diagnostic = diagnostics[0]
        assert isinstance(diagnostic, dict)
        self.assertEqual(diagnostic["code"], "check/missing-field")
        diagnostic_range = diagnostic["range"]
        assert isinstance(diagnostic_range, dict)
        start = diagnostic_range["start"]
        assert isinstance(start, dict)
        self.assertEqual(start["line"], 2)


if __name__ == "__main__":
    unittest.main()
