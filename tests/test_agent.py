from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any

from kosika.agent import (
    AgentLoop,
    AgentLoopError,
    ApprovalRecord,
    ApprovalRequiredError,
    action_hash,
    plan_hash,
)
from kosika.approval_ledger import ApprovalLedger
from kosika.model import (
    ChatMessage,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    OpenAICompatibleProvider,
    ScriptedModelProvider,
)
from kosika.parser import parse

ROOT = Path(__file__).parents[1]
MOCK_SOURCE = ROOT / "examples" / "test_host.syn"


class AgentLoopTests(unittest.TestCase):
    def program(self):
        return parse(MOCK_SOURCE.read_text(encoding="utf-8"))

    def test_agent_loop_exposes_declared_tools_and_returns_final_text(self) -> None:
        provider = ScriptedModelProvider(
            [
                ModelResponse(
                    tool_calls=(ModelToolCall("call-1", "check_disk", {}),),
                    finish_reason="tool_calls",
                ),
                ModelResponse(content="Disk usage is healthy."),
            ]
        )
        calls: list[tuple[str, dict[str, Any]]] = []

        def execute(tool, arguments):
            calls.append((tool.name, arguments))
            return {"mount": "/", "used_percent": 42.0}

        result = AgentLoop(self.program(), provider, execute).run(
            "monitor", "Check the disk."
        )
        self.assertEqual(result.content, "Disk usage is healthy.")
        self.assertEqual(result.model_steps, 2)
        self.assertEqual(calls, [("check_disk", {})])
        self.assertEqual(provider.requests[0].tools[0].name, "check_disk")
        self.assertEqual(provider.requests[0].messages[-1].role, "user")
        self.assertEqual(provider.requests[1].messages[-1].role, "tool")
        self.assertEqual(len(result.plan_hash), 64)

    def test_agent_loop_exposes_typed_input_schema(self) -> None:
        program = parse(
            """
            record Request {
              mount: Text
            }
            record Result {
              ok: Bool
            }
            tool inspect {
              source mcp "local_mock"
              input: Request
              operation: "inspect"
              access: observe
              output: Result
            }
            agent monitor {
              can: [inspect]
              cannot: []
            }
            """
        )
        provider = ScriptedModelProvider(
            [
                ModelResponse(
                    tool_calls=(ModelToolCall("call-1", "inspect", {"mount": "/"}),)
                ),
                ModelResponse(content="Inspected."),
            ]
        )
        calls: list[dict[str, Any]] = []

        def execute(tool: Any, arguments: dict[str, Any]) -> dict[str, bool]:
            calls.append(arguments)
            return {"ok": True}

        loop = AgentLoop(program, provider, execute)
        result = loop.run("monitor", "Inspect the mount")
        self.assertEqual(result.content, "Inspected.")
        self.assertEqual(calls, [{"mount": "/"}])
        self.assertEqual(
            provider.requests[0].tools[0].input_schema["required"], ["mount"]
        )

    def test_agent_loop_fails_closed_for_unknown_or_invalid_tools(self) -> None:
        unknown = ScriptedModelProvider(
            [ModelResponse(tool_calls=(ModelToolCall("call-1", "shell", {}),))]
        )
        with self.assertRaisesRegex(AgentLoopError, "undeclared tool"):
            AgentLoop(self.program(), unknown, lambda tool, args: {}).run(
                "monitor", "Do something"
            )

        invalid_input = ScriptedModelProvider(
            [
                ModelResponse(
                    tool_calls=(ModelToolCall("call-1", "check_disk", {"x": 1}),)
                )
            ]
        )
        with self.assertRaisesRegex(AgentLoopError, "Invalid arguments"):
            AgentLoop(self.program(), invalid_input, lambda tool, args: {}).run(
                "monitor", "Check the disk"
            )

        invalid_output = ScriptedModelProvider(
            [ModelResponse(tool_calls=(ModelToolCall("call-1", "check_disk", {}),))]
        )
        with self.assertRaisesRegex(AgentLoopError, "Invalid result"):
            AgentLoop(self.program(), invalid_output, lambda tool, args: {}).run(
                "monitor", "Check the disk"
            )

    def test_execute_tool_requires_matching_plan_approval(self) -> None:
        program = parse(
            """
            record MutationResult {
              ok: Bool
            }

            tool mutate {
              source mcp "local_mock"
              operation: "mutate"
              access: execute
              output: MutationResult
            }

            agent operator {
              can: [mutate]
              cannot: []
            }
            """
        )
        response = ModelResponse(tool_calls=(ModelToolCall("call-1", "mutate", {}),))
        provider = ScriptedModelProvider([response])
        loop = AgentLoop(program, provider, lambda tool, args: {"ok": True})
        with self.assertRaises(ApprovalRequiredError):
            loop.run("operator", "Apply the change")

        bindings = loop._bindings_for(program.agents[0])
        approved = ApprovalRecord(
            plan_hash=plan_hash("operator", "Apply the change", bindings),
            approved_by="test-user",
            action_hash=action_hash(
                plan_hash("operator", "Apply the change", bindings), "mutate", {}
            ),
        )
        provider = ScriptedModelProvider([response, ModelResponse(content="Applied.")])
        with tempfile.TemporaryDirectory() as directory:
            result = AgentLoop(
                program,
                provider,
                lambda tool, args: {"ok": True},
                approval_ledger=ApprovalLedger(Path(directory) / "approvals.sqlite3"),
            ).run("operator", "Apply the change", approval=approved)
        self.assertEqual(result.content, "Applied.")

    def test_approval_is_bound_to_arguments_and_single_use(self) -> None:
        program = parse("""
            record MutationResult { ok: Bool }
            tool mutate {
              source mcp "local_mock"
              operation: "mutate"
              access: execute
              output: MutationResult
            }
            agent operator { can: [mutate] cannot: [] }
        """)
        schema = {
            "type": "object",
            "properties": {"target": {"type": "string"}},
            "required": ["target"],
            "additionalProperties": False,
        }
        calls: list[dict[str, Any]] = []

        def execute(tool, arguments):
            calls.append(arguments)
            return {"ok": True}

        provider = ScriptedModelProvider(
            [
                ModelResponse(
                    tool_calls=(ModelToolCall("one", "mutate", {"target": "B"}),)
                )
            ]
        )
        loop = AgentLoop(program, provider, execute, input_schemas={"mutate": schema})
        plan = loop.plan_hash_for("operator", "Apply")
        approval = ApprovalRecord(
            plan, "reviewer", action_hash=action_hash(plan, "mutate", {"target": "A"})
        )
        with self.assertRaisesRegex(ApprovalRequiredError, "action"):
            loop.run("operator", "Apply", approval=approval)
        self.assertEqual(calls, [])

        provider = ScriptedModelProvider(
            [
                ModelResponse(
                    tool_calls=(ModelToolCall("one", "mutate", {"target": "A"}),)
                ),
                ModelResponse(
                    tool_calls=(ModelToolCall("two", "mutate", {"target": "A"}),)
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            loop = AgentLoop(
                program,
                provider,
                execute,
                input_schemas={"mutate": schema},
                approval_ledger=ApprovalLedger(Path(directory) / "approvals.sqlite3"),
            )
            with self.assertRaisesRegex(ApprovalRequiredError, "at most one"):
                loop.run("operator", "Apply", approval=approval)
        self.assertEqual(calls, [{"target": "A"}])

    def test_invalid_later_tool_call_prevents_batch_execution(self) -> None:
        calls = []
        response = ModelResponse(
            tool_calls=(
                ModelToolCall("one", "check_disk", {}),
                ModelToolCall("two", "unknown", {}),
            )
        )
        with self.assertRaisesRegex(AgentLoopError, "undeclared tool"):
            AgentLoop(
                self.program(),
                ScriptedModelProvider([response]),
                lambda tool, args: calls.append(tool.name),
            ).run("monitor", "Check")
        self.assertEqual(calls, [])

    def test_agent_loop_limits_model_steps_and_validates_inputs(self) -> None:
        provider = ScriptedModelProvider(
            [ModelResponse(content="still thinking") for _ in range(2)]
        )
        result = AgentLoop(self.program(), provider, lambda tool, args: {}).run(
            "monitor", "Explain"
        )
        self.assertEqual(result.model_steps, 1)

        looping_provider = ScriptedModelProvider(
            [
                ModelResponse(tool_calls=(ModelToolCall("call-1", "check_disk", {}),)),
                ModelResponse(tool_calls=(ModelToolCall("call-2", "check_disk", {}),)),
            ]
        )
        with self.assertRaisesRegex(AgentLoopError, "maximum model steps"):
            AgentLoop(
                self.program(),
                looping_provider,
                lambda tool, args: {"mount": "/", "used_percent": 1},
                max_steps=2,
            ).run("monitor", "Loop")


class OpenAICompatibleProviderTests(unittest.TestCase):
    def test_provider_serializes_messages_and_parses_tool_calls(self) -> None:
        captured: dict[str, Any] = {}

        def transport(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "check_disk",
                                            "arguments": '{"mount":"/"}',
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "total_tokens": 14,
                    },
                }
            ).encode("utf-8")

        provider = OpenAICompatibleProvider(
            "http://localhost:11434/v1",
            "local-model",
            api_key="secret",
            transport=transport,
        )
        response = provider.complete(
            ModelRequest(
                messages=(
                    ChatMessage(role="system", content="Be safe."),
                    ChatMessage(role="user", content="Check disk."),
                ),
                tools=(),
            )
        )
        self.assertEqual(captured["url"], "http://localhost:11434/v1/chat/completions")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(captured["body"]["model"], "local-model")
        self.assertEqual(response.tool_calls[0].arguments, {"mount": "/"})
        self.assertIsNotNone(response.usage)
        assert response.usage is not None
        self.assertEqual(response.usage.total_tokens, 14)

    def test_provider_rejects_malformed_responses_and_transport_errors(self) -> None:
        def malformed(request, timeout):
            return b'{"choices": []}'

        provider = OpenAICompatibleProvider(
            "http://localhost:11434", "local-model", transport=malformed
        )
        with self.assertRaisesRegex(ModelProviderError, "did not contain choices"):
            provider.complete(
                ModelRequest(messages=(ChatMessage(role="user", content="Hi"),))
            )

        def unavailable(request, timeout):
            raise urllib.error.URLError("offline")

        provider = OpenAICompatibleProvider(
            "http://localhost:11434", "local-model", transport=unavailable
        )
        with self.assertRaisesRegex(ModelProviderError, "request failed"):
            provider.complete(
                ModelRequest(messages=(ChatMessage(role="user", content="Hi"),))
            )


if __name__ == "__main__":
    unittest.main()
