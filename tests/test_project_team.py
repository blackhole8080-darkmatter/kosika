"""Consumer regressions for the read-only, evidence-backed project team."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from kosika.memory import bind_memory_tools
from kosika.model import (
    ModelProviderError,
    ModelResponse,
    OpenAICompatibleProvider,
    ScriptedModelProvider,
)
from kosika.modules import load_program
from kosika.runtime import run
from kosika.story import read_trace
from kosika.system_tools import SystemToolError, bind_system_tools

ROOT = Path(__file__).resolve().parents[1]
TEAM = ROOT / "examples" / "self_improve" / "team.kos"
MODELS = ("researcher_model", "planner_model", "reviewer_model")
AUTHORS = ["researcher", "planner", "reviewer"]
POLICY = "Policy requires a human review before accepting project changes."
GUIDE = "The project guide recommends small, evidence-backed review proposals."
ADVICE = {
    "explanation": "The current policy requires human review of proposed changes.",
    "steps": [
        {
            "action": "Propose a human review checkpoint for each project change.",
            "check": "Attempt a project change without human approval in an isolated fixture.",
            "expected": "The change remains pending and no project file is written.",
            "source": "policy.md:1",
        }
    ],
}
APPROVAL = {
    "assessment": "The answer covers the review policy, matches the evidence, and proposes a reproducible pending-change check.",
    "verdict": "accept",
}


def all_events(events: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for event in events:
        yield event
        yield from all_events(event.get("events", []))


class ProjectTeamTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.project = self.directory / "project"
        self.project.mkdir()
        self.memory = self.directory / "memory"
        (self.project / "policy.md").write_text(POLICY, encoding="utf-8")
        (self.project / "guide.md").write_text(GUIDE, encoding="utf-8")
        self.request = {
            "project": "review-project",
            "question": "How should this project review policy changes?",
            "focus": "policy.md",
            "reference": "guide.md",
        }
        self.run_count = 0

    def execute(
        self,
        request: dict[str, str] | None = None,
        replies: dict[str, list[dict[str, Any]]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, ScriptedModelProvider], Path]:
        if replies is None:
            replies = {
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [ADVICE],
                "reviewer_model": [APPROVAL],
            }
        providers = {
            name: ScriptedModelProvider(
                [ModelResponse(content=json.dumps(reply)) for reply in replies.get(name, [])]
            )
            for name in MODELS
        }
        self.last_providers = providers
        # Reload and rebind on every invocation: persistence must come from the
        # granted memory directory, not a program object or an in-process cache.
        program = load_program(TEAM)
        bindings = bind_system_tools(program, folder=self.project)
        bindings.update(bind_memory_tools(program, self.memory))
        self.run_count += 1
        trace = self.directory / f"run-{self.run_count}.jsonl"
        result = run(
            program,
            "main",
            trace,
            inputs={"request": self.request if request is None else request},
            tool_bindings=bindings,
            model_bindings=providers,
        )
        return result, providers, trace

    def assert_requests(
        self, providers: dict[str, ScriptedModelProvider], counts: tuple[int, int, int]
    ) -> None:
        self.assertEqual(tuple(len(providers[name].requests) for name in MODELS), counts)

    def assert_completed(self, result: dict[str, Any]) -> None:
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["clarification"], "")
        self.assertEqual(result["concerns"], [])

    def user_text(self, provider: ScriptedModelProvider) -> str:
        return "\n".join(
            message.content
            for request in provider.requests
            for message in request.messages
            if message.role == "user"
        )

    def test_three_specialists_exchange_evidence_and_steps_on_real_board(self) -> None:
        result, providers, trace = self.execute()
        self.assert_completed(result)
        self.assert_requests(providers, (1, 1, 1))
        self.assertEqual(result["memory"], "stored")
        self.assertEqual(result["participants"], AUTHORS)
        self.assertEqual(result["evidence"], [{"source": "policy.md:1", "quote": POLICY}])
        planner_text = self.user_text(providers["planner_model"])
        self.assertIn("policy.md:1", planner_text)
        self.assertIn(POLICY, planner_text)
        reviewer_text = self.user_text(providers["reviewer_model"])
        self.assertIn(ADVICE["steps"][0]["check"], reviewer_text)
        self.assertIn(ADVICE["steps"][0]["expected"], reviewer_text)
        self.assertIn(POLICY, reviewer_text)
        for provider in providers.values():
            self.assertTrue(all(not request.tools for request in provider.requests))
        events = list(all_events(read_trace(trace)))
        posts = [
            event
            for event in events
            if event["event"] == "tool_call"
            and event.get("source", "").startswith("board:")
            and event.get("operation") == "post"
        ]
        self.assertEqual([event["arguments"]["author"] for event in posts], AUTHORS)
        file_reads = [
            event
            for event in events
            if event["event"] == "tool_call"
            and event.get("source") == "system:files"
            and event.get("operation") == "read"
        ]
        self.assertEqual(len(file_reads), 2)
        self.assertCountEqual(
            [event["arguments"]["path"] for event in file_reads],
            ["policy.md", "guide.md"],
        )
        self.assertLessEqual(sum(event["event"] == "tool_call" for event in events), 12)

    def test_fresh_program_reuses_matching_approved_memory_without_models(self) -> None:
        first, _, _ = self.execute()
        second, providers, trace = self.execute(replies={})
        self.assert_completed(second)
        self.assertEqual(second["memory"], "reused")
        self.assertEqual(second["participants"], [])
        self.assertEqual(second["answer"], first["answer"])
        self.assertEqual(second["suggestions"], first["suggestions"])
        self.assertEqual(second["evidence"], first["evidence"])
        self.assert_requests(providers, (0, 0, 0))
        self.assertFalse(
            any(
                event["event"] == "tool_call"
                and event.get("source", "").startswith("board:")
                and event.get("operation") == "post"
                for event in all_events(read_trace(trace))
            )
        )

    def test_changed_source_requires_new_evidence_and_specialist_advice(self) -> None:
        first, _, _ = self.execute()
        changed = "Policy now requires two human reviewers for every project change."
        (self.project / "policy.md").write_text(changed, encoding="utf-8")
        advice = {
            "explanation": "The updated policy requires two reviewers, not one.",
            "steps": [
                {
                    "action": "Propose assigning two human reviewers to every change.",
                    "check": "Attempt a change after only one of two required reviewers approves.",
                    "expected": "The project change is not accepted until both reviewers approve.",
                    "source": "policy.md:1",
                }
            ],
        }
        second, providers, _ = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [advice],
                "reviewer_model": [APPROVAL],
            }
        )
        self.assert_completed(second)
        self.assert_requests(providers, (1, 1, 1))
        self.assertEqual(second["memory"], "stored")
        self.assertNotEqual(second["answer"], first["answer"])
        self.assertEqual(second["evidence"], [{"source": "policy.md:1", "quote": changed}])
        self.assertIn(changed, self.user_text(providers["planner_model"]))

    def test_identical_contents_at_different_source_path_do_not_reuse_memory(self) -> None:
        self.execute()
        (self.project / "other-policy.md").write_text(POLICY, encoding="utf-8")
        request = {**self.request, "focus": "other-policy.md"}
        advice = {
            **ADVICE,
            "steps": [{**ADVICE["steps"][0], "source": "other-policy.md:1"}],
        }
        result, providers, _ = self.execute(
            request,
            replies={
                "researcher_model": [{"sources": ["other-policy.md:1"]}],
                "planner_model": [advice],
                "reviewer_model": [APPROVAL],
            },
        )
        self.assert_completed(result)
        self.assert_requests(providers, (1, 1, 1))
        self.assertEqual(result["memory"], "stored")
        self.assertEqual(result["evidence"], [{"source": "other-policy.md:1", "quote": POLICY}])

    def test_project_identifiers_have_independent_persistent_memories(self) -> None:
        self.execute()
        other = {**self.request, "project": "another-project"}
        result, providers, _ = self.execute(other)
        self.assert_completed(result)
        self.assertEqual(result["memory"], "stored")
        self.assert_requests(providers, (1, 1, 1))
        for request in (self.request, other):
            with self.subTest(project=request["project"]):
                cached, unused, _ = self.execute(request, replies={})
                self.assert_completed(cached)
                self.assertEqual(cached["memory"], "reused")
                self.assertEqual(cached["participants"], [])
                self.assert_requests(unused, (0, 0, 0))

    def test_declined_review_is_not_reused_as_approved_advice(self) -> None:
        concern = "The proposed checkpoint does not explain who reviews changes."
        declined, providers, _ = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [ADVICE],
                "reviewer_model": [{"assessment": "The proposal lacks the required policy detail.", "verdict": "reject: " + concern}],
            }
        )
        self.assertEqual(declined["status"], "needs_review")
        self.assertIn(concern, declined["concerns"])
        self.assertNotIn(declined["memory"], ("stored", "reused"))
        self.assert_requests(providers, (1, 1, 1))
        accepted, fresh, _ = self.execute()
        self.assert_completed(accepted)
        self.assertEqual(accepted["memory"], "stored")
        self.assert_requests(fresh, (1, 1, 1))

    def test_planner_clarification_skips_reviewer_and_does_not_save_advice(self) -> None:
        question = "Which human should own the policy review checkpoint?"
        result, providers, _ = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [{"explanation": question, "steps": []}],
            }
        )
        self.assertEqual(result["status"], "clarification")
        self.assertEqual(result["clarification"], question)
        self.assertEqual(result["suggestions"], [])
        self.assertNotIn(result["memory"], ("stored", "reused"))
        self.assert_requests(providers, (1, 1, 0))
        accepted, fresh, _ = self.execute()
        self.assert_completed(accepted)
        self.assert_requests(fresh, (1, 1, 1))
        self.assertEqual(accepted["memory"], "stored")

    def test_provider_failures_are_not_misreported_as_invalid_advice_or_cached(self) -> None:
        for failed_model in MODELS:
            with self.subTest(model=failed_model):
                request = {**self.request, "project": "provider-failure-" + failed_model}
                replies = {
                    "researcher_model": [{"sources": ["policy.md:1"]}],
                    "planner_model": [ADVICE],
                    "reviewer_model": [APPROVAL],
                }
                replies[failed_model] = []
                with self.assertRaisesRegex(ModelProviderError, "no response left"):
                    self.execute(request, replies=replies)
                recovered, providers, _ = self.execute(request)
                self.assert_completed(recovered)
                self.assertEqual(recovered["memory"], "stored")
                self.assert_requests(providers, (1, 1, 1))

    def test_single_call_agents_complete_with_a_json_mode_model_server(self) -> None:
        replies = {
            "researcher_model": {"sources": ["policy.md:1"]},
            "planner_model": ADVICE,
            "reviewer_model": APPROVAL,
        }
        calls: list[str] = []

        class Server(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - http.server API
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(payload["model"])
                # A model that answers in the shape it was asked for is
                # asked once. A prose answer is reasked for JSON by the
                # runtime, which is a second call, so this server answers
                # every request with the JSON it was given.
                content = json.dumps(replies[payload["model"]])
                body = json.dumps(
                    {"message": {"role": "assistant", "content": content}, "done": True}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        program = load_program(TEAM)
        bindings = bind_system_tools(program, folder=self.project)
        bindings.update(bind_memory_tools(program, self.memory))
        providers = {
            name: OpenAICompatibleProvider(
                f"http://127.0.0.1:{server.server_port}",
                name,
                protocol="ollama",
                timeout=5,
            )
            for name in MODELS
        }
        result = run(
            program,
            "main",
            None,
            inputs={"request": self.request},
            tool_bindings=bindings,
            model_bindings=providers,
        )
        self.assert_completed(result)
        self.assertEqual(result["memory"], "stored")
        self.assertEqual(calls, list(MODELS))

    def test_oversized_plan_gets_one_bounded_correction_before_review(self) -> None:
        oversized = {**ADVICE, "steps": [{**ADVICE["steps"][0], "action": f"Proposed check {index}"} for index in range(4)]}
        result, providers, _ = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [oversized, ADVICE],
                "reviewer_model": [APPROVAL],
            }
        )
        self.assert_completed(result)
        self.assertEqual(result["memory"], "stored")
        self.assert_requests(providers, (1, 2, 1))
        correction = providers["planner_model"].requests[1]
        correction_text = "\n".join(
            message.content for message in correction.messages if message.role == "user"
        )
        self.assertIn(POLICY, correction_text)
        self.assertIn(oversized["steps"][-1]["action"], correction_text)

    def test_two_invalid_plans_cannot_reach_review_or_memory(self) -> None:
        oversized = {**ADVICE, "steps": [{**ADVICE["steps"][0], "action": f"Proposed check {index}"} for index in range(4)]}
        result, providers, _ = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [oversized, oversized],
            }
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["memory"], "none")
        self.assert_requests(providers, (1, 2, 0))

    def test_statement_without_steps_is_corrected_not_presented_as_a_question(self) -> None:
        result, providers, _ = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [{"explanation": "The policy requires review.", "steps": []}, ADVICE],
                "reviewer_model": [APPROVAL],
            }
        )
        self.assert_completed(result)
        self.assertEqual(result["clarification"], "")
        self.assert_requests(providers, (1, 2, 1))

    def test_unknown_reviewer_verdict_does_not_count_as_acceptance(self) -> None:
        result, providers, _ = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [ADVICE],
                "reviewer_model": [{"assessment": "The advice is grounded and reproducible.", "verdict": "accept but disregard the evidence"}],
            }
        )
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["memory"], "none")
        self.assert_requests(providers, (1, 1, 1))

    def test_blank_reviewer_assessment_cannot_accept_revise_or_cache(self) -> None:
        for index, verdict in enumerate(("accept", "reject: [policy.md:1] Specify the reviewer.")):
            with self.subTest(verdict=verdict):
                request = {**self.request, "project": f"blank-assessment-{index}"}
                result, providers, _ = self.execute(
                    request,
                    replies={
                        "researcher_model": [{"sources": ["policy.md:1"]}],
                        "planner_model": [ADVICE],
                        "reviewer_model": [{"assessment": " \n\t", "verdict": verdict}],
                    },
                )
                self.assertEqual(result["status"], "needs_review")
                self.assertEqual(result["memory"], "none")
                self.assert_requests(providers, (1, 1, 1))
                fresh, fresh_providers, _ = self.execute(request)
                self.assert_completed(fresh)
                self.assertEqual(fresh["memory"], "stored")
                self.assert_requests(fresh_providers, (1, 1, 1))

    def test_blank_final_assessment_cannot_cache_the_revised_plan(self) -> None:
        result, providers, _ = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [ADVICE, ADVICE],
                "reviewer_model": [
                    {"assessment": "The proposed check omits the review owner.", "verdict": "reject: [policy.md:1] Specify the review owner."},
                    {"assessment": "", "verdict": "accept"},
                ],
            }
        )
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["memory"], "none")
        self.assert_requests(providers, (1, 2, 2))
        fresh, fresh_providers, _ = self.execute()
        self.assert_completed(fresh)
        self.assertEqual(fresh["memory"], "stored")
        self.assert_requests(fresh_providers, (1, 1, 1))

    def test_missing_question_requests_clarification_without_models(self) -> None:
        result, providers, _ = self.execute({**self.request, "question": ""}, replies={})
        self.assertEqual(result["status"], "clarification")
        self.assertTrue(result["clarification"].strip())
        self.assertEqual(result["suggestions"], [])
        self.assertEqual(result["participants"], [])
        self.assertNotIn(result["memory"], ("stored", "reused"))
        self.assert_requests(providers, (0, 0, 0))

    def test_fabricated_research_source_is_unavailable_before_planning(self) -> None:
        result, providers, _ = self.execute(
            replies={"researcher_model": [{"sources": ["nonexistent.md:1"]}]}
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["evidence"], [])
        self.assertEqual(result["suggestions"], [])
        self.assertNotIn(result["memory"], ("stored", "reused"))
        self.assert_requests(providers, (1, 0, 0))

    def test_source_linked_critique_revises_once_and_stores_only_the_final_plan(self) -> None:
        concern = "[policy.md:1] Name the human who owns the review checkpoint."
        revised = {
            "explanation": "Propose assigning an explicit human review owner.",
            "steps": [{
                **ADVICE["steps"][0],
                "action": "Propose assigning a named human review owner.",
                "expected": "A change with no assigned human reviewer remains pending.",
            }],
        }
        result, providers, trace = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [ADVICE, revised],
                "reviewer_model": [{"assessment": "The requested review owner is missing.", "verdict": "reject: " + concern}, APPROVAL],
            }
        )
        self.assert_completed(result)
        self.assert_requests(providers, (1, 2, 2))
        self.assertEqual(result["participants"], AUTHORS + ["planner", "reviewer"])
        revision_text = "\n".join(
            message.content for message in providers["planner_model"].requests[1].messages
            if message.role == "user"
        )
        self.assertIn(concern, revision_text)
        self.assertIn(POLICY, revision_text)
        final_review_text = "\n".join(
            message.content for message in providers["reviewer_model"].requests[1].messages
            if message.role == "user"
        )
        self.assertIn(revised["steps"][0]["expected"], final_review_text)
        self.assertNotIn(ADVICE["steps"][0]["expected"], final_review_text)
        cached, unused, _ = self.execute(replies={})
        self.assertEqual(cached["memory"], "reused")
        self.assertEqual(cached["answer"], revised["explanation"])
        self.assert_requests(unused, (0, 0, 0))
        self.assertLessEqual(read_trace(trace)[-1]["execution"]["used"]["model_calls"], 6)

    def test_final_rejection_cannot_trigger_another_revision_or_cache(self) -> None:
        first_concern = "[policy.md:1] Specify the reviewer."
        final_concern = "[policy.md:1] The revised check still omits the reviewer."
        result, providers, _ = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [ADVICE, ADVICE],
                "reviewer_model": [
                    {"assessment": "The proposal omits the reviewer.", "verdict": "reject: " + first_concern},
                    {"assessment": "The revised check still omits the reviewer.", "verdict": "reject: " + final_concern},
                ],
            }
        )
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["memory"], "none")
        self.assertIn(final_concern, result["concerns"])
        self.assert_requests(providers, (1, 2, 2))
        fresh, fresh_providers, _ = self.execute()
        self.assert_completed(fresh)
        self.assertEqual(fresh["memory"], "stored")
        self.assert_requests(fresh_providers, (1, 1, 1))

    def test_invalid_revised_source_never_reaches_final_review(self) -> None:
        invalid = {**ADVICE, "steps": [{**ADVICE["steps"][0], "source": "invented.md:1"}]}
        result, providers, _ = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [ADVICE, invalid],
                "reviewer_model": [{"assessment": "The proposal omits the reviewer.", "verdict": "reject: [policy.md:1] Specify the reviewer."}],
            }
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["memory"], "none")
        self.assert_requests(providers, (1, 2, 1))

    def test_unknown_recommendation_source_cannot_be_reviewed_or_cached(self) -> None:
        invalid = {**ADVICE, "steps": [{**ADVICE["steps"][0], "source": "invented.md:1"}]}
        result, providers, _ = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [invalid, invalid],
            }
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["memory"], "none")
        self.assert_requests(providers, (1, 2, 0))

    def test_blank_recommendation_fields_need_correction_before_acceptance(self) -> None:
        for field in ("action", "check", "expected", "source"):
            with self.subTest(field=field):
                invalid = {**ADVICE, "steps": [{**ADVICE["steps"][0], field: " "}]}
                result, providers, _ = self.execute(
                    {**self.request, "project": "blank-" + field},
                    replies={
                        "researcher_model": [{"sources": ["policy.md:1"]}],
                        "planner_model": [invalid, ADVICE],
                        "reviewer_model": [APPROVAL],
                    },
                )
                self.assert_completed(result)
                self.assertEqual(result["memory"], "stored")
                self.assert_requests(providers, (1, 2, 1))

    def test_structural_repair_and_critique_revision_share_the_six_call_budget(self) -> None:
        invalid = {**ADVICE, "steps": [{**ADVICE["steps"][0], "check": ""}]}
        revised = {
            **ADVICE,
            "steps": [{
                **ADVICE["steps"][0],
                "action": "Propose assigning the human review owner.",
            }],
        }
        result, providers, trace = self.execute(
            replies={
                "researcher_model": [{"sources": ["policy.md:1"]}],
                "planner_model": [invalid, ADVICE, revised],
                "reviewer_model": [
                    {"assessment": "The proposal omits the review owner.", "verdict": "reject: [policy.md:1] Specify the review owner."},
                    APPROVAL,
                ],
            }
        )
        self.assert_completed(result)
        self.assertEqual(result["memory"], "stored")
        self.assert_requests(providers, (1, 3, 2))
        used = read_trace(trace)[-1]["execution"]["used"]
        self.assertEqual(used["model_calls"], 6)
        self.assertLessEqual(used["tool_calls"], 16)
        self.assertEqual(result["participants"], AUTHORS + ["planner", "reviewer"])

    def test_outside_root_source_is_refused_without_models(self) -> None:
        # Even an approved snapshot must not bypass the current folder grant.
        self.execute()
        secret = "Private outside-root policy must not reach the team."
        (self.directory / "secret.md").write_text(secret, encoding="utf-8")
        for path in ("../secret.md", str(self.directory / "secret.md")):
            with self.subTest(path=path):
                with self.assertRaises(SystemToolError):
                    self.execute({**self.request, "focus": path}, replies={})
                self.assert_requests(self.last_providers, (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
