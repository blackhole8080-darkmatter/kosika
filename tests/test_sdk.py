"""The Python SDK builds the same checked program as hand-written source."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kosika.approval_ledger import ApprovalLedger
from kosika.ast import ToolDecl
from kosika.checker import CheckError
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.parser import ParseError, parse
from kosika.runtime import replay
from kosika.sdk import App, Declared, SdkError, ref
from kosika.swarm import SwarmApproval, run_swarm, swarm_plan_hash

ROOT = Path(__file__).parents[1]

# The long form of the starter program, which is what the SDK renders.
STARTER_SOURCE = """
record Question { text: Text }
record Answer { text: Text }
model assistant { input: Question output: Answer }
workflow main(question: Question) -> Answer {
  step respond {
    let answer: Answer = use_model assistant(text: question.text)
    return answer
  }
}
"""
SWARM_EXAMPLE = ROOT / "examples" / "swarm_review.kos"
AGENT_EXAMPLE = ROOT / "examples" / "research_agent.kos"


def scripted(**answers: Any) -> dict[str, ScriptedModelProvider]:
    return {
        name: ScriptedModelProvider(
            [ModelResponse(content=json.dumps(item)) for item in items]
        )
        for name, items in answers.items()
    }


def starter_app() -> App:
    app = App()
    question = app.record("Question", text="Text")
    answer = app.record("Answer", text="Text")
    assistant = app.model("assistant", input=question, output=answer)
    main = app.workflow("main", output=answer, question=question)
    main.use_model("respond", assistant, bind="answer", text=ref("question.text"))
    return app


def swarm_app() -> App:
    app = App()
    draft = app.record("Draft", text="Text")
    review = app.record("Review", text="Text")
    read_docs = app.tool(
        "read_docs", source="local_mock", operation="search", output=draft
    )
    writer = app.model("writer_model", input=review, output=review)
    researcher = app.agent(
        "researcher",
        can=[read_docs],
        cannot=["write_files", "arbitrary_shell"],
        max_steps=4,
    )
    editor = app.agent("editor", model=writer, cannot=["arbitrary_shell"])
    gather = app.workflow("draft", output=draft)
    gather.execute("gather", researcher, read_docs, bind="result")
    revise = app.workflow("revise", output=review, draft=draft)
    revise.ask("rewrite", editor, bind="result", text=ref("draft.text"))
    board = app.workflow("review", output=review)
    gathered = board.call("start", gather, bind="gathered")
    board.call("evolve", revise, bind="revised", draft=gathered)
    app.swarm(
        "EditorialBoard", agents=[researcher, editor], workflow=board, max_rounds=3
    )
    return app


class SameProgramTests(unittest.TestCase):
    def test_the_starter_matches_its_hand_written_source(self) -> None:
        self.assertEqual(starter_app().build(), parse(STARTER_SOURCE))

    def test_a_swarm_program_matches_its_hand_written_source(self) -> None:
        self.assertEqual(
            swarm_app().build(), parse(SWARM_EXAMPLE.read_text(encoding="utf-8"))
        )

    def test_an_agent_program_matches_its_hand_written_source(self) -> None:
        app = App()
        app.record("ResearchRequest", question="Text")
        search_request = app.record("SearchRequest", query="Text")
        hit = app.record("SearchHit", title="Text", excerpt="Text")
        results = app.record("SearchResults", hits=f"{hit}[]")
        app.record("ResearchAnswer", answer="Text", sources="Text[]")
        writer = app.model(
            "research_writer",
            input="ResearchRequest",
            output="ResearchAnswer",
            deployment="scripted-research-model",
            temperature=0.2,
            max_output_tokens=256,
            top_p=0.9,
            stop=["END"],
        )
        search = app.tool(
            "search_notes",
            source="local_notes",
            input=search_request,
            output=results,
        )
        app.agent(
            "researcher",
            model=writer,
            instructions=(
                "Use only the retrieved notes. Return concise evidence-backed JSON."
            ),
            can=[search],
            cannot=["write_files", "arbitrary_shell"],
            max_steps=3,
            max_tool_calls=2,
            max_model_calls=3,
        )
        self.assertEqual(app.build(), parse(AGENT_EXAMPLE.read_text(encoding="utf-8")))

    def test_source_is_deterministic_and_reparses(self) -> None:
        first, second = swarm_app().to_source(), swarm_app().to_source()
        self.assertEqual(first, second)
        self.assertEqual(parse(first), swarm_app().build())


class RunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_run_needs_an_explicit_binding(self) -> None:
        with self.assertRaisesRegex(PermissionError, "no authorized host binding"):
            starter_app().run(inputs={"question": {"text": "hi"}})

    def test_run_traces_and_replays(self) -> None:
        trace = self.root / "trace.jsonl"
        result = starter_app().run(
            inputs={"question": {"text": "hi"}},
            trace=trace,
            model_bindings=scripted(assistant=[{"text": "hello"}]),
        )
        self.assertEqual(result, {"text": "hello"})
        self.assertEqual(replay(trace), {"text": "hello"})

    def test_values_become_kosika_literals(self) -> None:
        app = App()
        inner = app.record("Inner", name="Text")
        request = app.record(
            "Request",
            tags="Text[]",
            inner=inner,
            score="Number",
            on="Bool",
            note="Text",
        )
        app.source("fn echo(request: Request) -> Request {\n  return request\n}")
        main = app.workflow("main", output=request)
        main.function(
            "make",
            "echo",
            returns=request,
            bind="made",
            request={
                "tags": ["a", 'quote"d'],
                "inner": {"name": "näme"},
                "score": -1.5,
                "on": True,
                "note": "line\nbreak",
            },
        )
        self.assertEqual(
            app.run(),
            {
                "tags": ["a", 'quote"d'],
                "inner": {"name": "näme"},
                "score": -1.5,
                "on": True,
                "note": "line\nbreak",
            },
        )

    def test_a_built_swarm_runs_under_approval(self) -> None:
        program = swarm_app().build()

        def read_docs(_tool: ToolDecl, _arguments: dict[str, Any]) -> Any:
            return {"text": "notes"}

        outcome = run_swarm(
            program,
            "EditorialBoard",
            approval=SwarmApproval(swarm_plan_hash(program, "EditorialBoard"), "aryan"),
            ledger=ApprovalLedger(self.root / "ledger.sqlite"),
            model_bindings=scripted(writer_model=[{"text": "a"}, {"text": "a"}]),
            tool_bindings={"read_docs": read_docs},
        )
        self.assertEqual(outcome.terminated_by, "fixed_point")

    def test_save_writes_checked_source_only(self) -> None:
        target = starter_app().save(self.root / "main.kos")
        self.assertEqual(
            parse(target.read_text(encoding="utf-8")), parse(STARTER_SOURCE)
        )
        with self.assertRaises(SdkError):
            starter_app().save(self.root / "main.txt")
        broken = App()
        broken.workflow("main", output="Missing").function("s", "nope")
        with self.assertRaises((CheckError, ParseError)):
            broken.save(self.root / "broken.kos")
        self.assertFalse((self.root / "broken.kos").exists())


class RefusalTests(unittest.TestCase):
    def test_the_checker_still_decides(self) -> None:
        app = App()
        draft = app.record("Draft", text="Text")
        tool = app.tool("read_docs", source="local_mock", output=draft)
        outsider = app.agent("outsider")
        app.workflow("main", output=draft).execute("go", outsider, tool)
        with self.assertRaises(CheckError):
            app.build()

    def test_bad_builder_calls_are_refused(self) -> None:
        app = App()
        record = app.record("R", text="Text")
        model = app.model("m", input=record, output=record)
        workflow = app.workflow("main", output=record, r=record)
        workflow.use_model("one", model, text="x")
        cases = {
            "duplicate record": lambda: app.record("R"),
            "bad identifier": lambda: app.record("not valid"),
            "wrong handle kind": lambda: app.agent("a", model=record),
            "record type from a model": lambda: app.record("S", f=model),
            "unknown model parameter": lambda: app.model(
                "m2", input=record, output=record, seed=1
            ),
            "unknown agent limit": lambda: app.agent("b", retries=2),
            "unknown access": lambda: app.tool(
                "t", source="s", output=record, access="write"
            ),
            "empty tool source": lambda: app.tool("u", source="", output=record),
            "duplicate step": lambda: workflow.use_model("one", model, text="x"),
            "rebinding a parameter": lambda: workflow.use_model("r", model, text="x"),
            "declaration as a value": lambda: workflow.use_model(
                "two", model, text=record
            ),
            "unsupported value": lambda: workflow.use_model("three", model, text={1}),
            "non-finite number": lambda: workflow.use_model(
                "four", model, text=float("inf")
            ),
            "empty ref": lambda: ref(" "),
            "multi-line ref": lambda: ref("a\nb"),
            "multi-line type": lambda: app.record("T", f="Text\n}"),
            "empty source": lambda: app.source(" "),
        }
        for label, action in cases.items():
            with self.subTest(label):
                with self.assertRaises(SdkError):
                    action()

    def test_a_workflow_without_steps_is_refused(self) -> None:
        app = App()
        app.workflow("main", output=app.record("R", text="Text"))
        with self.assertRaisesRegex(SdkError, "no steps"):
            app.to_source()

    def test_handles_print_as_their_name(self) -> None:
        self.assertEqual(str(Declared("record", "R")), "R")


if __name__ == "__main__":
    unittest.main()
