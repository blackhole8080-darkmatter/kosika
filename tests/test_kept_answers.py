"""A kept answer is only given again while it is still an answer.

The learning router remembers a reply by the request it answered and hands it
back with no model asked. That is only safe while the edit it gives still
fits the code the request shows, and while no run has said the answer did not
pass. Without the second, one run wrongly judged as passing replays the same
wrong patch for ever and the request is marked done on it.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples" / "virtual_models"))

import smarter  # noqa: E402 - found beside the router it belongs to

SHOWN = "def f():\n    return 1\n\n\ndef g():\n    return 0\n"
ANSWER = (
    "Here it is.\n\n"
    "<<<<<<< FIND\n"
    "def f():\n"
    "    return 1\n"
    "=======\n"
    "def f():\n"
    "    return 2\n"
    ">>>>>>> REPLACE\n"
)


#: What the coder's prompt says, so that the router reads it as an edit: the
#: FIND/REPLACE format is what makes an answer checkable.
def body(shown: str = SHOWN, ask: str = "Make f return 2. Answer with a FIND block and a REPLACE block.") -> dict:
    return {"messages": [{"role": "user", "content": ask + "\n\n```python\n" + shown + "```"}]}


def reply(text: str = ANSWER) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class KeptAnswerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "kept.jsonl"

    def kept(self, text: str = ANSWER, shown: str = SHOWN) -> smarter.Kept:
        """A cache holding one answer that a run said had passed."""

        cache = smarter.Kept(self.path)
        cache.heard(body(shown), reply(text), "test-model")
        self.assertEqual(cache.passed(0), 1)
        return cache

    def test_an_answer_that_passed_is_given_again_with_no_model(self) -> None:
        cache = self.kept()
        given = cache.find(body())
        self.assertIsNotNone(given)
        self.assertIn("return 2", smarter.content(given))

    def test_a_kept_answer_survives_a_restart(self) -> None:
        self.kept()
        self.assertIsNotNone(smarter.Kept(self.path).find(body()))

    def test_an_answer_whose_edit_no_longer_fits_is_not_given(self) -> None:
        cache = self.kept()
        # The same request, but the code has moved on underneath it.
        moved = body(shown="def f():\n    return 7\n")
        self.assertIsNone(cache.find(moved))
        # And it is gone for good, not merely skipped this once.
        self.assertIsNone(smarter.Kept(self.path).find(moved))

    def test_an_answer_that_does_not_apply_is_not_kept_at_all(self) -> None:
        cache = smarter.Kept(self.path)
        cache.heard(body(), reply("<<<<<<< FIND\nnot in the code\n=======\nx\n>>>>>>> REPLACE\n"), "m")
        self.assertEqual(cache.passed(0), 0)
        self.assertIsNone(cache.find(body()))

    def test_a_failed_run_takes_back_the_answer_it_was_handed(self) -> None:
        cache = self.kept()
        self.assertIsNotNone(cache.find(body()))  # the run was given it
        self.assertEqual(cache.refused(0), 1)
        self.assertIsNone(cache.find(body()))
        self.assertIsNone(smarter.Kept(self.path).find(body()))

    def test_a_failed_run_leaves_an_answer_it_never_used_alone(self) -> None:
        cache = self.kept()
        # Another request fails: the answer to this one was not relied on.
        cache.heard(body(ask="Something else."), reply(), "m")
        self.assertEqual(cache.refused(0), 0)
        self.assertIsNotNone(cache.find(body()))


if __name__ == "__main__":
    unittest.main()
