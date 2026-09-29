from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kosika.lsp import (
    WATCH_CHANGED,
    WATCH_CREATED,
    WATCH_DELETED,
    JsonRpcTransport,
    KosikaLanguageServer,
)


def frame(message: dict[str, object]) -> bytes:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload


def messages(raw: bytes) -> list[dict[str, object]]:
    reader = io.BytesIO(raw)
    transport = JsonRpcTransport(reader, io.BytesIO())
    result = []
    while True:
        message = transport.read()
        if message is None:
            return result
        result.append(message)


class LspProtocolTests(unittest.TestCase):
    def test_stdio_server_handles_lifecycle_and_features(self) -> None:
        uri = "file:///workspace/main.kos"
        source = "record Result { ok: Bool }\nagent reviewer { can: [] cannot: [] }\n"
        input_stream = b"".join(
            [
                frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                frame({"jsonrpc": "2.0", "method": "initialized", "params": {}}),
                frame(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didOpen",
                        "params": {
                            "textDocument": {
                                "uri": uri,
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
                        "method": "textDocument/documentSymbol",
                        "params": {"textDocument": {"uri": uri}},
                    }
                ),
                frame(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "textDocument/hover",
                        "params": {
                            "textDocument": {"uri": uri},
                            "position": {"line": 0, "character": 9},
                        },
                    }
                ),
                frame(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "textDocument/completion",
                        "params": {"textDocument": {"uri": uri}},
                    }
                ),
                frame({"jsonrpc": "2.0", "id": 5, "method": "shutdown", "params": None}),
                frame({"jsonrpc": "2.0", "method": "exit", "params": None}),
            ]
        )
        output = io.BytesIO()
        server = KosikaLanguageServer(JsonRpcTransport(io.BytesIO(input_stream), output))
        self.assertEqual(server.serve(), 0)
        result = messages(output.getvalue())
        responses = {message.get("id"): message for message in result if "id" in message}
        self.assertTrue(responses[1]["result"]["capabilities"]["referencesProvider"])
        self.assertIn(2, responses)
        self.assertEqual([item["name"] for item in responses[2]["result"]], ["Result", "reviewer"])
        self.assertIn("contents", responses[3]["result"])
        self.assertTrue(responses[4]["result"]["items"])
        self.assertTrue(
            any(
                message.get("method") == "textDocument/publishDiagnostics"
                for message in result
            )
        )



    def test_cancelled_request_is_not_replied(self) -> None:
        output = io.BytesIO()
        server = KosikaLanguageServer(JsonRpcTransport(io.BytesIO(), output))
        server._handle("$/cancelRequest", {"id": 9})
        server._dispatch(
            {"jsonrpc": "2.0", "id": 9, "method": "initialize", "params": {}}
        )
        self.assertEqual(output.getvalue(), b"")

    def test_module_serves_real_stdio_process(self) -> None:
        uri = "file:///workspace/process.kos"
        input_stream = b"".join(
            [
                frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                frame(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didOpen",
                        "params": {
                            "textDocument": {
                                "uri": uri,
                                "languageId": "kosika",
                                "version": 1,
                                "text": "record Process { ok: Bool }\n",
                            }
                        },
                    }
                ),
                frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": None}),
                frame({"jsonrpc": "2.0", "method": "exit", "params": None}),
            ]
        )
        process = subprocess.run(
            [sys.executable, "-m", "kosika.lsp"],
            cwd=Path(__file__).resolve().parents[1],
            input=input_stream,
            capture_output=True,
            timeout=10,
            check=True,
        )
        result = messages(process.stdout)
        responses = {message.get("id"): message for message in result if "id" in message}
        self.assertEqual(responses[1]["result"]["capabilities"]["hoverProvider"], True)
        self.assertEqual(responses[2]["result"], None)
        self.assertTrue(
            any(message.get("method") == "textDocument/publishDiagnostics" for message in result)
        )


    def test_workspace_definitions_and_references_cross_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "library.kos"
            main = root / "main.kos"
            library.write_text(
                "export [Shared]\nrecord Shared { ok: Bool }\n",
                encoding="utf-8",
            )
            source = (
                'import "library.kos"\n'
                "record Use { value: Shared }\n"
            )
            main.write_text(source, encoding="utf-8")
            main_uri = main.as_uri()
            library_uri = library.as_uri()
            shared_character = source.splitlines()[1].index("Shared") + 1
            input_stream = b"".join(
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
                                    "uri": main_uri,
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
                            "method": "textDocument/definition",
                            "params": {
                                "textDocument": {"uri": main_uri},
                                "position": {"line": 1, "character": shared_character},
                            },
                        }
                    ),
                    frame(
                        {
                            "jsonrpc": "2.0",
                            "id": 3,
                            "method": "textDocument/references",
                            "params": {
                                "textDocument": {"uri": main_uri},
                                "position": {"line": 1, "character": shared_character},
                                "context": {"includeDeclaration": False},
                            },
                        }
                    ),
                    frame(
                        {
                            "jsonrpc": "2.0",
                            "id": 4,
                            "method": "textDocument/references",
                            "params": {
                                "textDocument": {"uri": main_uri},
                                "position": {"line": 1, "character": shared_character},
                                "context": {"includeDeclaration": True},
                            },
                        }
                    ),
                    frame({"jsonrpc": "2.0", "id": 5, "method": "shutdown", "params": None}),
                    frame({"jsonrpc": "2.0", "method": "exit", "params": None}),
                ]
            )
            output = io.BytesIO()
            server = KosikaLanguageServer(
                JsonRpcTransport(io.BytesIO(input_stream), output)
            )
            self.assertEqual(server.serve(), 0)
            responses = {
                message.get("id"): message
                for message in messages(output.getvalue())
                if "id" in message
            }
            self.assertEqual(responses[2]["result"][0]["uri"], library_uri)
            without_declaration = responses[3]["result"]
            with_declaration = responses[4]["result"]
            self.assertTrue(any(item["uri"] == main_uri for item in without_declaration))
            self.assertTrue(any(item["uri"] == library_uri for item in with_declaration))
            self.assertGreater(len(with_declaration), len(without_declaration))


    def test_watched_files_refresh_create_change_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "library.kos"
            main = root / "main.kos"
            library.write_text(
                "export [Shared]\nrecord Shared { ok: Bool }\n",
                encoding="utf-8",
            )
            source = 'import "library.kos"\nrecord Use { value: Shared }\n'
            main.write_text(source, encoding="utf-8")
            library_uri = library.as_uri()
            main_uri = main.as_uri()
            output = io.BytesIO()
            server = KosikaLanguageServer(JsonRpcTransport(io.BytesIO(), output))

            server._handle("initialize", {"rootUri": root.as_uri()})
            server._handle(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": main_uri,
                        "languageId": "kosika",
                        "version": 1,
                        "text": source,
                    }
                },
            )
            library.write_text(
                "export [Changed]\nrecord Changed { ok: Bool }\n",
                encoding="utf-8",
            )
            server._handle(
                "workspace/didChangeWatchedFiles",
                {"changes": [{"uri": library_uri, "type": WATCH_CHANGED}]},
            )
            self.assertEqual(
                server.workspace.features(server.workspace.get(library_uri))["exports"],
                ["Changed"],
            )

            created = root / "created.kos"
            created.write_text("record Created { ok: Bool }\n", encoding="utf-8")
            created_uri = created.as_uri()
            server._handle(
                "workspace/didChangeWatchedFiles",
                {"changes": [{"uri": created_uri, "type": WATCH_CREATED}]},
            )
            self.assertIn(created_uri, server.workspace.documents)

            library.unlink()
            server._handle(
                "workspace/didChangeWatchedFiles",
                {"changes": [{"uri": library_uri, "type": WATCH_DELETED}]},
            )
            self.assertNotIn(library_uri, server.workspace.documents)
            published = [
                message
                for message in messages(output.getvalue())
                if message.get("method") == "textDocument/publishDiagnostics"
            ]
            self.assertGreaterEqual(len(published), 3)

    def test_invalid_source_publishes_diagnostic(self) -> None:
        uri = "file:///workspace/broken.kos"
        input_stream = b"".join(
            [
                frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                frame(
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didOpen",
                        "params": {
                            "textDocument": {
                                "uri": uri,
                                "languageId": "kosika",
                                "version": 1,
                                "text": "record Broken {\n",
                            }
                        },
                    }
                ),
                frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": None}),
                frame({"jsonrpc": "2.0", "method": "exit", "params": None}),
            ]
        )
        output = io.BytesIO()
        server = KosikaLanguageServer(JsonRpcTransport(io.BytesIO(input_stream), output))
        server.serve()
        diagnostics = [
            message
            for message in messages(output.getvalue())
            if message.get("method") == "textDocument/publishDiagnostics"
        ]
        self.assertTrue(diagnostics[0]["params"]["diagnostics"])


if __name__ == "__main__":
    unittest.main()
