"""The streams the CLI prints through.

A console cannot show every character, so a stand-in is shown rather than
crashing. A stream that is not a console — a page, a file, a pipe — has no
such excuse, and is written as UTF-8 so a character such as a Greek alpha or
a subscript survives instead of becoming a question mark.
"""

from __future__ import annotations

import sys
import unittest
from unittest import mock

from kosika import cli


class FakeStream:
    """A stream that records how it was reconfigured."""

    def __init__(self, tty: bool) -> None:
        self.tty = tty
        self.calls: list[dict[str, object]] = []

    def isatty(self) -> bool:
        return self.tty

    def reconfigure(self, **options: object) -> None:
        self.calls.append(options)


class ConsoleEncodingTests(unittest.TestCase):
    def test_a_stream_that_is_not_a_console_is_written_as_utf8(self) -> None:
        out, err = FakeStream(False), FakeStream(False)
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            cli.configure_console()
        self.assertEqual(out.calls, [{"encoding": "utf-8", "errors": "replace"}])
        self.assertEqual(err.calls, [{"encoding": "utf-8", "errors": "replace"}])

    def test_a_console_keeps_its_own_encoding(self) -> None:
        # Its code page is what the person at it is reading, so only the
        # error policy is set.
        out = FakeStream(True)
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", FakeStream(True)):
            cli.configure_console()
        self.assertEqual(out.calls, [{"errors": "replace"}])

    def test_a_stream_that_cannot_reconfigure_is_left_alone(self) -> None:
        class Bare:
            def isatty(self) -> bool:
                return False

        with mock.patch.object(sys, "stdout", Bare()), mock.patch.object(sys, "stderr", Bare()):
            cli.configure_console()


if __name__ == "__main__":
    unittest.main()
