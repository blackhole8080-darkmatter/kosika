"""`kosika new` and the one-flag `--model` run path."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from kosika import cli
from kosika.model import ModelResponse, ScriptedModelProvider
from kosika.modules import load_program
from kosika.runtime import replay
from kosika.starter import StarterError, create_starter, starter_files

ROOT = Path(__file__).parents[1]
HELLO = ROOT / "examples" / "hello_world.kos"


def invoke(*arguments: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", ["kosika", *arguments]):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main()
    return code, out.getvalue(), err.getvalue()


def binding_args(**overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {
        "model_fixture": None,
        "model_binding": None,
        "model": None,
        "model_url": None,
        "model_protocol": "openai",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class StarterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_the_starter_checks_runs_offline_and_replays(self) -> None:
        project = self.root / "hello"
        code, out, _ = invoke("new", str(project))
        self.assertEqual(code, 0)
        self.assertIn("main.kos", out)
        self.assertEqual(
            sorted(path.name for path in project.iterdir()),
            sorted(starter_files("hello")),
        )
        trace = project / "trace.jsonl"
        code, out, _ = invoke(
            "run",
            str(project / "main.kos"),
            "--input",
            str(project / "input.json"),
            "--model-fixture",
            str(project / "responses.json"),
            "--trace",
            str(trace),
        )
        self.assertEqual(code, 0)
        expected = json.loads((project / "responses.json").read_text("utf-8"))
        self.assertEqual(replay(trace), expected["assistant"][0])

    def test_the_starter_has_a_test_and_accepts_its_folder(self) -> None:
        project = self.root / "hello"
        create_starter(project)
        code, out, err = invoke("test", str(project))
        self.assertEqual(code, 0, err)
        self.assertIn("1 passed, 0 failed", out)

    def test_test_explains_a_folder_without_main(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        code, _, err = invoke("test", str(empty))
        self.assertEqual(code, 2)
        self.assertIn("folder without a main.kos file", err)

    def test_the_researcher_starter_has_a_complete_offline_path(self) -> None:
        project = self.root / "lab"
        code, out, err = invoke("new", str(project), "--kind", "researcher")
        self.assertEqual(code, 0, err)
        self.assertIn("responses.json", out)
        code, _, err = invoke("check", str(project / "main.kos"))
        self.assertEqual(code, 0, err)
        code, out, err = invoke("test", str(project))
        self.assertEqual(code, 0, err)
        self.assertIn("4 passed, 0 failed", out)
        trace = project / "run.jsonl"
        code, _, err = invoke(
            "run",
            str(project / "main.kos"),
            "--input",
            str(project / "input.json"),
            "--model-fixture",
            str(project / "responses.json"),
            "--trace",
            str(trace),
        )
        self.assertEqual(code, 0, err)
        report = replay(trace)
        self.assertEqual(report["documents"], 2)
        self.assertEqual(report["checked"], 2)
        self.assertEqual(report["unsupported"], 1)
        self.assertTrue(report["findings"][0]["verified"])
        self.assertFalse(report["findings"][1]["verified"])

    def test_the_readme_names_the_project(self) -> None:
        create_starter(self.root / "my-agent")
        readme = (self.root / "my-agent" / "README.md").read_text("utf-8")
        self.assertTrue(readme.startswith("# my-agent\n"))

    def test_an_existing_empty_directory_is_accepted(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual(len(create_starter(empty)), 4)

    def test_nothing_is_overwritten(self) -> None:
        project = self.root / "taken"
        project.mkdir()
        (project / "main.kos").write_text("keep me", encoding="utf-8")
        code, _, err = invoke("new", str(project))
        self.assertEqual(code, 2)
        self.assertIn("not empty", err)
        self.assertEqual((project / "main.kos").read_text("utf-8"), "keep me")

    def test_a_file_is_not_a_project_folder(self) -> None:
        target = self.root / "file.txt"
        target.write_text("x", encoding="utf-8")
        with self.assertRaises(StarterError):
            create_starter(target)


class ModelFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.project = Path(self.directory.name) / "hello"
        create_starter(self.project)
        self.program = load_program(self.project / "main.kos")

    def bind(self, **overrides: Any) -> dict[str, Any]:
        with contextlib.redirect_stderr(io.StringIO()):
            return cli._workflow_model_bindings(binding_args(**overrides), self.program)

    def test_no_flag_grants_nothing(self) -> None:
        self.assertEqual(self.bind(), {})

    def test_model_alone_grants_every_declared_model(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KOSIKA_MODEL_URL", None)
            bindings = self.bind(model="local-model")
        self.assertEqual(list(bindings), ["assistant"])
        provider = bindings["assistant"]
        self.assertEqual(provider.model, "local-model")
        self.assertEqual(
            provider.endpoint, cli.DEFAULT_LOCAL_MODEL_URL + "/chat/completions"
        )

    def test_the_endpoint_follows_the_environment_then_the_flag(self) -> None:
        with patch.dict(os.environ, {"KOSIKA_MODEL_URL": "http://env:1/v1"}):
            from_env = self.bind(model="m")["assistant"]
            from_flag = self.bind(model="m", model_url="http://flag:2/v1")["assistant"]
        self.assertTrue(from_env.endpoint.startswith("http://env:1/v1"))
        self.assertTrue(from_flag.endpoint.startswith("http://flag:2/v1"))

    def test_the_ollama_protocol_uses_the_native_endpoint(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KOSIKA_MODEL_URL", None)
            provider = self.bind(model="m", model_protocol="ollama")["assistant"]
        self.assertEqual(provider.endpoint, "http://localhost:11434/api/chat")

    def test_an_explicit_binding_grants_only_that_model(self) -> None:
        bindings = self.bind(
            model_binding="assistant", model="m", model_url="http://x/v1"
        )
        self.assertEqual(list(bindings), ["assistant"])

    def test_incomplete_or_conflicting_flags_are_refused(self) -> None:
        cases: list[dict[str, Any]] = [
            {"model_url": "http://x/v1"},
            {"model_binding": "assistant"},
            {"model_binding": "assistant", "model": "m"},
            {"model_fixture": self.project / "responses.json", "model": "m"},
        ]
        for overrides in cases:
            with self.subTest(overrides=sorted(overrides)):
                with self.assertRaises(ValueError):
                    self.bind(**overrides)

    def test_model_without_a_declared_model_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "declares no model"):
            cli._workflow_model_bindings(binding_args(model="m"), load_program(HELLO))

    def test_a_missing_binding_explains_both_ways_forward(self) -> None:
        code, _, err = invoke(
            "run",
            str(self.project / "main.kos"),
            "--input",
            str(self.project / "input.json"),
            "--trace",
            str(self.project / "trace.jsonl"),
        )
        self.assertEqual(code, 2)
        self.assertIn("--model NAME", err)
        self.assertIn("--model-fixture FILE", err)

    def test_the_hint_is_only_for_a_missing_model_binding(self) -> None:
        self.assertEqual(cli._missing_model_hint(self.program, "other failure"), "")
        self.assertEqual(
            cli._missing_model_hint(
                load_program(HELLO), "Model 'x' has no authorized host binding"
            ),
            "",
        )

    def test_model_flag_runs_the_workflow_and_says_what_it_granted(self) -> None:
        answer = {"text": "from the provider"}

        def fake_provider(url: str, model: str, **_: Any) -> ScriptedModelProvider:
            self.assertEqual((url, model), ("http://x/v1", "m"))
            return ScriptedModelProvider([ModelResponse(content=json.dumps(answer))])

        trace = self.project / "trace.jsonl"
        with patch.object(cli, "OpenAICompatibleProvider", fake_provider):
            code, out, err = invoke(
                "run",
                str(self.project / "main.kos"),
                "--input",
                str(self.project / "input.json"),
                "--model",
                "m",
                "--model-url",
                "http://x/v1",
                "--trace",
                str(trace),
            )
        self.assertEqual(code, 0, err)
        self.assertIn("answers for: assistant", err)
        self.assertEqual(replay(trace), answer)


if __name__ == "__main__":
    unittest.main()
