"""Answering asks from anywhere through a push relay (kosika answer --relay),
tested against a small stand-in for an ntfy server on this computer."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from kosika import ask_folder
from kosika.ask_relay import AskRelayError, Relay, notification, read_reply, relay_topics


class FakeNtfy:
    """Just enough of ntfy: publish JSON to /, post text to /TOPIC, and
    stream /TOPIC/json."""

    def __init__(self) -> None:
        self.topics: dict[str, list[dict]] = {}
        self.lock = threading.Lock()
        self.closing = threading.Event()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8")
                if self.path == "/":
                    message = json.loads(body)
                    topic = message["topic"]
                else:
                    topic, message = self.path.strip("/"), {"message": body}
                with fake.lock:
                    items = fake.topics.setdefault(topic, [])
                    items.append(dict(message, event="message", id=f"m{len(items)}", time=int(time.time())))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def do_GET(self) -> None:  # noqa: N802
                topic = self.path.split("?")[0].strip("/").removesuffix("/json")
                self.send_response(200)
                self.end_headers()
                sent = 0
                while not fake.closing.is_set():
                    with fake.lock:
                        items = list(fake.topics.get(topic, []))
                    for item in items[sent:]:
                        self.wfile.write((json.dumps(item) + "\n").encode("utf-8"))
                        self.wfile.flush()
                    sent = len(items)
                    time.sleep(0.05)

            def log_message(self, *_: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def messages(self, topic: str) -> list[dict]:
        with self.lock:
            return list(self.topics.get(topic, []))

    def press(self, button: dict) -> None:
        request = urllib.request.Request(button["url"], data=button["body"].encode("utf-8"), method="POST")
        urllib.request.urlopen(request, timeout=5).read()

    def close(self) -> None:
        self.closing.set()
        self.server.shutdown()
        self.server.server_close()


def until(check, seconds: float = 10.0):
    ends = time.monotonic() + seconds
    while time.monotonic() < ends:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("waited too long")


class RelayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp()) / "asks"
        self.ntfy = FakeNtfy()
        self.said: list[str] = []
        self.relay = Relay(self.folder, self.ntfy.url, "aryan", say=self.said.append)
        self.thread = threading.Thread(target=self.relay.run, kwargs={"every": 0.1}, daemon=True)

    def tearDown(self) -> None:
        self.relay.stop.set()
        self.ntfy.close()

    def ask(self) -> str:
        return ask_folder.put_ask(self.folder, {
            "step": ["main", "made"], "tool": "keeper.git", "arguments": {"args": ["status"]},
            "plan_hash": "p", "action_hash": "a",
        })

    def pushed(self, ask_id: str) -> dict:
        return until(lambda: next((m for m in self.ntfy.messages(self.relay.topics["asks"])
                                   if any(ask_id in b["body"] for b in m.get("actions", []))), None))

    def test_topics_are_long_random_and_kept(self) -> None:
        topics = relay_topics(self.folder)
        self.assertEqual(topics, self.relay.topics)
        self.assertTrue(topics["asks"].startswith("kosika-asks-") and len(topics["asks"]) >= 36)
        self.assertNotEqual(topics["asks"], topics["replies"])
        self.assertEqual(ask_folder.waiting(self.folder), [])

    def test_a_yes_pressed_on_the_phone_answers_the_ask(self) -> None:
        self.thread.start()
        ask_id = self.ask()
        push = self.pushed(ask_id)
        self.assertEqual(push["title"], "Kosika asks")
        self.assertIn("keeper.git", push["message"])
        yes, no = push["actions"]
        self.assertEqual((yes["label"], no["label"]), ("Yes, do it", "No"))
        self.ntfy.press(yes)
        answer = self.folder / f"{ask_id}{ask_folder.ANSWER}"
        until(answer.exists)
        self.assertEqual(ask_folder.read_answer(answer), (True, "aryan"))
        until(lambda: any(m["message"].startswith("approved " + ask_id) for m in self.ntfy.messages(self.relay.topics["asks"])))

    def test_a_no_is_a_no(self) -> None:
        self.thread.start()
        ask_id = self.ask()
        self.ntfy.press(self.pushed(ask_id)["actions"][1])
        answer = self.folder / f"{ask_id}{ask_folder.ANSWER}"
        until(answer.exists)
        self.assertEqual(ask_folder.read_answer(answer), (False, "aryan"))

    def test_a_reply_without_the_asks_own_token_approves_nothing(self) -> None:
        self.thread.start()
        ask_id = self.ask()
        self.pushed(ask_id)
        forged = {"url": f"{self.ntfy.url}/{self.relay.topics['replies']}", "body": f"{ask_id} yes guessed"}
        self.ntfy.press(forged)
        until(lambda: any("not a waiting ask's own button" in line for line in self.said))
        self.assertEqual([a["id"] for a in ask_folder.waiting(self.folder)], [ask_id])

    def test_a_button_pressed_twice_or_after_the_ask_ended_does_nothing(self) -> None:
        self.thread.start()
        ask_id = self.ask()
        yes = self.pushed(ask_id)["actions"][0]
        self.ntfy.press(yes)
        answer = self.folder / f"{ask_id}{ask_folder.ANSWER}"
        until(answer.exists)
        # the run read its answer and cleaned up; a second press is old
        answer.unlink()
        (self.folder / f"{ask_id}{ask_folder.ASK}").unlink()
        self.ntfy.press(yes)
        until(lambda: sum("not a waiting ask's own button" in line for line in self.said) >= 1)
        self.assertFalse(answer.exists())

    def test_each_ask_is_sent_once(self) -> None:
        first = self.ask()
        self.relay.publish = lambda message: self.said.append("sent")  # type: ignore[method-assign]
        self.relay.step()
        self.relay.step()
        self.assertEqual(self.said.count("sent"), 1)
        self.assertIn(first, self.relay.tokens)

    def test_only_https_or_this_computer(self) -> None:
        with self.assertRaisesRegex(AskRelayError, "https"):
            Relay(self.folder, "http://example.com", "aryan")

    def test_a_reply_is_three_words(self) -> None:
        self.assertEqual(read_reply("ab12 yes tok"), ("ab12", "yes", "tok"))
        self.assertIsNone(read_reply("ab12 maybe tok"))
        self.assertIsNone(read_reply("yes"))
        long = notification("https://x", {"asks": "a", "replies": "r"}, {"id": "i", "says": "z" * 5000}, "t")
        self.assertLess(len(long["message"]), 1100)


if __name__ == "__main__":
    unittest.main()
