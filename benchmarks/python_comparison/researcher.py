"""The researcher agent, written in plain Python for comparison with Kosika.

It does what examples/agents/researcher/main.kos does: answer a question
from the text files in one folder, and show the answer only with a quote
that is really in one of the lines that were found.

    python researcher.py --folder NOTES --model qwen2.5:3b --strong qwen2.5:7b "When is the demo?"

It uses only the standard library and talks to a local Ollama server. It is
written the way a careful developer would write it for themselves: it works
and it is tested, but it has only what its author thought to add.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

MODEL_URL = "http://127.0.0.1:11434/api/chat"
MAX_FILE_BYTES = 1024 * 1024
MAX_MATCHES = 40
MAX_FILES = 5000
MAX_SEARCH_SECONDS = 8.0
SKIPPED_FOLDERS = {"node_modules", "__pycache__", "venv", "dist", "build", "target"}
SMALL_WORDS = set(
    "a an the is are was were be do does did i me my we our you your it its of in "
    "on at to for from by with about and or what when where which who whom why how "
    "tell say says said there that this these those have has had can could should "
    "would will please any all much many need find search show".split()
)

# A model is anything that takes a prompt and the fields wanted, and gives a dict.
Model = Callable[[str, tuple[str, ...]], dict]


# ---------------------------------------------------------------- the folder


def inside(folder: Path, relative: str) -> Path:
    """Resolve a path inside the folder, refusing anything that leaves it."""

    candidate = Path(relative)
    if not relative.strip() or candidate.is_absolute() or candidate.drive:
        raise ValueError(f"path {relative!r} must be relative to the folder")
    current = folder
    for part in candidate.parts:
        if part in ("..", ""):
            raise ValueError(f"path {relative!r} leaves the folder")
        current = current / part
        if current.is_symlink():
            raise ValueError(f"path {relative!r} passes through a symlink")
    resolved = current.resolve()
    if resolved != folder and folder not in resolved.parents:
        raise ValueError(f"path {relative!r} leaves the folder")
    return resolved


def search(folder: Path, query: str) -> list[str]:
    """Lines under the folder holding the words, as path:line: text, best first."""

    words = [word for word in query.lower().split() if word]
    if not words:
        return []
    deadline = time.monotonic() + MAX_SEARCH_SECONDS
    found: list[tuple[int, str, int, str]] = []
    seen = 0
    pending = [folder]
    while pending and seen < MAX_FILES and time.monotonic() < deadline:
        current = pending.pop(0)
        try:
            entries = sorted(current.iterdir(), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in SKIPPED_FOLDERS:
                    pending.append(entry)
                continue
            seen += 1
            try:
                if entry.stat().st_size > MAX_FILE_BYTES:
                    continue
                text = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            name = entry.relative_to(folder).as_posix()
            for number, row in enumerate(text.splitlines(), 1):
                hits = sum(1 for word in words if word in row.lower())
                if hits:
                    found.append((hits, name, number, row.strip()))
    found.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [
        f"{name}:{number}: {row[:240]}" for _, name, number, row in found[:MAX_MATCHES]
    ]


def peek(folder: Path, relative: str) -> str:
    """The first 4000 characters of a text file in the folder, or ''."""

    try:
        path = inside(folder, relative)
        with path.open("r", encoding="utf-8") as stream:
            return stream.read(4000)
    except (OSError, ValueError, UnicodeDecodeError):
        return ""


# ----------------------------------------------------------------- the model


def ollama(name: str, timeout: float = 300.0) -> Model:
    """A model served by a local Ollama, asked for one JSON object."""

    def ask(prompt: str, fields: tuple[str, ...]) -> dict:
        wanted = ", ".join(f'"{field}": text' for field in fields)
        body = json.dumps(
            {
                "model": name,
                "stream": False,
                "format": "json",
                "options": {"num_predict": 220},
                "messages": [
                    {
                        "role": "system",
                        "content": f"Return only one JSON object: {{{wanted}}}.",
                    },
                    {"role": "user", "content": prompt},
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            MODEL_URL, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                reply = json.loads(response.read().decode("utf-8"))
            answer = json.loads(reply["message"]["content"])
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
            raise RuntimeError(f"the model gave no usable answer: {exc}") from exc
        # Check the shape by hand: every field present, and text.
        if not isinstance(answer, dict) or any(
            not isinstance(answer.get(field), str) for field in fields
        ):
            raise RuntimeError(f"the model's answer is not {fields}: {answer!r}")
        return {field: answer[field] for field in fields}

    return ask


# ------------------------------------------------------------- the questions


def clean(word: str) -> str:
    out = word.lower()
    for mark in "?!,;\"'():":
        out = out.replace(mark, "")
    return out[:-1].strip() if out.endswith(".") else out.strip()


def key_words(text: str, skip_first: bool = False) -> str:
    """The words of a question worth searching for, at most six."""

    words: list[str] = []
    for index, word in enumerate(item for item in text.split() if clean(item)):
        item = clean(word)
        if (
            (index > 0 or not skip_first)
            and len(item) > 1
            and item not in SMALL_WORDS
            and item not in words
        ):
            words.append(item)
    return " ".join(words[:6])


def passages(hits: list[str]) -> str:
    rows: list[str] = []
    size = 0
    for hit in hits:
        if len(rows) < 14 and size + len(hit) < 2600:
            rows.append(hit)
            size += len(hit)
    return "\n".join(rows)


def ask_prompt(question: str, lines: str) -> str:
    return (
        "Answer the question using only the lines below. Each line is written as file:line: text.\n"
        "Give the answer in one or two short sentences.\n"
        "In quote, copy the exact words from one line that prove your answer: the text only, "
        "without the file and line number, and without changing a single character.\n"
        'If the lines do not answer the question, set answer and quote to "".\n\n'
        f"Lines:\n{lines}\n\nQuestion: {question.strip()}"
    )


def bare(quote: str) -> str:
    """A quote without the marks at its two ends."""

    return quote.lower().strip().strip(".,;:!?\"' ")


def proof(hits: list[str], quote: str) -> str:
    """The hit that really holds the quote, or ''."""

    wanted = bare(quote)
    if len(wanted) < 6:
        return ""
    return next((hit for hit in hits if wanted in hit.lower()), "")


def source_of(hit: str) -> str:
    parts = hit.split(":")
    return hit if len(parts) < 3 else f"{parts[0]}, line {parts[1]}"


def read_and_prove(prompt: str, hits: list[str], fast: Model, strong: Model) -> dict:
    """Ask the fast model; if its answer is not proven, ask the strong one."""

    for model in (fast, strong):
        try:
            found = model(prompt, ("answer", "quote"))
        except RuntimeError:
            continue
        if found["answer"].strip() and proof(hits, found["quote"]):
            return found
    return {"answer": "", "quote": ""}


def answer_question(folder: Path, question: str, fast: Model, strong: Model) -> str:
    query = key_words(question)
    if not query:
        return 'Ask me something about the files, or say "find" and a few words.'
    return answer_from(question, query, search(folder, query), fast, strong)


def answer_from(
    question: str, query: str, hits: list[str], fast: Model, strong: Model
) -> str:
    """The answer to a question, given the lines the search found."""

    if not hits:
        return f'I found nothing about "{query}" in the folder. Try other words.'
    found = read_and_prove(ask_prompt(question, passages(hits)), hits, fast, strong)
    hit = proof(hits, found["quote"])
    if not found["answer"].strip() or not hit:
        closest = "\n".join(hits[:5])
        return (
            "I will not guess: I could not back an answer with an exact quote. "
            f'The closest lines I found for "{query}" are:\n{closest}'
        )
    return f'{found["answer"].strip()}\nQuote: "{found["quote"].strip()}"\nFrom: {source_of(hit)}'


def find_lines(folder: Path, text: str) -> str:
    query = key_words(text, skip_first=True)
    if not query:
        return "Tell me what to look for, like this: find invoice 2026"
    hits = search(folder, query)
    if not hits:
        return f'No line in the folder has "{query}".'
    return f'{len(hits)} line(s) for "{query}", best first:\n' + "\n".join(hits[:15])


def path_from(text: str) -> str:
    path = " ".join(text.split()[1:]).replace("\\", "/")
    for mark in "\"'?":
        path = path.replace(mark, "")
    return path.strip()


def read_one(folder: Path, text: str) -> str:
    path = path_from(text)
    if not path:
        return "Tell me which file, like this: read notes/plan.md"
    content = peek(folder, path)
    if not content:
        return f'I could not read "{path}". It may be missing, not text, or outside the folder.'
    return f"{path}:\n{content[:1500]}"


def summarise_one(folder: Path, text: str, fast: Model) -> str:
    path = path_from(text)
    content = peek(folder, path)
    if not content:
        return f'I could not read "{path}", so there is nothing to summarise.'
    try:
        brief = fast(
            f"Summarize this in two or three sentences:\n\n{content[:3500]}", ("text",)
        )
    except RuntimeError:
        return "Sorry, I could not get a summary just now."
    return f"Summary of {path}:\n{brief['text'].strip()}"


HELP = (
    "I answer from the files in the folder, with an exact quote and its line.\n"
    "Ask a question, or: find WORDS, read FILE, summarize FILE. I only read."
)


def respond(folder: Path, text: str, fast: Model, strong: Model) -> str:
    lower = text.lower().strip()
    first = lower.split(" ")[0] if lower else ""
    if lower == "help" or "what can you do" in lower:
        return HELP
    if first in ("find", "search", "grep"):
        return find_lines(folder, text)
    if first in ("read", "open", "show"):
        return read_one(folder, text)
    if first in ("summarize", "summarise"):
        return summarise_one(folder, text, fast)
    return answer_question(folder, text, fast, strong)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("question")
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--strong")
    args = parser.parse_args()
    folder = args.folder.resolve()
    if not folder.is_dir():
        parser.error(f"{args.folder} is not a folder")
    fast = ollama(args.model)
    print(
        respond(
            folder, args.question, fast, ollama(args.strong) if args.strong else fast
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
