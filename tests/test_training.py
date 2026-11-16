from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from kosika.cli import main
from kosika.model import ChatMessage, ModelRequest, ModelResponse, ScriptedModelProvider
from kosika.training import (
    TrainingConfig,
    TrainingError,
    TrainingPair,
    build_training_set,
    evaluate_lexical,
    evaluate_ranker,
    load_ranker,
    split_training_set,
    train_ranker,
)
from kosika.virtual_model import VirtualModelError, VirtualModelProvider

ROOT = Path(__file__).parents[1]

#: Three clearly different topics, so a passage says what it is about and a
#: trained ranker can learn the difference from two cited examples.
GUIDE = "\n".join(
    [
        "Kosika workflows have budgets for model calls and tool calls.",
        "An execution budget limits model calls.",
        "The checker validates declared model inputs and outputs.",
        "",
        *["A budget stops a workflow that would otherwise keep asking a model."] * 18,
        "",
        *["Tool grants decide which tools an agent may call."] * 22,
        "",
        *["A trace records every step so an execution can be replayed."] * 24,
        "",
    ]
)


def write_profile(root: Path, **overrides: object) -> Path:
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    (docs / "guide.md").write_text(GUIDE, encoding="utf-8")
    payload: dict[str, object] = {
        "schema": "kosika.virtual-model/v1",
        "name": "kosika-expert",
        "instructions": "Focus on the language specification.",
        "sources": ["docs"],
        "verified_examples": [
            {
                "task": "How are model calls bounded?",
                "answer": "By an execution budget.",
                "verified": True,
                "evidence": [
                    {
                        "source": "docs/guide.md",
                        "line": 2,
                        "quote": "An execution budget limits model calls.",
                    }
                ],
            },
            {
                "task": "Which tools may an agent call?",
                "answer": "Only the tools it was granted.",
                "verified": True,
                "evidence": [
                    {
                        "source": "docs/guide.md",
                        "line": 24,
                        "quote": "Tool grants decide which tools an agent may call.",
                    }
                ],
            },
            {
                "task": "What can be replayed after a run?",
                "answer": "Every recorded step.",
                "verified": True,
                "evidence": [
                    {
                        "source": "docs/guide.md",
                        "line": 47,
                        "quote": (
                            "A trace records every step so an execution can be "
                            "replayed."
                        ),
                    }
                ],
            },
        ],
        "max_context_chars": 2_000,
        "max_results": 3,
    }
    payload.update(overrides)
    profile = root / "specialist.json"
    profile.write_text(json.dumps(payload), encoding="utf-8")
    return profile


def small_config(**overrides: object) -> TrainingConfig:
    settings: dict[str, object] = {
        "epochs": 60,
        "batch_size": 3,
        "learning_rate": 0.1,
        "hidden": 8,
        "feature_dim": 128,
        "negative_ratio": 2,
        "seed": 7,
    }
    settings.update(overrides)
    return TrainingConfig(**settings)  # type: ignore[arg-type]


class TrainingTests(unittest.TestCase):
    def test_pairs_come_only_from_verified_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            training = build_training_set(profile, small_config())

            self.assertEqual(training.name, "kosika-expert")
            self.assertGreaterEqual(training.positives, 2)
            self.assertGreaterEqual(training.negatives, 2)
            cited = {pair.text for pair in training.pairs if pair.label == 1}
            self.assertTrue(
                any("budgets for model calls" in text for text in cited)
            )
            self.assertTrue(
                any("Tool grants decide" in text for text in cited)
            )
            for pair in training.pairs:
                self.assertIn(pair.label, (0, 1))
                self.assertTrue(pair.query)
                self.assertTrue(pair.text)

            write_profile(
                root,
                verified_examples=[],
            )
            with self.assertRaisesRegex(TrainingError, "verified examples"):
                build_training_set(profile, small_config())

    def test_training_lowers_loss_and_separates_cited_passages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            config = small_config()
            training = build_training_set(profile, config)
            model = train_ranker(training, config)

            self.assertLess(model.metrics["loss"], model.metrics["initial_loss"])
            self.assertGreater(model.metrics["accuracy"], 0.8)
            self.assertGreater(
                model.metrics["mean_positive"], model.metrics["mean_negative"]
            )
            self.assertEqual(len(model.history), config.epochs)
            self.assertLess(model.history[-1], model.history[0])
            cited = model.score(
                "How are model calls bounded?",
                "An execution budget limits model calls.",
            )
            uncited = model.score(
                "How are model calls bounded?",
                "A trace records every step so an execution can be replayed.",
            )
            self.assertGreater(cited, uncited)
            # Every weight was learned, not left at its initial value.
            self.assertTrue(
                any(value != 0.0 for row in model.w1 for value in row)
            )

    def test_same_seed_trains_the_same_weights(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            first = train_ranker(build_training_set(profile, small_config()))
            second = train_ranker(build_training_set(profile, small_config()))
            other = train_ranker(
                build_training_set(profile, small_config(seed=11))
            )
            self.assertEqual(first.identity(), second.identity())
            self.assertNotEqual(first.identity(), other.identity())

    def test_checkpoint_roundtrip_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            config = small_config()
            model = train_ranker(build_training_set(profile, config))
            checkpoint = model.save(root / "ranker.json")

            loaded = load_ranker(checkpoint)
            self.assertEqual(loaded.identity(), model.identity())
            self.assertEqual(
                loaded.score("model calls", "An execution budget limits model calls."),
                model.score("model calls", "An execution budget limits model calls."),
            )
            self.assertEqual(loaded.metrics, model.metrics)

            payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            payload["weights"]["w1"][0][0] = payload["weights"]["w1"][0][0] + 0.5
            edited = root / "edited.json"
            edited.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(TrainingError, "edited after training"):
                load_ranker(edited)

            payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            payload["extra"] = 1
            extra = root / "extra.json"
            extra.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(TrainingError, "exactly"):
                load_ranker(extra)

    def test_cli_trains_and_writes_the_checked_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            output = root / "ranker.json"
            out, err = io.StringIO(), io.StringIO()
            with (
                redirect_stdout(out),
                redirect_stderr(err),
                patch(
                    "sys.argv",
                    [
                        "kosika",
                        "train",
                        "ranker",
                        str(profile),
                        "--output",
                        str(output),
                        "--epochs",
                        "20",
                        "--hidden",
                        "8",
                        "--feature-dim",
                        "128",
                    ],
                ),
            ):
                result = main()
            self.assertEqual(result, 0, err.getvalue())
            self.assertTrue(output.is_file())
            printed = out.getvalue()
            self.assertIn("trained kosika-expert", printed)
            self.assertIn("accuracy", printed)
            self.assertIn("ranker", printed)
            self.assertIsNotNone(load_ranker(output))

    def test_cli_refuses_a_profile_without_verified_examples(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root, verified_examples=[])
            out, err = io.StringIO(), io.StringIO()
            with (
                redirect_stdout(out),
                redirect_stderr(err),
                patch(
                    "sys.argv",
                    [
                        "kosika",
                        "train",
                        "ranker",
                        str(profile),
                        "--output",
                        str(root / "ranker.json"),
                    ],
                ),
            ):
                result = main()
            self.assertEqual(result, 2)
            self.assertIn("Training failed safely", err.getvalue())
            self.assertFalse((root / "ranker.json").exists())


class RankedVirtualModelTests(unittest.TestCase):
    def test_profile_loads_trained_weights_and_ranks_with_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            config = small_config()
            model = train_ranker(build_training_set(profile, config), config)
            checkpoint = model.save(root / "ranker.json")

            write_profile(root, ranker=checkpoint.name)
            provider = ScriptedModelProvider([ModelResponse(content="Answer")])
            ranked = VirtualModelProvider(provider, profile)
            self.assertIsNotNone(ranked.ranker)
            unranked = VirtualModelProvider(
                ScriptedModelProvider([ModelResponse(content="Answer")]),
                write_profile(root),
            )
            self.assertNotEqual(ranked.plan_identity, unranked.plan_identity)

            ranked.complete(
                ModelRequest(
                    messages=(
                        ChatMessage(
                            role="user", content="How are model calls bounded?"
                        ),
                    )
                )
            )
            context = provider.requests[0].messages[0].content
            self.assertIn("docs/guide.md:1", context)
            self.assertIn("execution budget", context)

    def test_a_broken_checkpoint_is_reported_rather_than_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root, ranker="missing.json")
            with self.assertRaisesRegex(VirtualModelError, "not usable"):
                VirtualModelProvider(
                    ScriptedModelProvider([ModelResponse(content="unused")]),
                    profile,
                )

            (root / "bad.json").write_text("{", encoding="utf-8")
            profile = write_profile(root, ranker="bad.json")
            with self.assertRaisesRegex(VirtualModelError, "not usable"):
                VirtualModelProvider(
                    ScriptedModelProvider([ModelResponse(content="unused")]),
                    profile,
                )

    def test_ranker_field_must_be_a_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root, ranker=7)
            with self.assertRaisesRegex(VirtualModelError, "checkpoint path"):
                VirtualModelProvider(
                    ScriptedModelProvider([ModelResponse(content="unused")]),
                    profile,
                )


class TrainingGateTests(unittest.TestCase):
    def run_eval(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(out),
            redirect_stderr(err),
            patch("sys.argv", argv),
        ):
            result = main()
        return result, out.getvalue(), err.getvalue()

    def test_holdout_split_is_disjoint_and_repeatable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            config = small_config()
            training = build_training_set(profile, config)

            fit, held_out = split_training_set(training, 1, config.seed)
            again_fit, again_held = split_training_set(training, 1, config.seed)
            self.assertEqual(fit.pairs, again_fit.pairs)
            self.assertEqual(held_out.pairs, again_held.pairs)
            self.assertEqual(
                len(fit.pairs) + len(held_out.pairs), len(training.pairs)
            )
            self.assertGreater(len(fit.pairs), 0)
            self.assertGreater(len(held_out.pairs), 0)
            self.assertTrue(any(pair.label == 1 for pair in held_out.pairs))
            self.assertTrue(any(pair.label == 0 for pair in held_out.pairs))
            self.assertFalse(
                {pair.example for pair in fit.pairs}
                & {pair.example for pair in held_out.pairs}
            )
            with self.assertRaisesRegex(TrainingError, "would leave"):
                split_training_set(training, 9, config.seed)

    def test_holdout_that_leaves_one_example_is_refused_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            config = small_config()
            training = build_training_set(profile, config)

            with self.assertRaisesRegex(TrainingError, "at least 2 is required"):
                split_training_set(training, 2, config.seed)
            fit, held_out = split_training_set(
                training, 2, config.seed, min_examples=1
            )
            self.assertEqual(len({pair.example for pair in fit.pairs}), 1)
            self.assertEqual(len({pair.example for pair in held_out.pairs}), 2)

    def test_cli_refuses_a_holdout_that_leaves_one_example(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            result, _, error = self.run_eval(
                [
                    "kosika",
                    "train",
                    "eval",
                    str(profile),
                    "--holdout",
                    "2",
                ]
            )
            self.assertEqual(result, 2)
            self.assertIn("Evaluation failed safely", error)
            self.assertIn("at least 2 is required", error)

    def test_evaluation_counts_every_pair_and_stays_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            config = small_config()
            training = build_training_set(profile, config)
            model = train_ranker(training, config)

            scored = evaluate_ranker(model, training.pairs)
            self.assertEqual(int(scored["pairs"]), len(training.pairs))
            self.assertEqual(int(scored["positives"]), training.positives)
            self.assertEqual(int(scored["negatives"]), training.negatives)
            self.assertTrue(0.0 <= scored["accuracy"] <= 1.0)
            with self.assertRaisesRegex(TrainingError, "at least one pair"):
                evaluate_ranker(model, [])

    def test_gate_passes_and_keeps_the_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            output = root / "ranker.json"
            result, printed, error = self.run_eval(
                [
                    "kosika",
                    "train",
                    "eval",
                    str(profile),
                    "--output",
                    str(output),
                    "--min-accuracy",
                    "0.0",
                    "--epochs",
                    "20",
                    "--hidden",
                    "8",
                    "--feature-dim",
                    "128",
                ]
            )
            self.assertEqual(result, 0, error)
            self.assertTrue(output.is_file())
            self.assertIn("held-out accuracy", printed)
            self.assertIn("lexical baseline", printed)
            self.assertIn("trained lift", printed)
            self.assertIn("gate passes", printed)
            self.assertIn("wrote", printed)
            self.assertIsNotNone(load_ranker(output))

    def test_gate_fails_and_keeps_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            output = root / "ranker.json"
            # Untrained weights answer "relevant" to everything, and positives
            # are a minority, so the score cannot reach a perfect threshold.
            result, printed, error = self.run_eval(
                [
                    "kosika",
                    "train",
                    "eval",
                    str(profile),
                    "--output",
                    str(output),
                    "--min-accuracy",
                    "1.0",
                    "--epochs",
                    "1",
                    "--learning-rate",
                    "1e-9",
                    "--hidden",
                    "8",
                    "--feature-dim",
                    "128",
                ]
            )
            self.assertEqual(result, 1, error)
            self.assertIn("gate fails", printed)
            self.assertIn("is not kept", printed)
            self.assertFalse(output.exists())

    def test_lexical_baseline_scores_the_old_rule_honestly(self) -> None:
        clean = (
            TrainingPair(
                "model call budgets", "budgets limit model calls", 1, "a:1"
            ),
            TrainingPair(
                "model call budgets", "a trace records every step", 0, "b:1"
            ),
        )
        self.assertEqual(evaluate_lexical(clean)["accuracy"], 1.0)

        misled = (
            TrainingPair(
                "how are model calls bounded",
                "a trace records every step",
                1,
                "a:1",
            ),
            TrainingPair(
                "how are model calls bounded",
                "model calls are recorded in traces",
                0,
                "b:1",
            ),
        )
        found = evaluate_lexical(misled)
        self.assertEqual(found["accuracy"], 0.0)
        self.assertEqual(int(found["pairs"]), 2)
        self.assertEqual(int(found["positives"]), 1)
        with self.assertRaisesRegex(TrainingError, "at least one pair"):
            evaluate_lexical([])

    def test_gate_json_reports_baseline_and_lift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            result, printed, error = self.run_eval(
                [
                    "kosika",
                    "train",
                    "eval",
                    str(profile),
                    "--min-accuracy",
                    "0.0",
                    "--epochs",
                    "20",
                    "--hidden",
                    "8",
                    "--feature-dim",
                    "128",
                    "--json",
                ]
            )
            self.assertEqual(result, 0, error)
            payload = json.loads(printed)
            self.assertEqual(payload["gate"], "pass")
            self.assertEqual(payload["min_train_examples"], 2)
            self.assertIn("lexical", payload)
            self.assertAlmostEqual(
                payload["lift"],
                payload["held_out"]["accuracy"] - payload["lexical"]["accuracy"],
            )

    def test_gate_refuses_an_impossible_holdout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = write_profile(root)
            result, _, error = self.run_eval(
                [
                    "kosika",
                    "train",
                    "eval",
                    str(profile),
                    "--holdout",
                    "9",
                ]
            )
            self.assertEqual(result, 2)
            self.assertIn("Evaluation failed safely", error)


if __name__ == "__main__":
    unittest.main()
