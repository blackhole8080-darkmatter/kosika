"""A virtual model served to any agent that speaks the OpenAI protocol.

A virtual model is a profile over a provider (`virtual_model.py`): the profile
holds the instructions, the local sources it cites and any verified examples,
and a model the owner already authorized still answers. Until now only Kosika
could use one, because only Kosika knew how to load a profile and read it back.

Serving it at `/v1/chat/completions` makes the same specialist usable by any
agent inside or outside this project -- another assistant, an editor plugin, a
script -- because the OpenAI shape is the one every client already speaks, and
Kosika's own `--base-url` is the same shape. The grounding is unchanged: the
passages are still retrieved from this project's sources and cited.

What is served is metered. Each caller has a name, a key and a daily allowance
of calls and tokens, checked *before* the model is asked, so a caller out of
quota costs nothing. Every call is one line in a journal beside the profile, so
the day's spend can be read back exactly rather than remembered.

Two limits are stated rather than hidden:

* Tokens are the provider's own count when it reports one, and an estimate of
  four characters per token when it does not. Each answer says which it is.
* The server binds to loopback. It is meant for agents on this computer, or for
  a tunnel the owner sets up deliberately -- not for an open port.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .model import ChatMessage, ModelRequest, ModelResponse, ModelUsage

#: The name this server gives a model the profile does not name.
FALLBACK_NAME = "kosika-virtual"
#: Rough characters per token, used only when a provider reports no usage.
CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Quota:
    """What one caller may spend in a day, and in one call."""

    calls_per_day: int = 200
    tokens_per_day: int = 200_000
    tokens_per_call: int = 8_000

    def clipped(self, asked: int | None) -> int:
        """The output budget for one call, never above the allowance."""

        wanted = self.tokens_per_call if asked is None else int(asked)
        return max(1, min(wanted, self.tokens_per_call))


class UsageJournal:
    """Every call, one line, so a day's spend is read back rather than guessed."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def record(self, entry: Mapping[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(dict(entry), sort_keys=True) + "\n")

    def entries(self, *, day: str | None = None) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        found: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                # A half-written line from a stopped run is skipped, not fatal:
                # the journal is evidence, not the authority for a decision.
                continue
            if isinstance(entry, dict) and (day is None or entry.get("day") == day):
                found.append(entry)
        return found

    def spent(self, caller: str, day: str) -> tuple[int, int]:
        """Calls and tokens this caller has used on this day."""

        calls = 0
        tokens = 0
        for entry in self.entries(day=day):
            if entry.get("caller") != caller:
                continue
            if entry.get("cached"):
                # A repeated question costs nothing, so it spends no allowance.
                continue
            calls += 1
            tokens += int(entry.get("tokens") or 0)
        return calls, tokens


@dataclass(frozen=True)
class Spend:
    """One caller on one day: what it cost, and what the cache gave back."""

    day: str
    caller: str
    calls: int
    tokens: int
    repeats: int
    saved_tokens: int
    refusals: int = 0


def report(journal: UsageJournal, *, days: int | None = None) -> list[Spend]:
    """Every caller's spend per day, read back from the journal rather than remembered.

    A repeat is counted apart from a call and adds no tokens, so the report shows
    the allowance spent and the work avoided as two different numbers.
    """

    grouped: dict[tuple[str, str], dict[str, int]] = {}
    for entry in journal.entries():
        day = str(entry.get("day") or "")
        caller = str(entry.get("caller") or "")
        if not day or not caller:
            continue
        bucket = grouped.setdefault(
            (day, caller),
            {"calls": 0, "tokens": 0, "repeats": 0, "saved": 0, "refusals": 0},
        )
        if entry.get("cached"):
            bucket["repeats"] += 1
            bucket["saved"] += int(entry.get("saved_tokens") or 0)
            continue
        bucket["calls"] += 1
        bucket["tokens"] += int(entry.get("tokens") or 0)
        if entry.get("refused"):
            bucket["refusals"] += 1
    found = [
        Spend(
            day,
            caller,
            item["calls"],
            item["tokens"],
            item["repeats"],
            item["saved"],
            item["refusals"],
        )
        for (day, caller), item in grouped.items()
    ]
    found.sort(key=lambda spend: (spend.day, spend.caller))
    if days is not None and days > 0:
        newest = sorted({spend.day for spend in found})[-days:]
        found = [spend for spend in found if spend.day in newest]
    return found


class Meter:
    """The allowance, checked before a model is asked and charged after."""

    def __init__(
        self,
        journal: UsageJournal,
        quota: Quota,
        *,
        clock: Any = None,
    ) -> None:
        self.journal = journal
        self.quota = quota
        self._clock = clock or time.time

    def day(self) -> str:
        return datetime.fromtimestamp(self._clock(), timezone.utc).strftime("%Y-%m-%d")

    def refuse(self, caller: str) -> str | None:
        """Why this caller may not ask now, or None when it may."""

        day = self.day()
        calls, tokens = self.journal.spent(caller, day)
        if calls >= self.quota.calls_per_day:
            return (
                f"{caller} has used all {self.quota.calls_per_day} calls for {day}; "
                "the allowance returns at midnight UTC"
            )
        if tokens >= self.quota.tokens_per_day:
            return (
                f"{caller} has used all {self.quota.tokens_per_day} tokens for {day}; "
                "the allowance returns at midnight UTC"
            )
        return None

    def remaining(self, caller: str) -> tuple[int, int]:
        calls, tokens = self.journal.spent(caller, self.day())
        return (
            max(0, self.quota.calls_per_day - calls),
            max(0, self.quota.tokens_per_day - tokens),
        )

    def charge(
        self,
        caller: str,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated: bool,
        cached: bool = False,
        saved_tokens: int = 0,
        grounded: bool | None = None,
        refused: bool = False,
    ) -> None:
        self.journal.record(
            {
                "at": datetime.fromtimestamp(self._clock(), timezone.utc).isoformat(),
                "day": self.day(),
                "caller": caller,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "tokens": prompt_tokens + completion_tokens,
                "estimated": estimated,
                "cached": cached,
                "saved_tokens": saved_tokens,
                "grounded": grounded,
                "refused": refused,
            }
        )


def keys_from(text: str) -> dict[str, str]:
    """`pairs` from `name=KEY,name2=KEY2` to `KEY -> name`.

    A key never appears in a URL, a log line or an error: only the caller's name
    is ever written down, so a journal can be shown without giving access away.
    """

    found: dict[str, str] = {}
    for item in text.replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(f"a key is written name=KEY, not {item!r}")
        name, key = item.split("=", 1)
        name, key = name.strip(), key.strip()
        if not name or not key:
            raise ValueError(f"a key is written name=KEY, not {item!r}")
        found[key] = name
    return found


def new_key() -> str:
    return secrets.token_urlsafe(24)


def normalize_question(messages: tuple[ChatMessage, ...]) -> str:
    """The same question written twice, in one form.

    Whitespace and a trailing question mark carry no meaning, and a caller that
    retypes its question should not be charged for the model reading it again.
    """

    parts = []
    for message in messages:
        text = re.sub(r"\s+", " ", message.content or "").strip()
        if text:
            parts.append(f"{message.role}:{text}")
    return " ".join(parts).casefold().rstrip(" ?.!;,:\"'")


class AnswerCache:
    """Answers already paid for, so the same question asked again is free.

    A kept answer is only served to the question that produced it: the profile,
    the output budget and the grounding mode are part of the key, so a change in
    any of them is a miss rather than a stale answer. An answer that failed the
    grounding check is never kept, so a wrong answer cannot become the answer.

    A kept answer is never re-checked, so it is not served for ever: an answer
    older than the limit is a miss, and the tree that has changed under it is
    never spoken for by it. Zero means keep until pruned.

    The file is a saving, never a dependency: an unreadable or unwritable cache
    costs the next caller a model call and nothing else.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        limit: int = 200,
        max_age_seconds: float = 86_400.0,
        clock: Any = None,
    ) -> None:
        self.path = Path(path) if path is not None else None
        self.limit = max(1, limit)
        self.max_age_seconds = float(max_age_seconds)
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    def _fresh(self, entry: Mapping[str, Any]) -> bool:
        """Whether this answer is young enough to still speak for the tree."""

        if self.max_age_seconds <= 0:
            return True
        value = entry.get("saved_at")
        if value is None:
            return False
        try:
            saved = float(value)
        except (TypeError, ValueError):
            return False
        return (self._clock() - saved) <= self.max_age_seconds

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for key, value in raw.items():
            if (
                isinstance(key, str)
                and isinstance(value, dict)
                and isinstance(value.get("answer"), str)
                and self._fresh(value)
            ):
                self._entries[key] = dict(value)

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._entries, sort_keys=True, indent=0), encoding="utf-8"
            )
        except OSError:
            return

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._entries.get(key)
            if found is None:
                return None
            if not self._fresh(found):
                self._entries.pop(key, None)
                self._save()
                return None
            return dict(found)

    def put(self, key: str, entry: Mapping[str, Any]) -> None:
        with self._lock:
            kept = dict(entry)
            kept.setdefault("saved_at", self._clock())
            self._entries[key] = kept
            while len(self._entries) > self.limit:
                oldest = min(
                    self._entries,
                    key=lambda item: str(self._entries[item].get("at") or ""),
                )
                self._entries.pop(oldest, None)
            self._save()


#: Put in front of the model whenever the answers are going to be checked.
GROUNDING_RULE = (
    "Grounding rule: name no file, path, folder, command, flag or module that does "
    'not appear in the documents above. Cite each passage you use as [path:line] "'
    'the exact words on that line", copying the path as the passages write it -- '
    'the word "source" is not a path. When you name a file, path or flag, the '
    "citation must be a line that shows it. If a detail is not in them, say it "
    "is not known rather than naming one."
)

#: Bumped when the check changes, so an answer kept under an older rule is a miss.
GROUNDING_VERSION = "5"

_CLAIM_PATH = re.compile(
    r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+|[\w.-]+\.(?:py|jsonl|json|md|toml|kos|go|ya?ml|txt|ini|cfg|sh))(?![\w/])"
)
_CLAIM_FLAG = re.compile(r"(?<![\w-])(--[A-Za-z][A-Za-z0-9-]{1,40})")
_CLAIM_FOLDER = re.compile(
    r"`?([A-Za-z][\w.-]{1,40})`?\s+(?:sub)?(?:folder|directory)\b", re.IGNORECASE
)
_CLAIM_TICK = re.compile(r"`([^`\n]{1,160})`")


def claims_in(answer: str) -> tuple[str, ...]:
    """The names an answer commits to: paths, files, flags and folders.

    Prose claims are not checked, and the limit is stated rather than hidden:
    what this finds is the invention a caller could act on by copying it into a
    shell or opening it, which is where an invented name does its damage.
    """

    found: list[str] = []
    for pattern in (_CLAIM_PATH, _CLAIM_FLAG, _CLAIM_FOLDER):
        for token in pattern.findall(answer):
            # A leading dot belongs to the name (`.kosika`, `.env`); only a
            # trailing one is the punctuation of the sentence around it.
            token = token.strip("`'\" \t\n,;:()[]{}").rstrip(".")
            if token and token not in found:
                found.append(token)
    for span in _CLAIM_TICK.findall(answer):
        token = span.strip()
        if not token or " " in token or len(token) > 80:
            continue
        if any(character in token for character in "/.\\-_"):
            if token not in found:
                found.append(token)
    return tuple(found)


def _named(haystack: str, token: str) -> bool:
    """Whether the documents name this token, rather than merely contain it.

    A document that says `failed` does not name `fail`, and a document that says
    `retested` does not name `test`. A path is named when the documents name it,
    or name every part of it: `ledger.jsonl` beside `.kosika` and `selfdev` is a
    real file even if that one line never appears in the material.
    """

    def whole(part: str) -> bool:
        return bool(re.search(rf"(?<![\w.-]){re.escape(part)}(?![\w])", haystack))

    needle = token.casefold()
    if "/" in needle or "\\" in needle:
        if needle in haystack:
            return True
        parts = [part for part in re.split(r"[/\\]", needle) if part]
        return len(parts) > 1 and all(whole(part) for part in parts)
    if "." in needle or needle.startswith("--"):
        return whole(needle)
    if "_" in needle:
        # An identifier with an underscore is a name in code, not a prose word
        # that a sentence could shadow: `failed` proves nothing about `fail`,
        # but `max_steps` in a specification is that name and nothing else.
        return whole(needle)
    # A bare name is claimed as a folder: the documents must show it in a path.
    # A document that merely says the word does not say the folder exists.
    return bool(
        re.search(rf"[/\\]{re.escape(needle)}(?![\w])", haystack)
        or re.search(rf"(?<![\w.-]){re.escape(needle)}[/\\]", haystack)
    )


def unsupported_claims(
    answer: str, reference: str, *, elsewhere: str | None = None
) -> tuple[str, ...]:
    """The claims neither the reference text nor the sources support.

    What this can certify is that a name the answer acts on was in the material
    the model was given, or in the sources it was drawn from. The second half
    matters: a retrieved slice is not the profile, so a real key in the profile's
    own file was being refused as an invention -- a refusal that costs a caller
    tokens and teaches the model nothing. What is left is a name that exists
    nowhere, which is the invention this check is for.

    It still cannot certify that the answer used a real name correctly, and this
    project records the difference rather than pretending the check is stronger
    than it is.
    """

    haystack = re.sub(r"\s+", " ", reference).casefold()
    wider = (
        re.sub(r"\s+", " ", elsewhere).casefold()
        if isinstance(elsewhere, str) and elsewhere
        else None
    )
    return tuple(
        token
        for token in claims_in(answer)
        if not _named(haystack, token) and not (wider and _named(wider, token))
    )


_CITATION = re.compile(
    r"\[([\w./\\-]+\.[A-Za-z0-9]+):(\d+)[^\]]*\]"
    r"|(?<![\w/])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]+):(\d+)(?![\d])"
)
_QUOTE_AFTER = re.compile(r"^[\s,;:().\-—]*[`\"']([^`\"'\n]{3,120})[`\"']")


def citation_faults(answer: str, line_at: Any) -> tuple[str, ...]:
    """Citations that do not resolve to a line in the documents, or misquote it.

    A citation is checked against the profile's own sources, which are what the
    documents were taken from: a source that does not exist, a line that does
    not exist, and a quotation that is not on the line it is attributed to are
    all faults. An answer citing nothing is not faulted here, and that limit is
    stated rather than hidden -- prose claims remain unchecked either way.
    """

    if not callable(line_at):
        return ()
    faults: list[str] = []
    for match in _CITATION.finditer(answer):
        source = match.group(1) or match.group(3)
        line = int(match.group(2) or match.group(4))
        citation = f"{source}:{line}"
        text = line_at(source, line)
        if text is None:
            faults.append(citation)
            continue
        quoted = _QUOTE_AFTER.match(answer[match.end() :])
        if quoted and quoted.group(1).strip().casefold() not in text.casefold():
            faults.append(citation)
    return tuple(dict.fromkeys(faults))


def unquoted_citations(answer: str, line_at: Any) -> tuple[str, ...]:
    """Citations that resolve but are followed by no quotation of the line.

    Not a fault, and deliberately not a refusal: most citations a 7B writes are
    bare line references, and refusing those would refuse answers that are being
    honest about where they looked. Counted so the weakness is visible -- a line
    reference is a location, not evidence, and the two are reported apart.
    """

    if not callable(line_at):
        return ()
    found: list[str] = []
    for match in _CITATION.finditer(answer):
        source = match.group(1) or match.group(3)
        line = int(match.group(2) or match.group(4))
        if line_at(source, line) is None:
            continue
        if not _QUOTE_AFTER.match(answer[match.end() :]):
            found.append(f"{source}:{line}")
    return tuple(dict.fromkeys(found))


def _is_file_or_flag(token: str) -> bool:
    """The claims that come with a destination: a path, a file, or a flag.

    A folder the prose mentions and a bare identifier are not included: they are
    checked against the documents, but requiring a citation for every word a
    model backticks would refuse answers that name nothing a caller could act on.
    """

    return token.startswith("--") or "/" in token or "\\" in token or "." in token


def unbacked_claims(answer: str, line_at: Any) -> tuple[str, ...]:
    """Files and flags the answer names without a citation that shows them.

    This is the narrow requirement the measurement supports: not a citation per
    sentence, which a 7B will not write, but a citation for the thing a caller
    could act on. The cited line must contain the name -- or the file's own
    name, since a passage may show `.kosika/selfdev/ledger.jsonl` where the
    answer writes `ledger.jsonl` -- so the check is about evidence, not about
    having produced brackets.
    """

    if not callable(line_at):
        return ()
    cited_paths: list[str] = []
    cited_text: list[str] = []
    for match in _CITATION.finditer(answer):
        source = match.group(1) or match.group(3)
        line = int(match.group(2) or match.group(4))
        cited_paths.append(source.replace("\\", "/").casefold())
        text = line_at(source, line)
        if text:
            cited_text.append(text.casefold())
    missing: list[str] = []
    for token in claims_in(answer):
        if not _is_file_or_flag(token):
            continue
        needle = token.replace("\\", "/").casefold()
        leaf = needle.rsplit("/", 1)[-1]
        # The citation itself points at the file, which is the usual way an
        # answer backs a path; a line that writes the name out also backs it.
        backs = any(
            path == needle or path.endswith("/" + needle) or needle.endswith("/" + path)
            for path in cited_paths
        ) or any(leaf and leaf in text for text in cited_text)
        if backs:
            continue
        missing.append(token)
    return tuple(dict.fromkeys(missing))


def _refusal(
    missing: tuple[str, ...],
    citations: tuple[str, ...] = (),
    unbacked: tuple[str, ...] = (),
) -> str:
    """Our words, not the model's, for an answer its documents do not support."""

    reasons = []
    if missing:
        reasons.append(
            "it names "
            + ", ".join(f"`{item}`" for item in missing)
            + ", which do not appear in the documents I was given"
        )
    if citations:
        reasons.append(
            "it cites "
            + ", ".join(f"`{item}`" for item in citations)
            + ", which do not resolve to a line in those documents"
        )
    if unbacked:
        reasons.append(
            "it names "
            + ", ".join(f"`{item}`" for item in unbacked)
            + ", and no citation shows that name on a line of those documents"
        )
    return (
        "I cannot answer that: "
        + "; ".join(reasons)
        + ". The answer may be invented, so nothing in it should be acted on."
    )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _estimate(text: str | None) -> int:
    return max(1, len(text or "") // CHARS_PER_TOKEN)


class Gateway:
    """The HTTP surface, as a function of one request.

    Nothing here opens a socket, so the rules a caller meets -- the key, the
    quota, the shape of the answer -- are tested without one.
    """

    def __init__(
        self,
        specialist: Any,
        meter: Meter,
        *,
        name: str = FALLBACK_NAME,
        keys: Mapping[str, str] | None = None,
        cache: AnswerCache | None = None,
        ground: bool = True,
        require_citations: bool = False,
        on_hit: Any = None,
        clock: Any = None,
    ) -> None:
        self.specialist = specialist
        self.meter = meter
        self.name = name
        self.keys = dict(keys or {})
        self.cache = cache
        self.ground = ground
        #: Measured at 8 refusals in 20 answers, every one of them naming a real
        #: file or flag: requiring a citation for a name refuses the truth as
        #: readily as an invention. Reported by default; this makes it a gate.
        self.require_citations = require_citations
        self._on_hit = on_hit
        self._count_lock = threading.Lock()
        #: Repeats served from the cache, and the tokens they did not spend.
        self.hits = 0
        self.saved_tokens = 0
        self._clock = clock or time.time

    # -- one request --------------------------------------------------------

    def handle(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        route = path.split("?", 1)[0].rstrip("/") or "/"
        if method == "GET" and route in ("/v1/models", "/models"):
            return 200, self._models(), {}
        if method == "POST" and route in ("/v1/chat/completions", "/chat/completions"):
            return self._chat(headers, body)
        return 404, {"error": {"message": f"no route {method} {route}"}}, {}

    def _models(self) -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": self.name,
                    "object": "model",
                    "owned_by": "kosika",
                    "created": int(self._clock()),
                }
            ],
        }

    def _caller(self, headers: Mapping[str, str]) -> tuple[str | None, str | None]:
        """The caller's name, or the reason the request is refused."""

        said = _header(headers, "authorization") or ""
        token = said[7:].strip() if said.lower().startswith("bearer ") else said.strip()
        if not token:
            return None, "no key: send Authorization: Bearer KEY"
        caller = self.keys.get(token)
        if caller is None:
            return None, "that key is not one of this server's"
        return caller, None

    def _chat(
        self, headers: Mapping[str, str], body: bytes
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        caller, refusal = self._caller(headers)
        if caller is None:
            return 401, {"error": {"message": refusal, "type": "invalid_api_key"}}, {}
        try:
            asked = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, ValueError):
            return 400, {"error": {"message": "the body was not JSON"}}, {}
        if not isinstance(asked, dict):
            return 400, {"error": {"message": "the body was not one object"}}, {}
        messages = self._messages(asked.get("messages"))
        if isinstance(messages, str):
            return 400, {"error": {"message": messages}}, {}
        temperature = self._float(asked.get("temperature"))
        budget = self.meter.quota.clipped(self._int(asked.get("max_tokens")))
        checking = self.ground and callable(
            getattr(self.specialist, "context_for", None)
        )
        key = self._cache_key(messages, temperature, budget, checking=checking)
        if key is not None and self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                # A kept answer costs nothing, so it is served even to a caller
                # whose allowance is spent: the allowance bounds the model, and
                # this call does not reach it.
                return (
                    200,
                    self._cached_answer(caller, hit),
                    {"X-Kosika-Cache": "hit", **self._left(caller)},
                )
        refused = self.meter.refuse(caller)
        if refused is not None:
            calls_left, tokens_left = self.meter.remaining(caller)
            return (
                429,
                {"error": {"message": refused, "type": "quota_exhausted"}},
                {
                    "Retry-After": "3600",
                    "X-Kosika-Calls-Remaining": str(calls_left),
                    "X-Kosika-Tokens-Remaining": str(tokens_left),
                },
            )
        rule = (ChatMessage(role="system", content=GROUNDING_RULE),) if checking else ()
        request = ModelRequest(
            messages=(*rule, *messages),
            temperature=temperature,
            max_output_tokens=budget,
        )
        try:
            reply = self.specialist.complete(request)
        except Exception as exc:  # noqa: BLE001 - a provider must not close the server
            return (
                502,
                {"error": {"message": f"{type(exc).__name__}: {exc}", "type": "provider"}},
                {},
            )
        unsupported: tuple[str, ...] = ()
        cited: tuple[str, ...] = ()
        unquoted: tuple[str, ...] = ()
        unbacked: tuple[str, ...] = ()
        grounded: bool | None = None
        if checking:
            reference = self._reference(request)
            if reference is not None:
                unsupported = unsupported_claims(
                    reply.content or "", reference, elsewhere=self._elsewhere()
                )
                reader = getattr(self.specialist, "line_at", None)
                cited = citation_faults(reply.content or "", reader)
                unquoted = unquoted_citations(reply.content or "", reader)
                unbacked = unbacked_claims(reply.content or "", reader)
                grounded = (
                    not unsupported
                    and not cited
                    and not (unbacked and self.require_citations)
                )
        answer = self._answer(
            caller,
            request,
            reply,
            grounded=grounded,
            unsupported=unsupported,
            cited=cited,
            unquoted=unquoted,
            unbacked=unbacked,
        )
        answer["kosika"]["cache"] = "miss" if key is not None else "off"
        if unsupported or cited or (unbacked and self.require_citations):
            answer["choices"][0]["message"]["content"] = _refusal(
                unsupported,
                cited,
                unbacked if self.require_citations else (),
            )
            answer["choices"][0]["finish_reason"] = "stop"
        headers = self._left(caller)
        if key is not None:
            headers["X-Kosika-Cache"] = "miss"
            if (
                self.cache is not None
                and not unsupported
                and not cited
                and not (unbacked and self.require_citations)
            ):
                self.cache.put(
                    key,
                    {
                        "answer": reply.content or "",
                        "finish_reason": reply.finish_reason,
                        "prompt_tokens": answer["usage"]["prompt_tokens"],
                        "completion_tokens": answer["usage"]["completion_tokens"],
                        "estimated": answer["usage"]["estimated"],
                        "model": self.name,
                        "at": datetime.fromtimestamp(
                            self._clock(), timezone.utc
                        ).isoformat(),
                    },
                )
        return 200, answer, headers

    def _cache_key(
        self,
        messages: tuple[ChatMessage, ...],
        temperature: float | None,
        budget: int,
        *,
        checking: bool,
    ) -> str | None:
        """What a kept answer is kept for, or None when this call cannot be kept."""

        if self.cache is None:
            return None
        if temperature is not None and temperature > 0:
            # The caller asked for variety; a kept answer is its opposite.
            return None
        material = "\n".join(
            (
                f"checked-{GROUNDING_VERSION}" if checking else "open",
                str(budget),
                str(getattr(self.specialist, "plan_identity", "") or ""),
                normalize_question(messages),
            )
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _elsewhere(self) -> str | None:
        """Every source the profile has, not only the slice this call retrieved.

        A name is an invention when it exists nowhere, not when this question did
        not happen to retrieve it.
        """

        reader = getattr(self.specialist, "sources_text", None)
        if not callable(reader):
            return None
        try:
            return reader()
        except Exception:  # noqa: BLE001 - a wider check is a bonus, not a gate
            return None

    def _reference(self, request: ModelRequest) -> str | None:
        """The documents the profile puts in front of the model, if it says."""

        reader = getattr(self.specialist, "context_for", None)
        if not callable(reader):
            return None
        try:
            return reader(request)
        except Exception:  # noqa: BLE001 - a check that fails is not an answer
            return None

    def _left(self, caller: str) -> dict[str, str]:
        calls_left, tokens_left = self.meter.remaining(caller)
        return {
            "X-Kosika-Calls-Remaining": str(calls_left),
            "X-Kosika-Tokens-Remaining": str(tokens_left),
        }

    def _cached_answer(self, caller: str, hit: Mapping[str, Any]) -> dict[str, Any]:
        """A kept answer, reported with what it cost then and what it costs now."""

        prompt_tokens = int(hit.get("prompt_tokens") or 0)
        completion_tokens = int(hit.get("completion_tokens") or 0)
        total = prompt_tokens + completion_tokens
        with self._count_lock:
            self.hits += 1
            self.saved_tokens += total
        if self._on_hit is not None:
            try:
                self._on_hit(total)
            except Exception:  # noqa: BLE001 - a printer must not fail a request
                pass
        self.meter.charge(
            caller,
            model=self.name,
            prompt_tokens=0,
            completion_tokens=0,
            estimated=False,
            cached=True,
            saved_tokens=total,
            grounded=True,
        )
        return {
            "id": f"chatcmpl-{secrets.token_hex(12)}",
            "object": "chat.completion",
            "created": int(self._clock()),
            "model": self.name,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": str(hit.get("answer") or ""),
                    },
                    "finish_reason": hit.get("finish_reason") or "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total,
                "estimated": bool(hit.get("estimated")),
                "caller": caller,
                # What the answer cost when it was made, and what this caller paid.
                "cached": True,
                "charged_tokens": 0,
                "saved_tokens": total,
            },
            "kosika": {
                "grounded": True,
                "unsupported": [],
                "bad_citations": [],
                "cache": "hit",
            },
        }

    def _messages(self, raw: Any) -> tuple[ChatMessage, ...] | str:
        """The messages as Kosika reads them, or why they cannot be read."""

        if not isinstance(raw, list) or not raw:
            return "messages must be a non-empty list"
        found: list[ChatMessage] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                return f"message {index + 1} is not an object"
            role = item.get("role")
            content = item.get("content")
            if not isinstance(role, str) or role not in ("system", "user", "assistant", "tool"):
                return f"message {index + 1} needs a role of system, user, assistant or tool"
            if isinstance(content, list):
                # The OpenAI shape allows parts; only their text is used, since
                # a virtual model's sources are text and images are not grounded.
                content = "".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            if content is not None and not isinstance(content, str):
                return f"message {index + 1} content must be text"
            found.append(ChatMessage(role=role, content=content or ""))
        return tuple(found)

    def _answer(
        self,
        caller: str,
        request: ModelRequest,
        reply: ModelResponse,
        *,
        grounded: bool | None = None,
        unsupported: tuple[str, ...] = (),
        cited: tuple[str, ...] = (),
        unquoted: tuple[str, ...] = (),
        unbacked: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        usage = reply.usage or ModelUsage()
        estimated = usage.prompt_tokens is None or usage.completion_tokens is None
        prompt_tokens = (
            usage.prompt_tokens
            if usage.prompt_tokens is not None
            else _estimate("".join(message.content for message in request.messages))
        )
        completion_tokens = (
            usage.completion_tokens
            if usage.completion_tokens is not None
            else _estimate(reply.content)
        )
        self.meter.charge(
            caller,
            model=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated=estimated,
            grounded=grounded,
            refused=bool(
                unsupported or cited or (unbacked and self.require_citations)
            ),
        )
        created = int(self._clock())
        return {
            "id": f"chatcmpl-{secrets.token_hex(12)}",
            "object": "chat.completion",
            "created": created,
            "model": self.name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply.content or ""},
                    "finish_reason": reply.finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                # Not part of the OpenAI shape; a caller that cares can tell a
                # counted number from a guessed one instead of trusting both.
                "estimated": estimated,
                "caller": caller,
            },
            # Not part of the OpenAI shape: whether the names in this answer
            # were checked against the documents, and what was kept of it.
            "kosika": {
                "grounded": grounded,
                "unsupported": list(unsupported),
                "bad_citations": list(cited),
                "unquoted_citations": list(unquoted),
                "unbacked": list(unbacked),
                "cache": "off",
            },
        }

    @staticmethod
    def _int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None


def make_server(
    gateway: Gateway, *, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    """A server that answers the gateway's rules over HTTP."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _answer(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            status, payload, extra = gateway.handle(
                method, self.path, dict(self.headers), body
            )
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for name, value in extra.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
            self._answer("GET")

        def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
            self._answer("POST")

        def log_message(self, *args: Any) -> None:
            # A served model says nothing per request: the journal is the record.
            return

    return ThreadingHTTPServer((host, port), Handler)


def address(server: ThreadingHTTPServer) -> str:
    host, port = server.server_address[:2]
    if isinstance(host, bytes):
        host = host.decode("utf-8", "replace")
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    return f"http://{shown}:{port}"
