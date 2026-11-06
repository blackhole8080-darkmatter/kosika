"""The Kosika MCP server lets an assistant check, test, repair, run, replay, map and declare programs."""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from typing import Any

from kosika.mcp_server import (
    MAX_SOURCE_BYTES,
    PROTOCOL_VERSION,
    TOOLS,
    call_tool,
    handle,
    serve,
)
from kosika.mcp_stdio import MCPStdioClient, MCPToolError

PURE = """
record Order { customer: Text items: Text }
record Summary { greeting: Text count: Number }

fn summarize_order(order: Order) -> Summary {
  return {
    greeting: "Hello, " + order.customer + "!",
    count: order.items.split(separator: ",").length()
  }
}

workflow main(order: Order) -> Summary {
  summary = summarize_order(order)
}
"""

THINKING = """
record Ticket { text: Text }
record Routing { queue: Text sure: Bool }
model assistant

fn route(label: Choice) -> Routing {
  return {queue: label.label, sure: label.confidence > 0.5}
}

workflow main(ticket: Ticket) -> Routing {
  label = classify ticket.text into ["billing", "bug"]
  routing = route(label)
}
"""

TOOLED = """
record City { name: Text }
record Weather { celsius: Number }
tool get_weather {
  source mcp "weather"
  operation: "current"
  access: observe
  input: City
  output: Weather
}
agent forecaster { can: [get_weather] }
workflow main(city: City) -> Weather {
  weather = execute forecaster.get_weather(city.name)
}
"""

ORDER = {"order": {"customer": "Ada", "items": "pen,book"}}

TYPO_FIELD = PURE.replace("order.items", "order.itms")
TYPO_VARIABLE = PURE.replace("summarize_order(order)", "summarize_order(ordr)")
NO_CLOSE_NAME = PURE.replace("order.items", "order.zzzzz")
TWO_TYPOS = TYPO_FIELD.replace("summarize_order(order)", "summarize_order(ordr)")

TESTED = """
record Order { customer: Text items: Text }
record Summary { greeting: Text count: Number }

fn summarize_order(order: Order) -> Summary {
  return {
    greeting: "Hello, " + order.customer + "!",
    count: order.items.split(separator: ",").length()
  }
}

workflow main(order: Order) -> Summary {
  summary = summarize_order(order)
}

test "the count is the number of items" {
  result = main({customer: "Ada", items: "pen,book"})
  expect result.count == 2
}

test "the greeting names the customer" {
  result = main({customer: "Ada", items: "pen"})
  expect result.greeting == "Hello, Ada!"
}
"""

TESTED_WRONG = TESTED.replace("expect result.count == 2", "expect result.count == 3")

TESTED_UNANSWERED = """
record Ticket { text: Text }
record Routing { queue: Text }
model assistant
workflow main(ticket: Ticket) -> Routing {
  label = classify ticket.text into ["billing", "bug"]
  routing = {queue: label.label}
}
test "the queue follows the label" {
  result = main({text: "charged twice"})
  expect result.queue == "billing"
}
"""

CONTRACT = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "components"
        / "uppercase_python.component.json"
    ).read_text(encoding="utf-8")
)


def payload(name: str, **arguments: Any) -> dict[str, Any]:
    return call_tool(name, arguments)["structuredContent"]


class ToolTests(unittest.TestCase):
    def test_the_guide_is_served(self) -> None:
        answer = payload("kosika_guide")
        self.assertTrue(answer["ok"])
        self.assertIn("Kosika for AI authors", answer["guide"])

    def test_check_accepts_a_valid_program(self) -> None:
        self.assertEqual(
            payload("kosika_check", source=PURE), {"ok": True, "problems": []}
        )

    def test_check_reports_code_message_and_line(self) -> None:
        answer = payload(
            "kosika_check", source=PURE.replace("order.items", "order.itms")
        )
        self.assertFalse(answer["ok"])
        problem = answer["problems"][0]
        self.assertEqual(problem["code"], "check/field-access")
        self.assertIn("Did you mean 'items'?", problem["message"])
        self.assertIsInstance(problem["line"], int)

    def test_run_returns_the_result_and_a_replayable_trace(self) -> None:
        answer = payload("kosika_run", source=PURE, inputs=ORDER)
        self.assertEqual(answer["result"], {"greeting": "Hello, Ada!", "count": 2})
        replayed = payload("kosika_replay", trace=answer["trace"])
        self.assertEqual(replayed, {"ok": True, "result": answer["result"]})

    def test_run_uses_only_the_answers_it_is_given(self) -> None:
        ticket = {"ticket": {"text": "charged twice"}}
        missing = payload("kosika_run", source=THINKING, inputs=ticket)
        self.assertEqual((missing["ok"], missing["stage"]), (False, "run"))
        self.assertIn("no authorized host binding", missing["message"])
        answered = payload(
            "kosika_run",
            source=THINKING,
            inputs=ticket,
            model_answers={"assistant": [{"label": "billing", "confidence": 0.9}]},
        )
        self.assertEqual(answered["result"], {"queue": "billing", "sure": True})
        outside = payload(
            "kosika_run",
            source=THINKING,
            inputs=ticket,
            model_answers={"assistant": [{"label": "refunds", "confidence": 0.9}]},
        )
        self.assertIn("not one of the choices", outside["message"])

    def test_run_scripts_tools_and_never_reaches_a_real_one(self) -> None:
        city = {"city": {"name": "Lisbon"}}
        unbound = payload("kosika_run", source=TOOLED, inputs=city)
        self.assertFalse(unbound["ok"])
        single = payload(
            "kosika_run",
            source=TOOLED,
            inputs=city,
            tool_answers={"get_weather": {"celsius": 21}},
        )
        self.assertEqual(single["result"], {"celsius": 21})
        listed = payload(
            "kosika_run",
            source=TOOLED,
            inputs=city,
            tool_answers={"get_weather": [{"celsius": 5}]},
        )
        self.assertEqual(listed["result"], {"celsius": 5})

    def test_run_stops_at_the_check_stage_for_an_invalid_program(self) -> None:
        answer = payload("kosika_run", source="record Broken {", inputs={})
        self.assertEqual((answer["ok"], answer["stage"]), (False, "check"))
        self.assertTrue(answer["problems"])

    def test_run_reports_bad_inputs_and_unknown_workflows(self) -> None:
        self.assertIn(
            "requires input",
            payload("kosika_run", source=PURE, inputs={})["message"],
        )
        wrong = payload("kosika_run", source=PURE, inputs=ORDER, workflow="other")
        self.assertIn("Unknown workflow", wrong["message"])

    def test_replay_rejects_text_that_is_not_a_trace(self) -> None:
        answer = payload("kosika_replay", trace="not a trace")
        self.assertEqual((answer["ok"], answer["stage"]), (False, "replay"))

    def test_map_in_three_formats(self) -> None:
        self.assertIn("workflow main", payload("kosika_map", source=PURE)["map"])
        self.assertIn(
            "main", payload("kosika_map", source=PURE, format="mermaid")["map"]
        )
        self.assertIn("nodes", payload("kosika_map", source=PURE, format="json")["map"])
        self.assertFalse(payload("kosika_map", source="record X {")["ok"])

    def test_declare_renders_the_contract_it_is_given(self) -> None:
        answer = payload("kosika_declare", contract=CONTRACT)
        self.assertTrue(answer["ok"])
        self.assertIn("tool uppercase_python {", answer["declaration"])
        self.assertIn(
            'source mcp "component:example.uppercase-python"', answer["declaration"]
        )
        self.assertIn("record UppercasePythonInput {", answer["declaration"])
        self.assertTrue(payload("kosika_check", source=answer["declaration"])["ok"])

    def test_declare_says_why_a_contract_cannot_be_shown(self) -> None:
        broken = dict(CONTRACT)
        del broken["schema"]
        invalid = payload("kosika_declare", contract=broken)
        self.assertEqual((invalid["ok"], invalid["stage"]), (False, "contract"))

        unshowable = json.loads(json.dumps(CONTRACT))
        unshowable["inputSchema"]["properties"]["text"] = {"type": "integer"}
        unsupported = payload("kosika_declare", contract=unshowable)
        self.assertEqual(
            (unsupported["ok"], unsupported["stage"]), (False, "declaration")
        )
        self.assertIn("integer", unsupported["message"])

    def test_test_runs_the_programs_own_tests(self) -> None:
        answer = payload("kosika_test", source=TESTED)
        self.assertTrue(answer["ok"])
        self.assertEqual(
            [(row["name"], row["passed"]) for row in answer["tests"]],
            [
                ("the count is the number of items", True),
                ("the greeting names the customer", True),
            ],
        )
        self.assertEqual((answer["passed"], answer["failed"]), (2, 0))

    def test_test_reports_a_failing_expectation(self) -> None:
        answer = payload("kosika_test", source=TESTED_WRONG)
        self.assertFalse(answer["ok"])
        failed = [row for row in answer["tests"] if not row["passed"]]
        self.assertEqual(len(failed), 1)
        self.assertIn("3", failed[0]["message"])
        self.assertEqual(answer["failed"], 1)

    def test_test_never_reaches_a_real_model(self) -> None:
        answer = payload("kosika_test", source=TESTED_UNANSWERED)
        self.assertFalse(answer["ok"])
        self.assertIn("no response left", answer["tests"][0]["message"])

    def test_test_stops_at_the_check_stage_and_names_one_test(self) -> None:
        broken = payload("kosika_test", source="record Broken {")
        self.assertEqual((broken["ok"], broken["stage"]), (False, "check"))
        one = payload(
            "kosika_test", source=TESTED, only="the greeting names the customer"
        )
        self.assertEqual(
            [row["name"] for row in one["tests"]], ["the greeting names the customer"]
        )
        unknown = payload("kosika_test", source=TESTED, only="no such test")
        self.assertEqual((unknown["ok"], unknown["stage"]), (False, "test"))

    def test_repair_offers_the_typo_and_a_checked_patch(self) -> None:
        answer = payload("kosika_repair", source=TYPO_FIELD)
        self.assertFalse(answer["ok"])
        self.assertEqual(
            answer["edits"],
            [
                {
                    "code": "check/field-access",
                    "line": 8,
                    "column": 18,
                    "remove": "itms",
                    "with": "items",
                }
            ],
        )
        self.assertIn("order.items", answer["patched"])
        self.assertTrue(payload("kosika_check", source=answer["patched"])["ok"])

    def test_repair_renames_an_unknown_variable(self) -> None:
        answer = payload("kosika_repair", source=TYPO_VARIABLE)
        edit = answer["edits"][0]
        self.assertEqual((edit["remove"], edit["with"]), ("ordr", "order"))
        self.assertIn("summarize_order(order)", answer["patched"])

    def test_repair_applies_every_edit_it_can_verify(self) -> None:
        answer = payload("kosika_repair", source=TWO_TYPOS)
        self.assertEqual(len(answer["edits"]), 2)
        self.assertTrue(payload("kosika_check", source=answer["patched"])["ok"])

    def test_repair_leaves_a_problem_it_cannot_name(self) -> None:
        answer = payload("kosika_repair", source=NO_CLOSE_NAME)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["edits"], [])
        self.assertNotIn("patched", answer)

    def test_repair_withholds_a_patch_that_does_not_check_clean(self) -> None:
        answer = payload(
            "kosika_repair",
            source=TYPO_FIELD
            + """
workflow second(order: Order) -> Summary {
  wrong = order.zzzzz
}
""",
        )
        self.assertEqual(len(answer["edits"]), 1)
        self.assertNotIn("patched", answer)

    def test_repair_is_quiet_for_a_clean_program(self) -> None:
        self.assertEqual(
            payload("kosika_repair", source=PURE),
            {"ok": True, "problems": [], "edits": []},
        )

    def test_bad_arguments_are_tool_errors(self) -> None:
        cases: list[tuple[str, dict[str, Any]]] = [
            ("kosika_check", {}),
            ("kosika_check", {"source": "   "}),
            ("kosika_check", {"source": "x" * (MAX_SOURCE_BYTES + 1)}),
            ("kosika_check", {"source": PURE, "path": "C:/secret"}),
            ("kosika_run", {"source": PURE, "inputs": []}),
            ("kosika_run", {"source": PURE, "workflow": 3}),
            ("kosika_run", {"source": PURE, "model_answers": {"m": {"text": "x"}}}),
            ("kosika_map", {"source": PURE, "format": "svg"}),
            ("kosika_declare", {}),
            ("kosika_declare", {"contract": "not a record"}),
            ("kosika_declare", {"contract": CONTRACT, "path": "C:/secret"}),
            ("kosika_test", {}),
            ("kosika_test", {"source": TESTED, "only": 3}),
            ("kosika_repair", {}),
            ("kosika_repair", {"source": PURE, "path": "C:/secret"}),
            ("kosika_guide", {"extra": 1}),
        ]
        for name, arguments in cases:
            with self.subTest(name=name, arguments=sorted(arguments)):
                answer = call_tool(name, arguments)
                self.assertTrue(answer["isError"])
                self.assertEqual(answer["structuredContent"]["stage"], "input")

    def test_no_tool_accepts_a_file_path_or_address(self) -> None:
        for tool in TOOLS:
            properties = tool["inputSchema"]["properties"]
            self.assertFalse(tool["inputSchema"].get("additionalProperties", True))
            self.assertFalse({"path", "file", "url", "command"} & set(properties))


class ProtocolTests(unittest.TestCase):
    def exchange(self, *messages: Any) -> list[dict[str, Any]]:
        lines = "".join(
            (message if isinstance(message, str) else json.dumps(message)) + "\n"
            for message in messages
        )
        output = io.StringIO()
        serve(io.StringIO(lines), output)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_initialize_list_and_call(self) -> None:
        answers = self.exchange(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "kosika_check", "arguments": {"source": PURE}},
            },
            {"jsonrpc": "2.0", "id": 4, "method": "ping"},
        )
        self.assertEqual([answer["id"] for answer in answers], [1, 2, 3, 4])
        self.assertEqual(answers[0]["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(answers[0]["result"]["serverInfo"]["name"], "kosika")
        self.assertEqual(len(answers[1]["result"]["tools"]), 8)
        self.assertTrue(answers[2]["result"]["structuredContent"]["ok"])

    def test_protocol_errors_have_json_rpc_codes(self) -> None:
        answers = self.exchange(
            "this is not json",
            {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "delete_everything", "arguments": {}},
            },
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "kosika_check", "arguments": "source"},
            },
            "",
        )
        self.assertEqual(
            [answer["error"]["code"] for answer in answers],
            [-32700, -32601, -32602, -32602],
        )

    def test_handle_refuses_unknown_methods(self) -> None:
        with self.assertRaises(LookupError):
            handle({"method": "sampling/createMessage"})

    def test_a_real_client_can_drive_the_server_process(self) -> None:
        with MCPStdioClient([sys.executable, "-m", "kosika.mcp_server"]) as client:
            names = {tool.name for tool in client.list_tools()}
            self.assertEqual(names, {tool["name"] for tool in TOOLS})
            ran = client.call_tool("kosika_run", {"source": PURE, "inputs": ORDER})
            self.assertEqual(ran["result"]["count"], 2)
            replayed = client.call_tool("kosika_replay", {"trace": ran["trace"]})
            self.assertEqual(replayed["result"], ran["result"])
            with self.assertRaises(MCPToolError):
                client.call_tool("kosika_check", {})


if __name__ == "__main__":
    unittest.main()
