from __future__ import annotations

import io
import json
import tempfile
import threading
import time
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
)
from kosika.approval_ledger import ApprovalLedger
from kosika.checker import CheckError, check
from kosika.durable import AgentExecutionSession
from kosika.model import (
    ChatMessage,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    ModelTool,
    ModelToolCall,
    OpenAICompatibleProvider,
    ScriptedModelProvider,
)
from kosika.parser import parse
from kosika.trace_replay import replay_trace

ROOT = Path(__file__).parents[1]
MOCK_SOURCE = ROOT / "examples" / "test_host.kos"


class InterruptingAgentSession(AgentExecutionSession):
    def __init__(self, path: Path, *, binding_id: str, crash_state: str):
        super().__init__(path, binding_id=binding_id)
        self.crash_state = crash_state
        self.crashed = False

    def checkpoint(self, state: str, payload: dict[str, Any]) -> None:
        super().checkpoint(state, payload)
        if state == self.crash_state and not self.crashed:
            self.crashed = True
            raise RuntimeError(f"simulated interruption at {state}")


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

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "agent.jsonl"
            result = AgentLoop(self.program(), provider, execute).run(
                "monitor", "Check the disk.", trace_path=trace
            )
            self.assertEqual(replay_trace(trace), "Disk usage is healthy.")
            tampered = Path(directory) / "tampered.jsonl"
            events = [
                json.loads(line)
                for line in trace.read_text(encoding="utf-8").splitlines()
            ]
            for event in events:
                if event.get("event") == "agent_tool_call":
                    event["result"]["used_percent"] = 99.0
            tampered.write_text(
                "\n".join(json.dumps(event) for event in events) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "messages"):
                replay_trace(tampered)
        self.assertEqual(result.content, "Disk usage is healthy.")
        self.assertEqual(result.model_steps, 2)
        self.assertEqual(calls, [("check_disk", {})])
        self.assertEqual(provider.requests[0].tools[0].name, "check_disk")
        self.assertEqual(provider.requests[0].messages[-1].role, "user")
        self.assertEqual(provider.requests[1].messages[-1].role, "tool")
        self.assertEqual(len(result.plan_hash), 64)

    def test_durable_agent_completion_is_cached_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "agent.sqlite"
            first = AgentLoop(
                self.program(),
                ScriptedModelProvider([ModelResponse(content="first result")]),
                lambda tool, arguments: {},
            ).run(
                "monitor",
                "Run once.",
                session=AgentExecutionSession(journal, binding_id="model-v1"),
            )
            resumed_provider = ScriptedModelProvider(
                [ModelResponse(content="must not be called")]
            )
            resumed = AgentLoop(
                self.program(), resumed_provider, lambda tool, arguments: {}
            ).run(
                "monitor",
                "Run once.",
                session=AgentExecutionSession(
                    journal, binding_id="model-v1", resume=True
                ),
            )
            self.assertEqual(first.content, "first result")
            self.assertEqual(resumed.content, "first result")
            self.assertEqual(resumed_provider.requests, [])
            with self.assertRaisesRegex(
                AgentLoopError, "different plan or host binding"
            ):
                AgentLoop(
                    self.program(),
                    ScriptedModelProvider(
                        [ModelResponse(content="must not be called")]
                    ),
                    lambda tool, arguments: {},
                ).run(
                    "monitor",
                    "Run once.",
                    session=AgentExecutionSession(
                        journal, binding_id="different-host", resume=True
                    ),
                )

    def test_durable_agent_resumes_from_ready_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "agent.sqlite"
            interrupted_provider = ScriptedModelProvider(
                [ModelResponse(content="must not be called")]
            )
            with self.assertRaisesRegex(RuntimeError, "ready"):
                AgentLoop(
                    self.program(), interrupted_provider, lambda tool, arguments: {}
                ).run(
                    "monitor",
                    "Resume from ready.",
                    session=InterruptingAgentSession(
                        journal, binding_id="model-v1", crash_state="ready"
                    ),
                )
            self.assertEqual(interrupted_provider.requests, [])
            resumed_provider = ScriptedModelProvider(
                [ModelResponse(content="resumed from ready")]
            )
            result = AgentLoop(
                self.program(), resumed_provider, lambda tool, arguments: {}
            ).run(
                "monitor",
                "Resume from ready.",
                session=AgentExecutionSession(
                    journal, binding_id="model-v1", resume=True
                ),
            )
            self.assertEqual(result.content, "resumed from ready")
            self.assertEqual(len(resumed_provider.requests), 1)

    def test_durable_agent_resumes_from_model_response_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "agent.sqlite"
            interrupted_provider = ScriptedModelProvider(
                [ModelResponse(content="checkpointed final")]
            )
            with self.assertRaisesRegex(RuntimeError, "model_response"):
                AgentLoop(
                    self.program(), interrupted_provider, lambda tool, arguments: {}
                ).run(
                    "monitor",
                    "Resume final response.",
                    session=InterruptingAgentSession(
                        journal, binding_id="model-v1", crash_state="model_response"
                    ),
                )
            resumed_provider = ScriptedModelProvider(
                [ModelResponse(content="must not be called")]
            )
            result = AgentLoop(
                self.program(), resumed_provider, lambda tool, arguments: {}
            ).run(
                "monitor",
                "Resume final response.",
                session=AgentExecutionSession(
                    journal, binding_id="model-v1", resume=True
                ),
            )
            self.assertEqual(result.content, "checkpointed final")
            self.assertEqual(resumed_provider.requests, [])

    def test_durable_agent_resumes_from_tool_result_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "agent.sqlite"
            first_provider = ScriptedModelProvider(
                [
                    ModelResponse(
                        finish_reason="tool_calls",
                        tool_calls=(
                            ModelToolCall("call-1", "check_disk", {}),
                            ModelToolCall("call-2", "check_disk", {}),
                        ),
                    )
                ]
            )
            calls: list[dict[str, Any]] = []

            def execute(tool, arguments):
                calls.append(arguments)
                return {"mount": "/", "used_percent": 42.0}

            with self.assertRaisesRegex(RuntimeError, "tool_result"):
                AgentLoop(self.program(), first_provider, execute).run(
                    "monitor",
                    "Resume after the first tool.",
                    session=InterruptingAgentSession(
                        journal, binding_id="model-v1", crash_state="tool_result"
                    ),
                )
            self.assertEqual(len(calls), 1)
            resumed_provider = ScriptedModelProvider(
                [ModelResponse(content="both tools resumed")]
            )
            result = AgentLoop(self.program(), resumed_provider, execute).run(
                "monitor",
                "Resume after the first tool.",
                session=AgentExecutionSession(
                    journal, binding_id="model-v1", resume=True
                ),
            )
            self.assertEqual(result.content, "both tools resumed")
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(resumed_provider.requests), 1)
            self.assertEqual(
                [
                    message.role
                    for message in resumed_provider.requests[0].messages[-2:]
                ],
                ["tool", "tool"],
            )

    def test_durable_agent_rejects_interrupted_tool_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "agent.sqlite"
            provider = ScriptedModelProvider(
                [
                    ModelResponse(
                        tool_calls=(ModelToolCall("call-1", "check_disk", {}),),
                        finish_reason="tool_calls",
                    )
                ]
            )

            def interrupted(tool, arguments):
                raise RuntimeError("tool process stopped")

            with self.assertRaisesRegex(RuntimeError, "tool process stopped"):
                AgentLoop(self.program(), provider, interrupted).run(
                    "monitor",
                    "Check the disk.",
                    session=AgentExecutionSession(journal, binding_id="model-v1"),
                )
            self.assertEqual(
                AgentExecutionSession(journal, binding_id="model-v1").inspect()[
                    "state"
                ],
                "tool_pending",
            )
            resumed_provider = ScriptedModelProvider(
                [ModelResponse(content="must not be called")]
            )
            with self.assertRaisesRegex(
                AgentLoopError, "requires reconciliation"
            ) as caught:
                AgentLoop(
                    self.program(), resumed_provider, lambda tool, arguments: {}
                ).run(
                    "monitor",
                    "Check the disk.",
                    session=AgentExecutionSession(
                        journal, binding_id="model-v1", resume=True
                    ),
                )
            self.assertEqual(caught.exception.code, "uncertain_outcome")
            self.assertEqual(resumed_provider.requests, [])

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

    def test_model_declaration_configures_requests_and_agent_limits(self) -> None:
        program = parse(
            """
            record Request { prompt: Text }
            record Result { ok: Bool }
            model planner {
              input: Request
              output: Result
              deployment: "declared-model"
              parameters: {
                temperature: 0.2
                max_output_tokens: 128
                top_p: 0.9
                frequency_penalty: -0.2
                presence_penalty: 0.1
                stop: ["DONE", "END"]
              }
            }
            agent bounded {
              model: planner
              can: []
              cannot: []
              max_steps: 2
              max_tool_calls: 3
              max_model_calls: 2
            }
            """
        )
        provider = ScriptedModelProvider([ModelResponse(content="Done.")])
        loop = AgentLoop(program, provider, lambda tool, args: {})
        result = loop.run("bounded", "Plan the work.")

        self.assertEqual(result.content, "Done.")
        request = provider.requests[0]
        self.assertEqual(request.model, "declared-model")
        self.assertEqual(request.temperature, 0.2)
        self.assertEqual(request.max_output_tokens, 128)
        self.assertEqual(request.top_p, 0.9)
        self.assertEqual(request.frequency_penalty, -0.2)
        self.assertEqual(request.presence_penalty, 0.1)
        self.assertEqual(request.stop, ("DONE", "END"))

    def test_declared_agent_instructions_are_system_context_and_hash_input(
        self,
    ) -> None:
        source = """
        record Request { prompt: Text }
        record Result { ok: Bool }
        model planner { input: Request output: Result }
        agent instructed {
          model: planner
          instructions: "Be concise and cite tool results."
          can: []
          cannot: []
        }
        """
        program = parse(source)
        provider = ScriptedModelProvider([ModelResponse(content="Done.")])
        loop = AgentLoop(program, provider, lambda tool, args: {})
        result = loop.run(
            "instructed",
            "Plan the work.",
            system_prompt="Return the final answer plainly.",
        )

        self.assertEqual(result.content, "Done.")
        self.assertEqual(
            provider.requests[0].messages[0].content,
            "Be concise and cite tool results.",
        )
        self.assertEqual(
            provider.requests[0].messages[1].content,
            "Return the final answer plainly.",
        )
        without_instructions = parse(
            source.replace('instructions: "Be concise and cite tool results."', "")
        )
        self.assertNotEqual(
            loop.plan_hash_for("instructed", "Plan the work."),
            AgentLoop(
                without_instructions,
                ScriptedModelProvider([]),
                lambda tool, args: {},
            ).plan_hash_for("instructed", "Plan the work."),
        )

    def test_structured_agent_input_and_output_are_schema_checked(self) -> None:
        program = parse(
            """
            record Request { prompt: Text }
            record Result { ok: Bool }
            model planner { input: Request output: Result }
            agent bounded { model: planner can: [] cannot: [] }
            """
        )
        provider = ScriptedModelProvider([ModelResponse(content='{"ok": true}')])
        result = AgentLoop(program, provider, lambda tool, args: {}).run(
            "bounded",
            input_value={"prompt": "Plan"},
        )
        self.assertEqual(result.structured_output, {"ok": True})
        self.assertEqual(
            provider.requests[0].messages[-1].content, '{"prompt": "Plan"}'
        )
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "structured-agent.jsonl"
            traced = AgentLoop(
                program,
                ScriptedModelProvider([ModelResponse(content='{"ok": true}')]),
                lambda tool, args: {},
            ).run(
                "bounded",
                input_value={"prompt": "Plan"},
                trace_path=trace,
            )
            self.assertEqual(traced.structured_output, {"ok": True})
            self.assertEqual(replay_trace(trace), {"ok": True})

        with self.assertRaisesRegex(AgentLoopError, "Invalid structured agent input"):
            AgentLoop(
                program,
                ScriptedModelProvider([]),
                lambda tool, args: {},
            ).run("bounded", input_value={"wrong": True})

        with self.assertRaisesRegex(AgentLoopError, "Invalid structured agent output"):
            AgentLoop(
                program,
                ScriptedModelProvider([ModelResponse(content='{"wrong": true}')]),
                lambda tool, args: {},
            ).run("bounded", input_value={"prompt": "Plan"})

    def test_agent_deadline_and_cancellation_are_cooperative(self) -> None:
        source = """
        record Request { prompt: Text }
        record Result { ok: Bool }
        model planner { input: Request output: Result }
        agent bounded {
          model: planner
          deadline_seconds: 1
          can: []
          cannot: []
        }
        """
        program = parse(source)
        check(program)
        with self.assertRaises(CheckError):
            check(parse(source.replace("deadline_seconds: 1", "deadline_seconds: 0")))
        cancel = threading.Event()
        cancel.set()
        cancelled = AgentLoop(
            program,
            ScriptedModelProvider([]),
            lambda tool, args: {},
            cancel=cancel,
        )
        with self.assertRaisesRegex(AgentLoopError, "cancelled") as cancelled_error:
            cancelled.run("bounded", input_value={"prompt": "Plan"})
        self.assertEqual(cancelled_error.exception.code, "cancelled")

        class SlowProvider:
            def complete(self, request):
                time.sleep(0.02)
                return ModelResponse(content='{"ok": true}')

        deadline = AgentLoop(
            program,
            SlowProvider(),
            lambda tool, args: {},
            deadline_seconds=0.000001,
        )
        with self.assertRaisesRegex(AgentLoopError, "deadline") as deadline_error:
            deadline.run("bounded", input_value={"prompt": "Plan"})
        self.assertEqual(deadline_error.exception.code, "deadline")

    def test_agent_tool_and_model_call_limits_are_enforced(self) -> None:
        program = parse(
            """
            record Result { ok: Bool }
            tool inspect {
              source mcp "local_mock"
              operation: "inspect"
              access: observe
              output: Result
            }
            agent bounded {
              can: [inspect]
              cannot: []
              max_steps: 3
              max_tool_calls: 1
              max_model_calls: 2
            }
            """
        )
        check(program)
        tool_response = ModelResponse(
            tool_calls=(
                ModelToolCall("one", "inspect", {}),
                ModelToolCall("two", "inspect", {}),
            )
        )
        calls: list[str] = []
        with self.assertRaisesRegex(AgentLoopError, "maximum tool calls"):
            AgentLoop(
                program,
                ScriptedModelProvider([tool_response]),
                lambda tool, args: calls.append(tool.name) or {"ok": True},
            ).run("bounded", "Inspect twice.")
        self.assertEqual(calls, [])

        provider = ScriptedModelProvider(
            [
                ModelResponse(tool_calls=(ModelToolCall("one", "inspect", {}),)),
                ModelResponse(content="Done."),
            ]
        )
        with self.assertRaisesRegex(AgentLoopError, "maximum model calls"):
            AgentLoop(
                program,
                provider,
                lambda tool, args: {"ok": True},
                max_model_calls=1,
            ).run("bounded", "Inspect.")

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

        plan = loop.plan_hash_for("operator", "Apply the change")
        approved = ApprovalRecord(
            plan_hash=plan,
            approved_by="test-user",
            action_hash=action_hash(plan, "mutate", {}),
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

    def test_provider_validates_request_serialization_and_response_shapes(self) -> None:
        captured: dict[str, Any] = {}

        def transport(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return b'{"choices":[{"message":{"content":"ok"}}]}'

        provider = OpenAICompatibleProvider(
            "http://localhost:11434/v1", "default", transport=transport
        )
        response = provider.complete(
            ModelRequest(
                messages=(
                    ChatMessage(
                        role="tool",
                        content="result",
                        tool_calls=(ModelToolCall("call", "inspect", {"x": 1}),),
                        tool_call_id="call",
                        name="inspect",
                    ),
                ),
                tools=(ModelTool("inspect", "Inspect data", {"type": "object"}),),
                model="override",
                temperature=0.2,
                max_output_tokens=12,
                top_p=0.8,
                frequency_penalty=-0.4,
                presence_penalty=0.3,
                stop=("DONE", "END"),
            )
        )
        self.assertEqual(response.content, "ok")
        self.assertEqual(captured["payload"]["model"], "override")
        self.assertEqual(captured["payload"]["temperature"], 0.2)
        self.assertEqual(captured["payload"]["max_tokens"], 12)
        self.assertEqual(captured["payload"]["top_p"], 0.8)
        self.assertEqual(captured["payload"]["frequency_penalty"], -0.4)
        self.assertEqual(captured["payload"]["presence_penalty"], 0.3)
        self.assertEqual(captured["payload"]["stop"], ["DONE", "END"])
        self.assertEqual(captured["payload"]["messages"][0]["tool_call_id"], "call")
        self.assertEqual(captured["payload"]["messages"][0]["name"], "inspect")
        self.assertEqual(captured["payload"]["tools"][0]["function"]["name"], "inspect")

        for invalid in (0, True, -1):
            with (
                self.subTest(max_output_tokens=invalid),
                self.assertRaisesRegex(ModelProviderError, "max_output_tokens"),
            ):
                provider.complete(
                    ModelRequest(
                        messages=(),
                        max_output_tokens=invalid,  # type: ignore[arg-type]
                    )
                )

        with self.assertRaisesRegex(ValueError, "base URL"):
            OpenAICompatibleProvider("", "model")
        with self.assertRaisesRegex(ValueError, "Model name"):
            OpenAICompatibleProvider("http://localhost", "")
        with self.assertRaisesRegex(ValueError, "timeout"):
            OpenAICompatibleProvider("http://localhost", "model", timeout=0)

        invalid_payloads = (
            (None, "JSON object"),
            ({"choices": []}, "did not contain choices"),
            ({"choices": [{}]}, "did not contain a message"),
            ({"choices": [{"message": {"content": 1}}]}, "content must be text"),
            (
                {"choices": [{"message": {"tool_calls": {}}}]},
                "tool_calls must be a list",
            ),
            ({"choices": [{"message": {"tool_calls": [1]}}]}, "invalid tool call"),
            (
                {"choices": [{"message": {"tool_calls": [{"function": {}}]}}]},
                "lacked a function name",
            ),
            (
                {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "inspect",
                                            "arguments": "not-json",
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                },
                "invalid JSON arguments",
            ),
            (
                {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "function": {
                                            "name": "inspect",
                                            "arguments": [],
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                },
                "arguments must be an object",
            ),
            (
                {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "id": 1,
                                        "function": {"name": "inspect"},
                                    }
                                ]
                            }
                        }
                    ]
                },
                "id must be text",
            ),
            ({"choices": [{"message": {}}], "usage": []}, "usage must be an object"),
            (
                {
                    "choices": [{"message": {}}],
                    "usage": {"total_tokens": True},
                },
                "must be an integer",
            ),
        )
        for payload, message in invalid_payloads:
            with (
                self.subTest(message=message),
                self.assertRaisesRegex(ModelProviderError, message),
            ):
                OpenAICompatibleProvider._parse_response(payload)

        fallback = OpenAICompatibleProvider._parse_response(
            {"choices": [{"finish_reason": 4, "message": {}}]}
        )
        self.assertEqual(fallback.finish_reason, "stop")

    def test_provider_translates_http_and_decode_failures(self) -> None:
        def http_failure(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                503,
                "unavailable",
                {},
                io.BytesIO(b"service unavailable"),
            )

        provider = OpenAICompatibleProvider(
            "http://localhost", "model", transport=http_failure
        )
        with self.assertRaisesRegex(ModelProviderError, "HTTP 503"):
            provider.complete(ModelRequest(messages=()))

        invalid_json = OpenAICompatibleProvider(
            "http://localhost", "model", transport=lambda request, timeout: b"not-json"
        )
        with self.assertRaisesRegex(ModelProviderError, "invalid JSON"):
            invalid_json.complete(ModelRequest(messages=()))


if __name__ == "__main__":
    unittest.main()
