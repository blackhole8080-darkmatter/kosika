"""Allow ``python -m kosika_lsp`` to start the language server."""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
