from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from kosika.agent import AgentLoop
from kosika.cli import main
from kosika.model import ChatMessage, ModelRequest, ModelResponse, ScriptedModelProvider
from kosika.parser import parse
from kosika.virtual_model import (
    VirtualModelError,
    VirtualModelProvider,
    accept_verified_example,
    review_example_candidate,
)

ROOT = Path(__file__).parents[1]


class VirtualModelTests(unittest.TestCase):
    def make_profile(self, root: Path, **overrides: object) -> Path:
        docs = root / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "guide.md").write_text(
            "Kosika workflows have budgets for model calls and tool calls.\n"
            "The checker validates declared model inputs and outputs.\n",
            encoding="utf-8",
        )
        payload: dict[str, object] = {
            "schema": "kosika.virtual-model/v1",
            "name": "kosika-expert",
            "instructions": "Focus on the language specification.",
            "sources": ["docs"],
            "verified_examples": [],
            "max_context_chars": 2_000,
            "max_results": 3,
        }
        payload.update(overrides)
        profile = root / "specialist.json"
        profile.write_text(json.dumps(payload), encoding="utf-8")
        return profile

    def test_retrieves_cited_local_knowledge_and_preserves_model_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = ScriptedModelProvider([ModelResponse(content="Answer")])
            specialist = VirtualModelProvider(provider, self.make_profile(root))
            request = ModelRequest(
                messages=(
                    ChatMessage(role="user", content="How are model calls bounded?"),
                ),
                model="backend-model",
                max_output_tokens=321,
                json_object=True,
            )
            response = specialist.complete(request)
            self.assertEqual(response.content, "Answer")
            sent = provider.requests[0]
            self.assertEqual(sent.model, "backend-model")
            self.assertEqual(sent.max_output_tokens, 321)
            self.assertTrue(sent.json_object)
            self.assertEqual(sent.messages[-1], request.messages[0])
            self.assertIn("[docs/guide.md:1; relevance", sent.messages[0].content)
            self.assertIn("model calls and tool calls", sent.messages[0].content)
            self.assertIn("untrusted reference data", sent.messages[0].content)

    def test_only_explicit_verified_examples_with_source_evidence_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            example = {
                "task": "Explain model call budgets",
                "answer": "They cap model invocations.",
                "verified": True,
                "evidence": [
                    {
                        "source": "docs/guide.md",
                        "line": 1,
                        "quote": "Kosika workflows have budgets",
                    }
                ],
            }
            provider = ScriptedModelProvider([ModelResponse(content="ok")])
            specialist = VirtualModelProvider(
                provider,
                self.make_profile(root, verified_examples=[example]),
            )
            specialist.complete(
                ModelRequest(
                    messages=(ChatMessage(role="user", content="model call budgets"),)
                )
            )
            self.assertIn(
                "Verified answer: They cap model invocations",
                provider.requests[0].messages[0].content,
            )

            example["verified"] = False
            with self.assertRaisesRegex(VirtualModelError, "verified=true"):
                VirtualModelProvider(
                    provider,
                    self.make_profile(root, verified_examples=[example]),
                )

    def test_rejects_unresolved_example_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            example = {
                "task": "Explain budgets",
                "answer": "They are bounded.",
                "verified": True,
                "evidence": [
                    {
                        "source": "docs/missing.md",
                        "line": 99,
                        "quote": "No such quote",
                    }
                ],
            }
            with self.assertRaisesRegex(VirtualModelError, "does not resolve"):
                VirtualModelProvider(
                    ScriptedModelProvider([ModelResponse(content="unused")]),
                    self.make_profile(root, verified_examples=[example]),
                )

    def test_context_is_bounded_and_direct_invalid_sources_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bad = root / "broken.kos"
            bad.write_text("this is not a Kosika declaration", encoding="utf-8")
            profile = self.make_profile(root, sources=["broken.kos"])
            with self.assertRaisesRegex(VirtualModelError, "does not check"):
                VirtualModelProvider(
                    ScriptedModelProvider([ModelResponse(content="unused")]), profile
                )

    def test_agent_uses_virtual_model_without_expanding_tool_grants(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = ScriptedModelProvider(
                [ModelResponse(content="Grounded answer.")]
            )
            specialist = VirtualModelProvider(provider, self.make_profile(root))
            program = parse(
                (ROOT / "examples" / "test_host.kos").read_text(encoding="utf-8")
            )
            result = AgentLoop(program, specialist, lambda tool, arguments: {}).run(
                "monitor", "Explain model call budgets."
            )
            sent = provider.requests[0]
            self.assertEqual(result.content, "Grounded answer.")
            self.assertEqual([tool.name for tool in sent.tools], ["check_disk"])
            self.assertIn("[docs/guide.md:1; relevance", sent.messages[0].content)
            loop = AgentLoop(program, specialist, lambda tool, arguments: {})
            changed = VirtualModelProvider(
                ScriptedModelProvider([ModelResponse(content="unused")]),
                self.make_profile(root, instructions="A changed instruction."),
            )
            changed_loop = AgentLoop(program, changed, lambda tool, arguments: {})
            self.assertNotEqual(
                loop.plan_hash_for("monitor", "Explain model call budgets."),
                changed_loop.plan_hash_for("monitor", "Explain model call budgets."),
            )

    def test_cli_runs_agent_through_virtual_model_with_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self.make_profile(root)
            fixture = root / "answers.json"
            fixture.write_text(
                json.dumps(
                    {"fixture-backend": [{"content": "Fixture specialist answer."}]}
                ),
                encoding="utf-8",
            )
            out, err = io.StringIO(), io.StringIO()
            with (
                redirect_stdout(out),
                redirect_stderr(err),
                patch(
                    "sys.argv",
                    [
                        "kosika",
                        "agent",
                        str(ROOT / "examples" / "test_host.kos"),
                        "--agent",
                        "monitor",
                        "--task",
                        "Explain model call budgets",
                        "--model",
                        "fixture-backend",
                        "--model-fixture",
                        str(fixture),
                        "--virtual-model",
                        str(profile),
                    ],
                ),
            ):
                result = main()
            self.assertEqual(result, 0, err.getvalue())
            self.assertIn("Fixture specialist answer.", out.getvalue())

    def test_accept_verified_example_atomically_writes_a_revalidated_candidate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self.make_profile(root)
            candidate = {
                "task": "Explain model call budgets",
                "answer": "They cap model invocations.",
                "evidence": [
                    {
                        "source": "docs/guide.md",
                        "line": 1,
                        "quote": "Kosika workflows have budgets",
                    }
                ],
            }
            example, digest, source_identity = review_example_candidate(
                profile, candidate
            )
            self.assertEqual(example.task, candidate["task"])
            self.assertEqual(
                accept_verified_example(
                    profile,
                    candidate,
                    expected_profile_digest=digest,
                    expected_source_identity=source_identity,
                ),
                1,
            )
            loaded = VirtualModelProvider(
                ScriptedModelProvider([ModelResponse(content="unused")]), profile
            )
            self.assertEqual(len(loaded.examples), 1)
            self.assertIn(
                "Verified answer: They cap model invocations",
                loaded._context(
                    ModelRequest(
                        messages=(
                            ChatMessage(role="user", content="model call budgets"),
                        )
                    )
                ),
            )
            self.assertFalse(profile.with_name(profile.name + ".lock").exists())
            self.assertEqual(list(root.glob(".specialist.json.*.tmp")), [])

    def test_verified_example_acceptance_rejects_stale_duplicate_and_locked_profiles(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self.make_profile(root)
            candidate = {
                "task": "Explain model call budgets",
                "answer": "They cap model invocations.",
                "evidence": [
                    {
                        "source": "docs/guide.md",
                        "line": 1,
                        "quote": "Kosika workflows have budgets",
                    }
                ],
            }
            _, digest, source_identity = review_example_candidate(profile, candidate)
            profile.write_text(
                profile.read_text(encoding="utf-8").replace(
                    "kosika-expert", "new-name"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(VirtualModelError, "changed during review"):
                accept_verified_example(
                    profile,
                    candidate,
                    expected_profile_digest=digest,
                    expected_source_identity=source_identity,
                )
            _, new_digest, source_identity = review_example_candidate(
                profile, candidate
            )
            accept_verified_example(
                profile,
                candidate,
                expected_profile_digest=new_digest,
                expected_source_identity=source_identity,
            )
            with self.assertRaisesRegex(VirtualModelError, "already exists"):
                review_example_candidate(profile, candidate)

            lock = profile.with_name(profile.name + ".lock")
            lock.write_text("another reviewer\n", encoding="ascii")
            _, current_digest, source_identity = review_example_candidate(
                profile,
                {
                    **candidate,
                    "task": "A distinct task",
                },
            )
            with self.assertRaisesRegex(VirtualModelError, "Another example review"):
                accept_verified_example(
                    profile,
                    {**candidate, "task": "A distinct task"},
                    expected_profile_digest=current_digest,
                    expected_source_identity=source_identity,
                )
            self.assertTrue(lock.exists())

    def test_source_change_between_review_and_acceptance_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self.make_profile(root)
            candidate = {
                "task": "Explain model call budgets",
                "answer": "They cap model invocations.",
                "evidence": [
                    {
                        "source": "docs/guide.md",
                        "line": 1,
                        "quote": "Kosika workflows have budgets",
                    }
                ],
            }
            _, digest, identity = review_example_candidate(profile, candidate)
            guide = root / "docs" / "guide.md"
            guide.write_text(
                guide.read_text(encoding="utf-8").replace("budgets", "limits"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(VirtualModelError, "sources changed"):
                accept_verified_example(
                    profile,
                    candidate,
                    expected_profile_digest=digest,
                    expected_source_identity=identity,
                )
            self.assertEqual(
                json.loads(profile.read_text(encoding="utf-8"))["verified_examples"],
                [],
            )

    def test_cli_review_requires_interaction_and_saves_only_after_yes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self.make_profile(root)
            candidate_path = root / "candidate.json"
            candidate_path.write_text(
                json.dumps(
                    {
                        "task": "Explain model call budgets",
                        "answer": "They cap model invocations.",
                        "evidence": [
                            {
                                "source": "docs/guide.md",
                                "line": 1,
                                "quote": "Kosika workflows have budgets",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            def run(*, tty: bool, answer: str | None) -> tuple[int, str, str]:
                out, err = io.StringIO(), io.StringIO()
                argv = [
                    "kosika",
                    "virtual-model",
                    "add-example",
                    str(profile),
                    str(candidate_path),
                ]
                with (
                    redirect_stdout(out),
                    redirect_stderr(err),
                    patch("sys.argv", argv),
                    patch("sys.stdin.isatty", return_value=tty),
                    patch("builtins.input", return_value=answer or ""),
                ):
                    result = main()
                return result, out.getvalue(), err.getvalue()

            original = profile.read_bytes()
            code, _, error = run(tty=False, answer="yes")
            self.assertEqual(code, 2)
            self.assertIn("interactive terminal", error)
            self.assertEqual(profile.read_bytes(), original)
            code, output, _ = run(tty=True, answer="no")
            self.assertEqual(code, 1)
            self.assertIn("not changed", output)
            self.assertEqual(profile.read_bytes(), original)
            code, output, error = run(tty=True, answer="yes")
            self.assertEqual(code, 0, error)
            self.assertIn("Accepted and saved", output)
            self.assertEqual(
                len(
                    json.loads(profile.read_text(encoding="utf-8"))["verified_examples"]
                ),
                1,
            )

    def test_plan_identity_changes_when_source_snapshot_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = self.make_profile(root)
            before = VirtualModelProvider(
                ScriptedModelProvider([ModelResponse(content="ok")]), profile
            ).plan_identity
            guide = root / "docs" / "guide.md"
            guide.write_text(
                guide.read_text(encoding="utf-8") + "New verified rule.\\n",
                encoding="utf-8",
            )
            after = VirtualModelProvider(
                ScriptedModelProvider([ModelResponse(content="ok")]), profile
            ).plan_identity
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
