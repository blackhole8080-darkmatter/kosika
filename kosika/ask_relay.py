"""Answer a run's asks from anywhere, through a push relay.

    kosika answer FOLDER --relay https://ntfy.sh

`--serve` needs the phone on the same Wi-Fi. A relay does not: each ask
waiting in FOLDER is sent as a push notification with a Yes and a No
button, through an ntfy server (the free public one, or your own). Pressing
a button posts the answer to a second topic, which this process reads by
calling out, so nothing on the laptop is opened to the internet.

What keeps it safe:

* nothing is sent unless the person starts the relay and names the server;
  what is sent is the ask's one line (what would be done), nothing more;
* the two topics are long random names made once and kept in
  FOLDER/.relay.json, so the phone subscribes once; whoever knows the ask
  topic could answer, exactly as whoever has the `--serve` address could;
* every ask gets its own one-time token, carried by its buttons: a reply
  is taken only with the token of an ask that is still waiting, so an old
  or made-up reply can approve nothing;
* the answer is written with `ask_folder.answer`, so it stays bound to the
  ask's own plan and action, and a run that has stopped waiting is not
  answered at all.

Either engine's runs can be answered: the folder protocol is the same.
"""

from __future__ import annotations

import json
import queue
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .ask_folder import AskFolderError, answer, waiting

RELAY_FILE = ".relay.json"
MAX_SAYS = 1000


class AskRelayError(ValueError):
    """The relay cannot start with what it was given."""


def relay_topics(folder: Path) -> dict[str, str]:
    """The folder's two topics, made the first time and kept after."""

    path = folder / RELAY_FILE
    try:
        kept = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(kept, dict) and all(isinstance(kept.get(k), str) and len(kept[k]) >= 24 for k in ("asks", "replies")):
            return {"asks": kept["asks"], "replies": kept["replies"]}
    except (OSError, ValueError):
        pass
    folder.mkdir(parents=True, exist_ok=True)
    topics = {
        "asks": "kosika-asks-" + secrets.token_hex(12),
        "replies": "kosika-replies-" + secrets.token_hex(12),
    }
    path.write_text(json.dumps(topics, indent=2), encoding="utf-8")
    return topics


def _server(url: str) -> str:
    url = url.strip().rstrip("/")
    if not (url.startswith("https://") or url.startswith("http://127.0.0.1") or url.startswith("http://localhost")):
        raise AskRelayError("the relay server must be https:// (or http://127.0.0.1 for one on this computer)")
    return url


def notification(server: str, topics: dict[str, str], ask: dict[str, Any], token: str) -> dict[str, Any]:
    """The push for one ask: its line, and a Yes and a No that post back."""

    says = str(ask.get("says", ""))
    if len(says) > MAX_SAYS:
        says = says[:MAX_SAYS] + " …"

    def button(label: str, reply: str) -> dict[str, Any]:
        return {
            "action": "http",
            "label": label,
            "url": f"{server}/{topics['replies']}",
            "method": "POST",
            "body": f"{ask['id']} {reply} {token}",
            "clear": True,
        }

    return {
        "topic": topics["asks"],
        "title": "Kosika asks",
        "message": says,
        "tags": ["robot"],
        "priority": 4,
        "actions": [button("Yes, do it", "yes"), button("No", "no")],
    }


def read_reply(text: str) -> tuple[str, str, str] | None:
    """An answer posted by a button: ask id, yes or no, token."""

    parts = text.strip().split()
    if len(parts) != 3 or parts[1] not in ("yes", "no"):
        return None
    return parts[0], parts[1], parts[2]


class Relay:
    def __init__(
        self,
        folder: Path,
        server: str,
        by: str,
        say: Callable[[str], None] = print,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.folder = folder
        self.server = _server(server)
        self.by = by
        self.say = say
        self.open = opener
        self.topics = relay_topics(folder)
        self.tokens: dict[str, str] = {}
        self.replies: "queue.Queue[str]" = queue.Queue()
        self.stop = threading.Event()

    # ------------------------------------------------------------ talking to the server
    def publish(self, message: dict[str, Any]) -> None:
        request = urllib.request.Request(
            self.server + "/",
            data=json.dumps(message, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.open(request, timeout=20) as response:
            response.read()

    def _listen(self) -> None:
        since = str(int(time.time()) - 5)
        while not self.stop.is_set():
            url = f"{self.server}/{self.topics['replies']}/json?since={since}"
            try:
                with self.open(url, timeout=60) as stream:
                    for raw in stream:
                        if self.stop.is_set():
                            return
                        try:
                            event = json.loads(raw)
                        except ValueError:
                            continue
                        if isinstance(event, dict) and event.get("event") == "message":
                            since = str(event.get("id") or since)
                            self.replies.put(str(event.get("message", "")))
            except (OSError, urllib.error.URLError):
                pass
            self.stop.wait(2)

    # ------------------------------------------------------------ the loop
    def step(self) -> None:
        """Send each new ask once, and take each reply that came in."""

        asks = waiting(self.folder)
        live = {ask["id"] for ask in asks}
        for ask in asks:
            if ask["id"] in self.tokens:
                continue
            token = secrets.token_urlsafe(16)
            try:
                self.publish(notification(self.server, self.topics, ask, token))
            except (OSError, urllib.error.URLError) as exc:
                self.say(f"could not send ask {ask['id']}: {exc}")
                continue
            self.tokens[ask["id"]] = token
            self.say(f"sent ask {ask['id']}: {ask.get('says', '')}")
        for gone in [ask_id for ask_id in self.tokens if ask_id not in live]:
            # answered here or elsewhere, or the run stopped waiting: its
            # token can approve nothing any more
            self.tokens[gone] = ""
        while True:
            try:
                text = self.replies.get_nowait()
            except queue.Empty:
                return
            self.take(text)

    def take(self, text: str) -> None:
        reply = read_reply(text)
        if reply is None:
            self.say("ignored a reply that is not an answer")
            return
        ask_id, said, token = reply
        expected = self.tokens.get(ask_id, "")
        if not expected or not secrets.compare_digest(expected, token):
            self.say(f"ignored a reply for {ask_id}: not a waiting ask's own button")
            return
        try:
            done = answer(self.folder, ask_id, said, self.by)
        except AskFolderError as exc:
            self.say(f"not answered: {exc}")
            return
        self.tokens[ask_id] = ""
        self.say(done)
        try:
            self.publish({"topic": self.topics["asks"], "title": "Kosika", "message": done, "tags": ["white_check_mark" if said == "yes" else "x"]})
        except (OSError, urllib.error.URLError):
            pass

    def run(self, every: float = 1.0) -> None:
        listener = threading.Thread(target=self._listen, daemon=True)
        listener.start()
        self.say(f"Relay on {self.server}. In the ntfy app, subscribe to: {self.topics['asks']}")
        try:
            while not self.stop.is_set():
                self.step()
                self.stop.wait(every)
        finally:
            self.stop.set()
