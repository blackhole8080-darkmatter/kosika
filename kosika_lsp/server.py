"""Standalone process entry point for the Kosika language server.

The server protocol itself lives in :mod:`kosika.lsp`. This module is the
installable process wrapper: it guards the JSON-RPC output stream and exposes a
console script.

Why the stdout guard matters: LSP over stdio multiplexes every response and
notification into ``sys.stdout``. A single ``print`` anywhere in the process --
including from a dependency, a warning, or a debugger -- lands inside the
message stream and desynchronizes the client. Server logs belong on stderr,
so anything written to the real stdout is redirected there instead of being
allowed to corrupt the protocol.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from typing import Iterator

from kosika.lsp import JsonRpcTransport, KosikaLanguageServer, serve_stdio

__all__ = [
    "KosikaLanguageServer",
    "JsonRpcTransport",
    "main",
    "serve_stdio",
]

SERVER_NAME = "kosika-lsp"
SERVER_VERSION = "0.1.0"


class _StderrLog(io.TextIOBase):
    """A text-layer stdout replacement that forwards writes to stderr.

    ``buffer`` deliberately still points at the real stdout binary stream: the
    JSON-RPC transport writes framed bytes through ``sys.stdout.buffer``, and
    that is the one legitimate writer. Only text writes -- the ones a stray
    ``print`` makes -- are diverted to stderr.
    """

    def __init__(self, buffer: io.BufferedIOBase) -> None:
        super().__init__()
        self.buffer = buffer

    def write(self, text: str) -> int:
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except (OSError, ValueError):
            pass
        return len(text)

    def writable(self) -> bool:
        return True

    def flush(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            sys.stderr.flush()


@contextlib.contextmanager
def _reserved_stdout() -> Iterator[None]:
    """Replace ``sys.stdout`` so stray text writes cannot corrupt the protocol."""
    binary = getattr(sys.stdout, "buffer", None)
    original = sys.stdout
    if binary is not None:
        sys.stdout = _StderrLog(binary)
    try:
        yield
    finally:
        with contextlib.suppress(OSError, ValueError):
            sys.stdout = original
        if binary is not None:
            with contextlib.suppress(OSError, ValueError):
                binary.flush()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=SERVER_NAME,
        description="Kosika Language Server Protocol server.",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="serve over stdin/stdout (the default and only supported transport)",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the server version and exit",
    )
    args = parser.parse_args(argv)
    if args.stdio and args.version:
        parser.error("--stdio and --version are mutually exclusive")
    return args


def main(argv: list[str] | None = None) -> int:
    """Run the language server process and return its exit code."""

    args = _parse_args(argv)
    if args.version:
        # Deliberately the only path that writes to stdout outside the protocol.
        print(f"{SERVER_NAME} {SERVER_VERSION}")
        return 0

    with _reserved_stdout():
        return serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
