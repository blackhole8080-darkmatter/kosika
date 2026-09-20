"""A bounded snapshot of explicitly granted local text documents."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class LocalDocuments:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Document root must be a directory")
        self.documents: dict[str, str] = {}
        # Deliberately one directory, text files only, with fixed resource bounds.
        for path in sorted(self.root.iterdir()):
            if path.suffix.lower() not in {".md", ".txt"}:
                continue
            if path.is_symlink() or path.resolve().parent != self.root:
                raise PermissionError(
                    "Document links outside the granted root are not allowed"
                )
            if not path.is_file():
                continue
            if len(self.documents) >= 32:
                raise ValueError("Document snapshot is limited to 32 files")
            with path.open("rb") as stream:
                data = stream.read(65537)
            if len(data) > 65536:
                raise ValueError(f"Document {path.name!r} exceeds the 64 KiB limit")
            self.documents[path.name] = data.decode("utf-8")

    def search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")
        if (
            set(arguments) != {"query"}
            or not isinstance(query, str)
            or not 1 <= len(query) <= 256
        ):
            raise ValueError("Search requires a query of 1 to 256 characters")
        terms = set(re.findall(r"\w+", query.casefold()))
        if not terms:
            raise ValueError("Search query must contain words")
        matches = []
        for source, text in self.documents.items():
            for line in text.splitlines():
                if terms & set(re.findall(r"\w+", line.casefold())):
                    matches.append({"source": source, "excerpt": line[:1000]})
                    if len(matches) == 5:
                        return {"hits": matches}
        return {"hits": matches}
