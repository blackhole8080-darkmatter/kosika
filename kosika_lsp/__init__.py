"""Kosika Language Server Protocol server.

A thin, installable wrapper around :mod:`kosika.lsp`. The protocol, workspace
index, and editor features live in the main ``kosika`` package; this package
exists so an editor or AI coding assistant can launch ``kosika-lsp`` without
depending on the runtime's CLI surface.
"""

from __future__ import annotations

from .server import (
    SERVER_NAME,
    SERVER_VERSION,
    JsonRpcTransport,
    KosikaLanguageServer,
    main,
    serve_stdio,
)

__all__ = [
    "JsonRpcTransport",
    "KosikaLanguageServer",
    "SERVER_NAME",
    "SERVER_VERSION",
    "main",
    "serve_stdio",
]
__version__ = SERVER_VERSION
