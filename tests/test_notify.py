"""Notes sent to the one address the person named."""

from __future__ import annotations

import unittest

from kosika.notify import MAX_NOTE_BYTES, NotifyError, bind_notify_tool, clean_address
from kosika.parser import parse
from kosika.system_tools import SystemToolError, bind_system_tools

PROGRAM = (
    "record Note { title: Text text: Text }\nrecord Sent { sent: Bool }\n"
    'tool tell_me {\n  source system "notify"\n  operation: "send"\n'
    "  access: observe\n  input: Note\n  output: Sent\n}\n"
)


class NotifyTests(unittest.TestCase):
    def test_only_one_plain_https_address_is_taken(self) -> None:
        self.assertEqual(clean_address(" https://ntfy.sh/a-topic "), "https://ntfy.sh/a-topic")
        for bad in ("http://ntfy.sh/a", "https://ntfy.sh/", "https://u:p@ntfy.sh/a", "https://ntfy.sh/a?x=1", "ntfy.sh/a"):
            with self.subTest(bad), self.assertRaises(NotifyError):
                clean_address(bad)

    def test_a_note_goes_to_that_address_and_a_long_one_is_cut(self) -> None:
        sent: list[tuple[str, str, bytes]] = []
        send = bind_notify_tool("https://ntfy.sh/mine", lambda url, title, body: sent.append((url, title, body)) or 200)
        self.assertEqual(send(None, {"title": "Digest\n today", "text": "hello"}), {"sent": True})  # type: ignore[arg-type]
        self.assertEqual(sent[0], ("https://ntfy.sh/mine", "Digest today", b"hello"))
        send(None, {"title": "t", "text": "x" * 10_000})  # type: ignore[arg-type]
        self.assertLessEqual(len(sent[1][2]), MAX_NOTE_BYTES)
        self.assertTrue(sent[1][2].endswith(b"[cut: the rest is on the computer]"))

    def test_a_refused_note_fails_the_step(self) -> None:
        send = bind_notify_tool("https://ntfy.sh/mine", lambda *_: 429)
        with self.assertRaises(NotifyError):
            send(None, {"title": "t", "text": "x"})  # type: ignore[arg-type]

    def test_the_tool_is_bound_only_when_an_address_is_named(self) -> None:
        program = parse(PROGRAM)
        self.assertNotIn("tell_me", bind_system_tools(program, folder=None))
        self.assertIn("tell_me", bind_system_tools(program, folder=None, notify="https://ntfy.sh/mine"))
        with self.assertRaises(SystemToolError):
            bind_system_tools(program, folder=None, notify="http://ntfy.sh/mine")


if __name__ == "__main__":
    unittest.main()
