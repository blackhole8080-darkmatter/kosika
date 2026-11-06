"""The grounding rule, on the shapes of answer this project really records."""

import unittest
from pathlib import Path

from examples.grounding.check import check

NOTES = Path(__file__).parents[1] / "benchmarks" / "researcher" / "notes"
RENT = "Rent is 900 euros a month, due on the 1st."


class GroundingRuleTests(unittest.TestCase):
    def test_a_quote_prefixed_with_its_source_still_counts_as_verbatim(self) -> None:
        said = (
            'KX-22917\nQuote: "home.md:5: Car insurance renews in March; '
            'the policy number is KX-22917."\nFrom: home.md, line 5'
        )
        verdict = check(said, NOTES)
        self.assertTrue(verdict.grounded, verdict.reason)

    def test_an_answer_that_says_more_than_the_line_says_is_refused(self) -> None:
        said = (
            "Your manager is Dana Whitfield.\n"
            f'Quote: "{RENT}"\nFrom: home.md, line 3'
        )
        verdict = check(said, NOTES)
        self.assertFalse(verdict.grounded)
        self.assertIn("manager", verdict.reason)

    def test_a_quote_that_is_not_on_the_cited_line_is_refused(self) -> None:
        said = (
            "Rent is 950 euros.\n"
            'Quote: "Rent is 950 euros a month."\nFrom: home.md, line 3'
        )
        verdict = check(said, NOTES)
        self.assertFalse(verdict.grounded)
        self.assertIn("quote", verdict.reason)

    def test_a_number_must_match_exactly(self) -> None:
        said = (
            "The rent is 90 euros.\n"
            f'Quote: "{RENT}"\nFrom: home.md, line 3'
        )
        verdict = check(said, NOTES)
        self.assertFalse(verdict.grounded)
        self.assertIn("90", verdict.reason)

    def test_an_answer_that_cites_nothing_is_refused(self) -> None:
        verdict = check("The rent is 900 euros a month.", NOTES)
        self.assertFalse(verdict.grounded)
        self.assertIn("cites nothing", verdict.reason)


if __name__ == "__main__":
    unittest.main()
