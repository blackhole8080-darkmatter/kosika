"""A step that chooses between calls, `kosika chat`, and narrowing over `and`."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.ast import program_payload
from kosika.chat import ChatError, answer_text, chat_loop, message_parameter
from kosika.checker import CheckError, check
from kosika.memory import bind_memory_tools
from kosika.mindmap import build_mindmap
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import ParseError, parse
from kosika.runtime import replay, run

SOURCE = """
record Msg { text: Text }
record Said { text: Text }
record Count { n: Number }

model assistant
memory counts of Count

fn kind_of(msg: Msg) -> Said {
  let n = msg.text.to_number()
  return {text: if n.exists() and n > 0 { "number" } else if msg.text.contains("?") { "ask" } else { "other" }}
}

fn say(text: Text) -> Said {
  return {text: text}
}

fn bump(known: CountsRecalled) -> Count {
  return {n: if known.found { known.value.value().n + 1 } else { 1 }}
}

fn told(count: Count) -> Said {
  return {text: "turn " + count.n}
}

workflow counted(msg: Msg) -> Said {
  known = recall counts under "me"
  count = bump(known)
  saved = remember count in counts under "me"
  said  = told(count)
}

workflow answered(msg: Msg) -> Said {
  reply = use_model assistant(msg.text)
  said  = say(reply.text)
}

workflow main(msg: Msg) -> Said {
  kind = kind_of(msg)
  said = match kind.text {
    case "number" { call counted(msg) }
    case "ask" { call answered(msg) }
    case _ { say("unclear") }
  }
}
"""


def checked(source: str = SOURCE):
    program = parse(source)
    check(program)
    return program


def error_of(source: str) -> str:
    try:
        checked(source)
    except ValueError as exc:
        return str(exc)
    raise AssertionError("expected the program to be rejected")


def scripted(*texts: str) -> ScriptedModelProvider:
    return ScriptedModelProvider(
        [ModelResponse(content=json.dumps({"text": text})) for text in texts]
    )


class StepMatchTests(unittest.TestCase):
    def go(
        self, text: str, provider: ScriptedModelProvider, trace: Path | None = None
    ) -> Any:
        program = checked()
        return run(
            program,
            "main",
            trace,
            inputs={"msg": {"text": text}},
            model_bindings={"assistant": provider},
            tool_bindings=bind_memory_tools(program, None, store=self.store),
        )

    def setUp(self) -> None:
        from kosika.memory import MemoryStore

        self.store = MemoryStore()

    def test_only_the_chosen_call_is_made(self) -> None:
        provider = scripted("Paris.")
        self.assertEqual(self.go("hello", provider), {"text": "unclear"})
        self.assertEqual(self.go("7", provider), {"text": "turn 1"})
        self.assertEqual(len(provider.requests), 0)
        self.assertEqual(self.go("Capital of France?", provider), {"text": "Paris."})
        self.assertEqual(len(provider.requests), 1)

    def test_every_branch_must_be_authorized_before_the_run(self) -> None:
        program = checked()
        with self.assertRaisesRegex(PermissionError, "Memory 'counts' was not granted"):
            run(
                program,
                "main",
                None,
                inputs={"msg": {"text": "hello"}},
                model_bindings={"assistant": scripted()},
            )

    def test_a_chosen_branch_replays(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "t.jsonl"
            result = self.go("3", scripted(), trace)
            self.assertEqual(replay(trace), result)

    def test_the_mind_map_draws_every_branch(self) -> None:
        labels = {
            edge["label"]
            for edge in build_mindmap(checked())["edges"]
            if "when" in edge["label"] or edge["label"].startswith("otherwise")
        }
        self.assertEqual(
            labels,
            {'when "number": calls', 'when "ask": calls', "otherwise: computes"},
        )

    def test_branches_must_agree_and_end_with_a_default(self) -> None:
        self.assertIn(
            "must end with exactly one `case _`",
            error_of(SOURCE.replace('    case _ { say("unclear") }\n', "")),
        )
        self.assertIn(
            "lists a value twice",
            error_of(SOURCE.replace('case "ask"', 'case "number"')),
        )
        self.assertIn(
            "return different types",
            error_of(
                SOURCE.replace('case _ { say("unclear") }', "case _ { bump_none(msg) }")
                + "\nfn bump_none(msg: Msg) -> Count {\n  return {n: 0}\n}\n"
            ),
        )
        self.assertIn(
            "match expects Text, Number, or Bool",
            error_of(SOURCE.replace("match kind.text {", "match kind {")),
        )

    def test_a_branch_is_checked_like_any_call(self) -> None:
        with self.assertRaises((CheckError, ParseError)):
            checked(SOURCE.replace('case _ { say("unclear") }', "case _ { say(5) }"))

    def test_a_program_without_a_step_match_keeps_its_hash_shape(self) -> None:
        plain = parse(
            "record A { x: Text }\nfn f(a: A) -> A {\n  return a\n}\n"
            "workflow main(a: A) -> A {\n  b = f(a)\n}\n"
        )
        self.assertNotIn("branches", json.dumps(program_payload(plain)))
        self.assertIn('"branches"', json.dumps(program_payload(checked())))


class NarrowingTests(unittest.TestCase):
    def test_the_right_of_and_uses_what_the_left_proved(self) -> None:
        checked()  # kind_of uses `n.exists() and n > 0`

    def test_the_right_of_or_is_not_narrowed_by_exists(self) -> None:
        self.assertIn(
            "Number?",
            error_of(SOURCE.replace("n.exists() and n > 0", "n.exists() or n > 0")),
        )

    def test_not_exists_or_uses_the_value(self) -> None:
        checked(
            SOURCE.replace(
                'if n.exists() and n > 0 { "number" }',
                'if not n.exists() or n <= 0 { "other" } else if true { "number" }',
            )
        )


class ChatTests(unittest.TestCase):
    def talk(self, lines: list[str], provider: ScriptedModelProvider) -> list[str]:
        program = checked()
        pending = iter(lines)
        said: list[str] = []
        from kosika.memory import MemoryStore

        answered = chat_loop(
            program,
            "main",
            model_bindings={"assistant": provider},
            tool_bindings=bind_memory_tools(program, None, store=MemoryStore()),
            read=lambda: next(pending, None),
            write=said.append,
            show_time=False,
        )
        self.answered = answered
        return said

    def test_each_line_is_one_run_and_memory_carries_over(self) -> None:
        said = self.talk(
            ["1", "", "2", "Capital?", "exit", "never read"], scripted("Paris.")
        )
        self.assertEqual(said, ["turn 1", "turn 2", "Paris."])
        self.assertEqual(self.answered, 3)

    def test_a_failed_turn_does_not_end_the_chat(self) -> None:
        said = self.talk(["First?", "5"], scripted())
        self.assertIn("that did not work", said[0])
        self.assertEqual(said[1], "turn 1")

    def test_the_end_of_input_ends_the_chat(self) -> None:
        self.assertEqual(self.talk(["hello"], scripted()), ["unclear"])

    def test_the_workflow_must_take_one_text_record(self) -> None:
        program = checked()
        self.assertEqual(message_parameter(program, "main"), ("msg", []))
        with self.assertRaisesRegex(ChatError, "No workflow named 'nope'"):
            message_parameter(program, "nope")
        two = checked(
            SOURCE.replace(
                "record Msg { text: Text }", "record Msg { text: Text who: Text }"
            ).replace("msg.text.to_number()", "msg.text.to_number()")
        )
        with self.assertRaisesRegex(ChatError, "other fields"):
            message_parameter(two, "main")

    def test_a_result_without_text_is_shown_whole(self) -> None:
        self.assertEqual(answer_text({"text": "hi"}), "hi")
        self.assertEqual(answer_text({"n": 1}), '{"n": 1}')


if __name__ == "__main__":
    unittest.main()
