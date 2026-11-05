"""Notes sent to the one address the person named."""

from __future__ import annotations

import unittest
from urllib.parse import unquote

from kosika.notify import MAX_NOTE_BYTES, MAX_PAGE_BYTES, NotifyError, bind_notify_tool, clean_address
from kosika.parser import parse
from kosika.system_tools import SystemToolError, bind_system_tools

PROGRAM = (
    "record Note { title: Text text: Text link: Text file_name: Text file: Text }\n"
    "record Sent { sent: Bool }\n"
    'tool tell_me {\n  source system "notify"\n  operation: "send"\n'
    "  access: observe\n  input: Note\n  output: Sent\n}\n"
)


def recorder(sent: list[tuple[str, dict[str, str], bytes]]):
    """A sender that records the request and answers 200."""

    return lambda url, headers, body: sent.append((url, headers, body)) or 200


def note(**fields: str) -> dict[str, str]:
    plain = {"title": "t", "text": "x", "link": "", "file_name": "", "file": ""}
    return {**plain, **fields}


class NotifyTests(unittest.TestCase):
    def test_only_one_plain_https_address_is_taken(self) -> None:
        self.assertEqual(clean_address(" https://ntfy.sh/a-topic "), "https://ntfy.sh/a-topic")
        for bad in ("http://ntfy.sh/a", "https://ntfy.sh/", "https://u:p@ntfy.sh/a", "https://ntfy.sh/a?x=1", "ntfy.sh/a"):
            with self.subTest(bad), self.assertRaises(NotifyError):
                clean_address(bad)

    def test_a_note_goes_to_that_address_and_a_long_one_is_cut(self) -> None:
        sent: list[tuple[str, dict[str, str], bytes]] = []
        send = bind_notify_tool("https://ntfy.sh/mine", recorder(sent))
        self.assertEqual(send(None, note(title="Digest\n today", text="hello")), {"sent": True})  # type: ignore[arg-type]
        self.assertEqual(sent[0][0], "https://ntfy.sh/mine")
        self.assertEqual(sent[0][1]["Title"], "Digest today")
        self.assertEqual(sent[0][2], b"hello")
        send(None, note(text="x" * 10_000))  # type: ignore[arg-type]
        self.assertLessEqual(len(sent[1][2]), MAX_NOTE_BYTES)
        self.assertTrue(sent[1][2].endswith(b"[cut: the rest is on the computer]"))

    def test_a_link_rides_as_click_and_a_bad_one_is_refused(self) -> None:
        sent: list[tuple[str, dict[str, str], bytes]] = []
        send = bind_notify_tool("https://ntfy.sh/mine", recorder(sent))
        send(None, note(text="read this", link="https://arxiv.org/abs/2610.23850"))  # type: ignore[arg-type]
        self.assertEqual(sent[0][1]["Click"], "https://arxiv.org/abs/2610.23850")
        send(None, note(text="no link"))  # type: ignore[arg-type]
        self.assertNotIn("Click", sent[1][1])
        for bad in ("javascript:alert(1)", "not a url at all", "https://u:p@x.com/a", "x" * 3000):
            with self.subTest(bad), self.assertRaises(NotifyError):
                send(None, note(link=bad))  # type: ignore[arg-type]

    def test_a_page_rides_as_the_body_and_the_words_as_the_message(self) -> None:
        sent: list[tuple[str, dict[str, str], bytes]] = []
        send = bind_notify_tool("https://ntfy.sh/mine", recorder(sent))
        send(None, note(title="A paper", text="Drew it.\nSee the page.", link="https://arxiv.org/abs/1", file_name="a/b page.html", file="<html>the page</html>"))  # type: ignore[arg-type]
        url, headers, body = sent[0]
        self.assertEqual(headers["Title"], "A paper")
        self.assertEqual(headers["Filename"], "a-b-page.html")
        self.assertEqual(headers["Click"], "https://arxiv.org/abs/1")
        self.assertEqual(body, b"<html>the page</html>")
        # The words of the note travel as the message parameter, with each
        # written \n standing for a line, as ntfy reads it.
        self.assertTrue(url.startswith("https://ntfy.sh/mine?message="))
        message = unquote(url.split("message=", 1)[1])
        self.assertEqual(message, "Drew it.\\nSee the page.")

    def test_a_file_without_a_name_is_refused_and_vice_versa(self) -> None:
        send = bind_notify_tool("https://ntfy.sh/mine", lambda *_: 200)
        with self.assertRaises(NotifyError):
            send(None, note(file="<html>"))  # type: ignore[arg-type]
        with self.assertRaises(NotifyError):
            send(None, note(file_name="page.html"))  # type: ignore[arg-type]
        with self.assertRaises(NotifyError):
            send(None, note(file_name="  ", file="<html>"))  # type: ignore[arg-type]

    def test_a_page_too_big_to_attach_still_sends_the_note(self) -> None:
        sent: list[tuple[str, dict[str, str], bytes]] = []
        send = bind_notify_tool("https://ntfy.sh/mine", recorder(sent))
        send(None, note(text="the digest", file_name="big.html", file="x" * (MAX_PAGE_BYTES + 1)))  # type: ignore[arg-type]
        url, headers, body = sent[0]
        self.assertNotIn("Filename", headers)
        self.assertEqual(url, "https://ntfy.sh/mine")
        self.assertIn(b"[the page was too big to attach]", body)

    def test_a_refused_note_fails_the_step(self) -> None:
        send = bind_notify_tool("https://ntfy.sh/mine", lambda *_: 429)
        with self.assertRaises(NotifyError):
            send(None, note())  # type: ignore[arg-type]

    def test_the_tool_is_bound_only_when_an_address_is_named(self) -> None:
        program = parse(PROGRAM)
        self.assertNotIn("tell_me", bind_system_tools(program, folder=None))
        self.assertIn("tell_me", bind_system_tools(program, folder=None, notify="https://ntfy.sh/mine"))
        with self.assertRaises(SystemToolError):
            bind_system_tools(program, folder=None, notify="http://ntfy.sh/mine")


if __name__ == "__main__":
    unittest.main()
