"""Small host-granted adapters. No source declaration grants access on its own."""

from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .ast import ToolDecl
from .controls import TransientFailure
from .model_steps import strict_json


class ReadOnlyRetry:
    def __init__(
        self, execute: Callable[[ToolDecl, dict[str, Any]], Any], *, attempts: int = 2
    ):
        if type(attempts) is not int or not 1 <= attempts <= 3:
            raise ValueError("Retry attempts must be 1..3")
        self.execute, self.attempts = execute, attempts

    def __call__(self, tool: ToolDecl, arguments: dict[str, Any]) -> Any:
        if tool.access != "observe":
            raise PermissionError("Retry adapter permits observe tools only")
        for attempt in range(self.attempts):
            try:
                return self.execute(tool, arguments)
            except TransientFailure:
                if attempt + 1 == self.attempts:
                    raise
        raise AssertionError("Unreachable retry state")


class JsonFiles:
    """Read only an exact host-provided filename map, bounded to 1 MiB each."""

    def __init__(self, files: Mapping[str, str | Path]):
        self.files = {
            key: Path(value).resolve(strict=True) for key, value in files.items()
        }

    def read(self, key: str) -> Any:
        if key not in self.files:
            raise PermissionError("File key is not granted")
        with self.files[key].open("rb") as stream:
            data = stream.read(1048577)
        if len(data) > 1048576:
            raise ValueError("JSON file exceeds 1 MiB")
        return strict_json(data.decode("utf-8"))


class SQLiteReader:
    """Named host-defined queries, bound parameters, read-only database connection."""

    def __init__(
        self, path: str | Path, queries: Mapping[str, str], *, max_rows: int = 100
    ):
        if type(max_rows) is not int or not 1 <= max_rows <= 1000:
            raise ValueError("max_rows must be 1..1000")
        self.path = Path(path).resolve(strict=True)
        self.queries, self.max_rows = dict(queries), max_rows

    def query(self, name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if name not in self.queries:
            raise PermissionError("Query name is not granted")
        connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        try:
            connection.row_factory = sqlite3.Row
            connection.set_authorizer(
                lambda action, a, b, db, trigger: (
                    sqlite3.SQLITE_OK
                    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ)
                    else sqlite3.SQLITE_DENY
                )
            )
            # Bound execution work as well as returned rows.
            ticks = 0

            def progress() -> int:
                nonlocal ticks
                ticks += 1
                return int(ticks > 1000)

            connection.set_progress_handler(progress, 1000)
            cursor = connection.execute(self.queries[name], parameters)
            rows = cursor.fetchmany(self.max_rows + 1)
            if len(rows) > self.max_rows:
                raise ValueError("Query exceeds row limit")
            result = {"rows": [dict(row) for row in rows]}
            json.dumps(result, allow_nan=False)
            return result
        finally:
            connection.close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: Any, msg: Any, headers: Any, newurl: Any
    ) -> None:
        raise PermissionError("HTTP redirects are not granted")


class HTTPReader:
    """GET only, exact host-granted URLs, no redirects or inherited proxy config."""

    def __init__(self, urls: Mapping[str, str], *, timeout: float = 10):
        import math

        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("HTTP timeout must be finite and positive")
        self.urls = dict(urls)
        self.timeout = timeout
        for url in self.urls.values():
            parsed = urlsplit(url)
            if (
                parsed.username
                or parsed.password
                or parsed.fragment
                or not parsed.hostname
            ):
                raise ValueError(
                    "HTTP grants require a plain URL without embedded credentials"
                )
            if parsed.scheme != "https" and not (
                parsed.scheme == "http"
                and parsed.hostname in ("127.0.0.1", "::1", "localhost")
            ):
                raise ValueError("Use HTTPS or explicit loopback HTTP")

    def get(self, name: str) -> Any:
        if name not in self.urls:
            raise PermissionError("URL name is not granted")
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )
        with opener.open(self.urls[name], timeout=self.timeout) as response:
            data = response.read(1048577)
        if len(data) > 1048576:
            raise ValueError("HTTP response exceeds 1 MiB")
        return strict_json(data.decode("utf-8"))
