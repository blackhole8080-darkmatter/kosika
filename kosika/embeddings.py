"""Turning text into numbers that are close when the meanings are close.

A files tool with the operation `find` ranks the lines of a folder by how
near their meaning is to a question, so "what do I pay my landlord" can find
"Rent is 900 euros a month" although they share no word. That needs an
embedding model, and like every model it is granted by the person running
the program, never chosen by the program:

    kosika studio FOLDER --allow-folder NOTES --embed-model nomic-embed-text

An embedder is any function from texts to one vector each. The one here
asks a local Ollama. Vectors are kept in a small database outside the
granted folder, so a folder is never written to by being searched and a
line is only ever embedded once.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import urllib.error
import urllib.request
from array import array
from collections.abc import Callable, Sequence
from pathlib import Path

Embedder = Callable[[Sequence[str]], list[list[float]]]

OLLAMA_URL = "http://127.0.0.1:11434"
#: How many texts are sent to the model in one request.
BATCH = 32
#: Longer texts are cut before they are embedded.
MAX_TEXT = 2000


class EmbeddingError(RuntimeError):
    """The embedding model could not be reached or gave no usable answer."""


def _prefixes(model: str) -> tuple[str, str]:
    # nomic-embed-text was trained with these two labels and ranks much
    # better when a question and the lines it is compared with carry them.
    if model.split(":")[0].startswith("nomic-embed"):
        return "search_query: ", "search_document: "
    return "", ""


def ollama_embedder(
    model: str, url: str = OLLAMA_URL, timeout: float = 120.0
) -> Embedder:
    """An embedder that asks a local Ollama for `model`."""

    def embed(texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH):
            batch = [text[:MAX_TEXT] for text in texts[start : start + BATCH]]
            request = urllib.request.Request(
                url.rstrip("/") + "/api/embed",
                data=json.dumps({"model": model, "input": batch}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    reply = json.loads(response.read().decode("utf-8"))
                found = reply["embeddings"]
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
                raise EmbeddingError(
                    f"embedding model {model!r} gave no answer: {exc}"
                ) from exc
            if len(found) != len(batch):
                raise EmbeddingError(
                    f"embedding model {model!r} answered {len(found)} of "
                    f"{len(batch)} texts"
                )
            vectors.extend(found)
        return vectors

    return embed


def default_store() -> Path:
    return Path.home() / ".kosika" / "embeddings.sqlite3"


class Meaning:
    """Ranks texts by nearness of meaning, remembering every vector it makes."""

    def __init__(
        self,
        embedder: Embedder,
        model: str = "",
        store: Path | None = None,
    ) -> None:
        self.embedder = embedder
        self.model = model
        self.store = store
        self.query_prefix, self.text_prefix = _prefixes(model)
        self._memory: dict[str, list[float]] = {}

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\0{text}".encode()).hexdigest()

    def _open(self) -> sqlite3.Connection | None:
        if self.store is None:
            return None
        try:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.store, timeout=5)
            connection.execute(
                "CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB)"
            )
            return connection
        except (OSError, sqlite3.Error):
            # Without a store every vector is simply made again.
            return None

    def vectors(
        self, texts: Sequence[str], limit: int | None = None
    ) -> list[list[float] | None]:
        """One vector per text. At most `limit` new ones are made; a text
        beyond that with no remembered vector gets None."""

        keys = [self._key(text) for text in texts]
        connection = self._open()
        try:
            if connection is not None:
                wanted = [key for key in set(keys) if key not in self._memory]
                for start in range(0, len(wanted), 500):
                    part = wanted[start : start + 500]
                    marks = ",".join("?" * len(part))
                    for key, blob in connection.execute(
                        f"SELECT key, vector FROM vectors WHERE key IN ({marks})", part
                    ):
                        self._memory[key] = array("f", blob).tolist()
            missing: dict[str, str] = {}
            for key, text in zip(keys, texts):
                if key not in self._memory and key not in missing:
                    if limit is not None and len(missing) >= limit:
                        continue
                    missing[key] = text
            if missing:
                made = self.embedder(list(missing.values()))
                for key, vector in zip(missing, made):
                    self._memory[key] = [float(number) for number in vector]
                if connection is not None:
                    connection.executemany(
                        "INSERT OR REPLACE INTO vectors VALUES (?, ?)",
                        [
                            (key, array("f", self._memory[key]).tobytes())
                            for key in missing
                        ],
                    )
                    connection.commit()
        except sqlite3.Error:
            pass
        finally:
            if connection is not None:
                connection.close()
        return [self._memory.get(key) for key in keys]

    def nearest(
        self, question: str, texts: Sequence[str], limit: int | None = None
    ) -> list[tuple[float, int]]:
        """(score, position) for every text with a vector, nearest first."""

        asked = self.embedder([self.query_prefix + question])[0]
        length = math.sqrt(sum(number * number for number in asked)) or 1.0
        scored: list[tuple[float, int]] = []
        found = self.vectors([self.text_prefix + text for text in texts], limit)
        for position, vector in enumerate(found):
            if vector is None or len(vector) != len(asked):
                continue
            other = math.sqrt(sum(number * number for number in vector)) or 1.0
            dot = sum(a * b for a, b in zip(asked, vector))
            scored.append((dot / (length * other), position))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return scored
