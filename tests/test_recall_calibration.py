"""The paraphrase pairs and the threshold sweep that calibrates recall."""

import json
import tempfile
import unittest
from pathlib import Path

from kosika.lessons import CLOSE_ENOUGH, SEMANTIC_ENOUGH
from kosika.recall_calibration import (
    MEASURES,
    CalibrationError,
    Pair,
    breakdown,
    calibrate,
    closest,
    compare,
    containment,
    evaluate,
    load_pairs,
    reach,
    report_to_text,
    scored,
    separation,
    trigram_score,
    word_score,
)

PAIRS = (
    Path(__file__).resolve().parent.parent
    / "benchmarks"
    / "learning"
    / "paraphrases.json"
)


class PairFileTest(unittest.TestCase):
    def test_the_shipped_pairs_hold_both_kinds(self):
        pairs = load_pairs(PAIRS)
        self.assertGreaterEqual(len(pairs), 200)
        self.assertTrue(any(pair.same for pair in pairs))
        self.assertTrue(any(not pair.same for pair in pairs))

    def test_the_shipped_pairs_cover_every_kind(self):
        pairs = load_pairs(PAIRS)
        self.assertEqual(
            {pair.kind for pair in pairs},
            {"reword", "reorder", "near_miss", "far_miss"},
        )

    def test_another_schema_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pairs.json"
            path.write_text(json.dumps({"schema": "other"}), encoding="utf-8")
            with self.assertRaises(CalibrationError):
                load_pairs(path)

    def test_pairs_of_one_kind_only_are_refused(self):
        payload = {
            "schema": "kosika.paraphrase-pairs/v1",
            "families": [
                {
                    "id": "only_same",
                    "reference": "how do i pay",
                    "pairs": [
                        {"id": "a", "question": "how do i pay", "same": True}
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pairs.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(CalibrationError):
                load_pairs(path)


class KindTest(unittest.TestCase):
    def load(self, pairs):
        payload = {
            "schema": "kosika.paraphrase-pairs/v1",
            "families": [{"id": "f", "reference": "how do i pay", "pairs": pairs}],
        }
        folder = tempfile.TemporaryDirectory()
        path = Path(folder.name) / "pairs.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.addCleanup(folder.cleanup)
        return load_pairs(path)

    def test_a_kind_must_agree_with_whether_it_means_it(self):
        with self.assertRaises(CalibrationError):
            self.load(
                [{"id": "a", "question": "how do i pay", "same": False,
                  "kind": "reword"}]
            )

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(CalibrationError):
            self.load(
                [{"id": "a", "question": "how do i pay", "same": True,
                  "kind": "nearly"}]
            )

    def test_a_pair_without_a_kind_takes_one_from_same(self):
        pairs = self.load(
            [
                {"id": "a", "question": "how do i pay", "same": True},
                {"id": "b", "question": "how do i cancel", "same": False},
            ]
        )
        self.assertEqual([pair.kind for pair in pairs], ["reword", "near_miss"])


class WordScoreTest(unittest.TestCase):
    def test_the_same_words_score_one(self):
        self.assertEqual(
            word_score("How do I change my card?", "how do I change my card"),
            1.0,
        )

    def test_no_shared_word_scores_zero(self):
        self.assertEqual(
            word_score("How do I change my card?", "parcel trip delayed"), 0.0
        )


class SweepTest(unittest.TestCase):
    PAIRS = (
        Pair("near", "f", "how do i change my card", "how do i change my card", True),
        Pair(
            "far",
            "f",
            "how do i change my card",
            "how do i change my address",
            False,
        ),
    )

    def test_a_high_threshold_answers_only_the_same_words(self):
        row = evaluate(self.PAIRS, word_score, 0.8)
        self.assertEqual((row["true_positive"], row["false_positive"]), (1, 0))
        self.assertEqual(row["precision"], 1.0)
        self.assertEqual(row["recall"], 1.0)

    def test_a_low_threshold_reaches_the_near_miss_too(self):
        row = evaluate(self.PAIRS, word_score, 0.7)
        self.assertEqual(row["false_positive"], 1)
        self.assertLess(row["precision"], 1.0)

    def test_the_report_names_the_shipped_threshold_and_a_best(self):
        report = calibrate(PAIRS)
        self.assertEqual(report["scorer"], "words")
        self.assertEqual(report["shipped_threshold"], CLOSE_ENOUGH)
        self.assertEqual(report["shipped"]["threshold"], CLOSE_ENOUGH)
        self.assertGreater(len(report["curve"]), 10)
        self.assertGreaterEqual(report["best"]["f1"], report["shipped"]["f1"])
        self.assertIn("threshold", report_to_text(report))


class CapabilityTest(unittest.TestCase):
    ROWS = (
        Pair("hit", "f", "a b c", "a b c", True, "reword"),
        Pair("reorder", "f", "a b c", "c b a", True, "reorder"),
        Pair("near", "f", "a b c", "a b d", False, "near_miss"),
    )

    def test_separation_reports_where_the_scores_overlap(self):
        held = separation(scored(self.ROWS, word_score))
        self.assertEqual(held["same_lowest"], 1.0)
        self.assertEqual(held["same_median"], 1.0)
        self.assertEqual(held["other_highest"], 0.5)
        self.assertEqual(held["crossed"], 0)

    def test_a_separation_gap_shows_as_a_crossing_when_overlapped(self):
        rows = scored(
            (
                Pair("hit", "f", "a b", "a b", True),
                Pair("miss", "f", "a b", "a b e", False),
            ),
            word_score,
        )
        held = separation(rows)
        self.assertEqual(held["other_highest"], round(2 / 3, 3))
        self.assertEqual(held["crossed"], 0)

    def test_the_breakdown_counts_each_kind(self):
        table = {row["kind"]: row for row in breakdown(scored(self.ROWS, word_score), 1.0)}
        self.assertEqual(table["reword"]["right"], 1)
        self.assertEqual(table["reorder"]["right"], 1)
        self.assertEqual(table["near_miss"]["reached"], 0)

    def test_reach_takes_the_most_at_or_above_the_bar(self):
        curve = [
            {"threshold": 0.9, "precision": 1.0, "recall": 0.2, "true_positive": 2},
            {"threshold": 0.8, "precision": 0.6, "recall": 0.9, "true_positive": 9},
            {"threshold": 0.7, "precision": 0.3, "recall": 1.0, "true_positive": 10},
        ]
        self.assertEqual(reach(curve, 0.6)["right"], 9)
        self.assertEqual(reach(curve, 0.95)["right"], 2)
        self.assertEqual(reach(curve, 0.99)["right"], 2)
        nothing = [{"threshold": 0.7, "precision": 0.5, "recall": 0.1,
                    "true_positive": 1}]
        self.assertIsNone(reach(nothing, 0.9)["threshold"])

    def test_closest_names_the_narrowest_hit_and_miss(self):
        rows = scored(self.ROWS, word_score)
        held = closest(rows, 1)
        self.assertEqual(held["hits"][0]["id"], "hit")
        self.assertEqual(held["misses"][0]["id"], "near")


class MeasureTest(unittest.TestCase):
    def test_every_lexical_measure_scores_the_same_words_one(self):
        for name, measure in MEASURES.items():
            with self.subTest(measure=name):
                self.assertEqual(measure("how do i change my card", "how do i change my card"), 1.0)

    def test_a_letter_level_measure_sees_the_stem_the_words_miss(self):
        self.assertEqual(word_score("change my card", "changing my cards"), 0.2)
        self.assertGreater(trigram_score("change my card", "changing my cards"), 0.5)

    def test_containment_sees_a_short_question_inside_a_long_reference(self):
        self.assertGreater(containment("how do i change my card", "change card"), 0.9)

    def test_the_comparison_holds_every_measure_with_a_best(self):
        table = compare(load_pairs(PAIRS))
        self.assertEqual([row["measure"] for row in table], list(MEASURES))
        for row in table:
            self.assertGreaterEqual(row["f1"], 0.0)
            self.assertGreaterEqual(row["right"], row["right_at_bar"])
        names = {row["measure"]: row for row in table}
        shipped = names["words (jaccard)"]
        self.assertEqual(shipped["f1"], calibrate(PAIRS)["best"]["f1"])


class MeaningGrantTest(unittest.TestCase):
    class Near:
        def nearest(self, question, texts):
            return [(0.9, 0)]

    def test_a_grant_is_measured_by_cosine(self):
        report = calibrate(PAIRS, meaning=self.Near())
        self.assertEqual(report["scorer"], "meaning")
        self.assertEqual(report["shipped_threshold"], SEMANTIC_ENOUGH)
        self.assertEqual(report["shipped"]["threshold"], SEMANTIC_ENOUGH)
