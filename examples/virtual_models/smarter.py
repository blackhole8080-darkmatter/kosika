"""What makes a small local model answer like a bigger one: several tries
with the checks choosing, and answers that passed kept for next time.

Used by learning_router.py; nothing here asks a model by itself.

- best_of: a model whose router.json entry says `"tries": N` is asked up to
  N times for an edit; an answer counts only if every FIND it gives is in
  exactly one place in the lines the request showed (the coder's own rule),
  and of those the answer most of the tries agree on wins. A small model
  that is right two times in three is then right nearly every time, for
  the price of a few seconds more on this computer.
- Kept: every reply is remembered for a while by the request it answered.
  When the program that asked says its change passed its checks (POST
  /v1/kosika/passed), the replies it got since it began are kept for good,
  in ~/.kosika/kept-answers.jsonl, and the same request is answered from
  there at once, with no model. The cloud's passing answers are also the
  examples the next kosika-coder is trained on (kept-answers.jsonl is read
  by make_fixes.py), so each week less of the work needs the cloud.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import Counter
from pathlib import Path

BLOCK = re.compile(r"<<<<<<< FIND\n(.*?)\n=======\n(.*?)\n?>>>>>>> REPLACE", re.S)
SHOWN = re.compile(r"```[^\n]*\n(.*?)\n```", re.S)
KEPT = Path.home() / ".kosika" / "kept-answers.jsonl"
#: How long a reply is remembered, waiting to hear whether it passed.
RECENT_SECONDS = 6 * 3600
MOST_RECENT = 2000


def is_edit_request(body: dict) -> bool:
    text = asked_text(body)
    return "FIND" in text and "```" in text


def asked_text(body: dict) -> str:
    return "\n".join(str(m.get("content") or "") for m in body.get("messages") or [] if m.get("role") == "user")


def content(reply: dict) -> str:
    try:
        return reply["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError, TypeError):
        return ""


def applies(asked: str, answer: str) -> bool:
    """Every FIND of the answer is in exactly one place in what was shown."""
    edits = BLOCK.findall(answer)
    if not edits:
        return False
    shown = SHOWN.findall(asked)
    for find, _put in edits:
        if not find.strip() or sum(block.count(find) for block in shown) != 1:
            return False
    return True


def same(answer: str) -> str:
    """An answer as the vote counts it: its edits, without spacing at line ends."""
    return "\n".join(line.rstrip() for line in "\n".join("\n=\n".join(e) for e in BLOCK.findall(answer)).split("\n"))


def best_of_together(ask, body: dict, tries: int) -> tuple[dict, dict]:
    """best_of with every try asked at once: on a model server that answers
    several requests together (Ollama with OLLAMA_NUM_PARALLEL), N tries
    take about as long as one."""
    from concurrent.futures import ThreadPoolExecutor

    asked = asked_text(body)

    def one(at: int) -> dict:
        varied = dict(body)
        if at:
            varied["temperature"] = 0.7
            varied["seed"] = at
        return ask(varied)

    with ThreadPoolExecutor(max_workers=tries) as pool:
        replies = list(pool.map(one, range(tries)))
    good = [(same(content(r)), r) for r in replies if applies(asked, content(r))]
    if not good:
        return replies[0], {"tries": tries, "applied": 0, "agreed": 0}
    votes = Counter(key for key, _ in good)
    key, count = votes.most_common(1)[0]
    return next(r for k, r in good if k == key), {"tries": tries, "applied": len(good), "agreed": count}


def best_of(ask, body: dict, tries: int) -> tuple[dict, dict]:
    """The reply most tries agree on, among those that apply, and what
    happened: {"tries": n, "applied": k, "agreed": m}. `ask(body)` asks the
    model once. The first try is as asked; later ones vary (temperature,
    seed) so that they are not the same answer again."""
    asked = asked_text(body)
    replies: list[dict] = []
    good: list[tuple[str, dict]] = []
    for at in range(tries):
        varied = dict(body)
        if at:
            varied["temperature"] = 0.7
            varied["seed"] = at
        reply = ask(varied)
        replies.append(reply)
        answer = content(reply)
        if applies(asked, answer):
            good.append((same(answer), reply))
            votes = Counter(key for key, _ in good)
            key, count = votes.most_common(1)[0]
            if count >= 2 or (at == 0 and tries == 1):
                break
    if not good:
        return replies[0], {"tries": len(replies), "applied": 0, "agreed": 0}
    votes = Counter(key for key, _ in good)
    key, count = votes.most_common(1)[0]
    chosen = next(reply for k, reply in good if k == key)
    return chosen, {"tries": len(replies), "applied": len(good), "agreed": count}


def key_of(body: dict) -> str:
    wanted = {"messages": [{"role": m.get("role"), "content": m.get("content")} for m in body.get("messages") or []],
              "tools": body.get("tools")}
    return hashlib.sha256(json.dumps(wanted, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class Kept:
    """Replies remembered by the request they answered: recent ones until
    the asker says they passed, then kept for good — and given up the moment
    a run says it did not pass, or the code the edit was written against has
    moved on.

    A kept answer is a promise that this exact request, asked again, needs no
    model. Two things keep that promise honest: the edit it gives must still
    be in exactly one place in the code the request shows, and a run that
    fails must be able to take the answer back. Without the second, one run
    wrongly judged as passing replays its answer for ever — and the request
    is marked done on it.
    """

    def __init__(self, path: Path = KEPT) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.recent: dict[str, tuple[float, dict, str, dict]] = {}
        self.rows: dict[str, dict] = {}
        #: When each kept answer was last given again, so that a run which
        #: fails can take back the answer it was handed.
        self.used: dict[str, float] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("key") and row.get("reply"):
                    self.rows[row["key"]] = row

    def _rewrite(self) -> None:
        """The file as it now stands. Called with the lock held."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in self.rows.values()),
            encoding="utf-8",
        )

    def find(self, body: dict) -> dict | None:
        key = key_of(body)
        with self.lock:
            row = self.rows.get(key)
            if row is None:
                return None
            if is_edit_request(body) and not applies(asked_text(body), content(row["reply"])):
                # The code has moved on, so this edit fits nowhere now.
                del self.rows[key]
                self._rewrite()
                return None
            self.used[key] = time.time()
            if len(self.used) > MOST_RECENT:
                for old in sorted(self.used, key=lambda k: self.used[k])[: len(self.used) - MOST_RECENT]:
                    del self.used[old]
            reply = json.loads(json.dumps(row["reply"]))
        return reply

    def heard(self, body: dict, reply: dict, model: str) -> None:
        now = time.time()
        with self.lock:
            self.recent[key_of(body)] = (now, reply, model, {"messages": body.get("messages")})
            if len(self.recent) > MOST_RECENT:
                for old in sorted(self.recent, key=lambda k: self.recent[k][0])[: len(self.recent) - MOST_RECENT]:
                    del self.recent[old]

    def passed(self, since: float) -> int:
        """Keep every reply given since `since`: the asker's change passed.
        An answer whose edit is not in the code it was shown is not kept,
        however the run was judged."""
        now = time.time()
        kept = 0
        with self.lock:
            rows = []
            for key, (at, reply, model, asked) in list(self.recent.items()):
                if at < since or now - at > RECENT_SECONDS or key in self.rows:
                    continue
                if is_edit_request(asked) and not applies(asked_text(asked), content(reply)):
                    continue
                row = {"key": key, "at": at, "model": model, "asked": asked, "reply": reply}
                self.rows[key] = row
                rows.append(row)
                kept += 1
            if rows:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as out:
                    out.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        return kept

    def refused(self, since: float) -> int:
        """The asker's change did not pass: forget the replies given since,
        and every kept answer that run was handed. A run that failed is proof
        the answer it relied on was wrong, so the next try asks a model
        rather than being given the same answer again."""
        forgotten = 0
        with self.lock:
            for key, (at, _reply, _model, _asked) in list(self.recent.items()):
                if at >= since:
                    del self.recent[key]
            for key, at in list(self.used.items()):
                if at >= since:
                    if self.rows.pop(key, None) is not None:
                        forgotten += 1
                    del self.used[key]
            if forgotten:
                self._rewrite()
        return forgotten
