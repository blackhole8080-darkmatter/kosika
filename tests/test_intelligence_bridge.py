"""Regression tests for the intelligence bridge and live function result cache.

These guard the new runtime wiring (intelligence event ingestion and the
pure-function result cache) against regressions, and document the expected
behaviour of the opt-in `intelligence` and `function_cache` parameters on
`kosika.runtime.run()`.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kosika.parser import parse
from kosika.runtime import replay, run
from kosika.intelligence_runtime import FunctionResultCache, IntelligenceRecorder


SOURCE = """
record Person { name: Text }
record Greeting { text: Text }

fn make_greeting(person: Person) -> Greeting {
  return {text: person.name}
}

workflow main(person: Person) -> Greeting {
  step prepare {
    let result: Greeting = make_greeting(person: person)
    return result
  }
}
"""


class FunctionResultCacheTests(unittest.TestCase):
    def test_cache_miss_stores_result(self) -> None:
        cache = FunctionResultCache(max_entries=4)
        stats0 = cache.stats()
        self.assertEqual(stats0["entries"], 0)
        self.assertEqual(stats0["hits"], 0)
        self.assertEqual(stats0["misses"], 0)
        self.assertEqual(stats0["hit_rate"], 0.0)

        hit0 = cache.get("make_greeting", {"person": {"name": "Ada"}})
        self.assertIsNone(hit0)

        cache.put("make_greeting", {"person": {"name": "Ada"}}, {"text": "Ada"})
        self.assertEqual(cache.stats()["entries"], 1)

        hit1 = cache.get("make_greeting", {"person": {"name": "Ada"}})
        self.assertEqual(hit1, {"text": "Ada"})
        self.assertEqual(cache.stats()["hits"], 1)
        self.assertEqual(cache.stats()["misses"], 1)

    def test_different_arguments_are_distinct_keys(self) -> None:
        cache = FunctionResultCache(max_entries=4)
        cache.put("make_greeting", {"person": {"name": "Ada"}}, {"text": "Ada"})
        cache.put("make_greeting", {"person": {"name": "Lin"}}, {"text": "Lin"})

        self.assertEqual(
            cache.get("make_greeting", {"person": {"name": "Ada"}}),
            {"text": "Ada"},
        )
        self.assertEqual(
            cache.get("make_greeting", {"person": {"name": "Lin"}}),
            {"text": "Lin"},
        )

    def test_eviction_respects_bound(self) -> None:
        cache = FunctionResultCache(max_entries=2)
        cache.put("a", {"x": 1}, {"x": 1})
        cache.put("b", {"x": 2}, {"x": 2})
        cache.put("c", {"x": 3}, {"x": 3})

        self.assertEqual(cache.stats()["entries"], 2)
        self.assertIsNone(cache.get("a", {"x": 1}))

    def test_clear_empties_cache_and_stats(self) -> None:
        cache = FunctionResultCache(max_entries=4)
        cache.put("a", {"x": 1}, {"x": 1})
        cache.put("b", {"x": 2}, {"x": 2})
        cache.clear()
        self.assertEqual(cache.stats()["entries"], 0)
        self.assertIsNone(cache.get("a", {"x": 1}))

    def test_canonical_arguments_are_deterministic(self) -> None:
        from kosika.intelligence_runtime import _canonical_arguments

        args1 = _canonical_arguments({"person": {"name": "Ada"}})
        args2 = _canonical_arguments({"name": "Ada", "person": {"name": "Ada"}})
        # Different key sets produce different bytes; same key sets in any
        # order produce the same bytes.
        args3 = _canonical_arguments({"person": {"name": "Ada"}})
        self.assertEqual(args1, args3)

    def test_concurrent_access_does_not_raise(self) -> None:
        import threading

        cache = FunctionResultCache(max_entries=64)
        errors: list[BaseException] = []

        def writer(i: int) -> None:
            try:
                cache.put(f"fn{i}", {"i": i}, {"i": i})
            except BaseException as exc:
                errors.append(exc)

        def reader(i: int) -> None:
            try:
                cache.get(f"fn{i}", {"i": i})
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(24)]
        threads += [threading.Thread(target=reader, args=(i,)) for i in range(24)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertLessEqual(cache.stats()["entries"], 64)


class IntelligenceRecorderTests(unittest.TestCase):
    def test_ingest_event_applies_record_on_run_complete(self) -> None:
        recorder = IntelligenceRecorder()
        recorder.record_event({
            "event": "run_start",
            "run_id": "r1",
            "program": "demo",
            "workflow": "main",
        })
        recorder.record_event({
            "event": "run_complete",
            "run_id": "r1",
            "workflow": "main",
            "result": {"ok": True},
            "duration": 0.25,
        })

        insights = recorder.insights
        self.assertIsInstance(insights, dict)
        self.assertIn("operations_analyzed", insights)
        self.assertGreaterEqual(insights["operations_analyzed"], 0)

    def test_pattern_extractor_populates_after_repeated_operations(self) -> None:
        recorder = IntelligenceRecorder()
        for _ in range(7):
            recorder.record_event({
                "event": "tool_call",
                "tool": "check_disk",
                "start_time": 0.0,
                "duration": 1.0,
            })
            recorder.record_event({
                "event": "tool_return",
                "tool": "check_disk",
                "start_time": 0.0,
                "duration": 1.0,
                "success": True,
            })

        patterns = recorder.patterns
        self.assertTrue(patterns)
        # Pattern keys are md5 hex digests; check the operation_type in
        # the pattern values instead.
        self.assertTrue(any("check_disk" in v.get("operation_type", "")
                            for v in patterns.values()))

    def test_insights_reports_operations_analyzed(self) -> None:
        recorder = IntelligenceRecorder()
        for _ in range(6):
            recorder.record_event({
                "event": "model_call",
                "model": "gpt-4",
                "start_time": 0.0,
                "duration": 0.5,
            })
            recorder.record_event({
                "event": "model_return",
                "model": "gpt-4",
                "start_time": 0.0,
                "duration": 0.5,
                "success": True,
            })

        insights = recorder.insights
        self.assertGreaterEqual(insights["operations_analyzed"], 1)
        self.assertTrue(any("gpt-4" in op["operation"] for op in insights["top_operations"]))

    def test_analyze_returns_operation_pattern_and_confidence(self) -> None:
        recorder = IntelligenceRecorder()
        for _ in range(8):
            recorder.record_event({
                "event": "agent_call",
                "agent": "monitor",
                "start_time": 0.0,
                "duration": 0.1,
            })
            recorder.record_event({
                "event": "agent_return",
                "agent": "monitor",
                "start_time": 0.0,
                "duration": 0.1,
                "success": True,
            })

        analysis = recorder.analyze("agent/monitor")
        self.assertIsInstance(analysis, dict)
        self.assertIn("operation", analysis)
        self.assertIn("pattern", analysis)
        self.assertIn("confidence", analysis)

    def test_function_cache_hits_on_repeated_deterministic_calls(self) -> None:
        program = parse(SOURCE)
        cache = FunctionResultCache(max_entries=16)

        result1 = run(program, "main", None, inputs={"person": {"name": "Ada"}}, function_cache=cache)
        self.assertEqual(result1, {"text": "Ada"})

        result2 = run(program, "main", None, inputs={"person": {"name": "Ada"}}, function_cache=cache)
        self.assertEqual(result2, {"text": "Ada"})

        stats = cache.stats()
        self.assertGreater(stats["hits"], 0)
        self.assertGreaterEqual(stats["entries"], 1)

    def test_function_cache_respects_different_inputs(self) -> None:
        program = parse(SOURCE)
        cache = FunctionResultCache(max_entries=16)

        run(program, "main", None, inputs={"person": {"name": "Ada"}}, function_cache=cache)
        run(program, "main", None, inputs={"person": {"name": "Lin"}}, function_cache=cache)

        self.assertGreaterEqual(cache.stats()["entries"], 2)

    def test_intelligence_recorder_receives_function_events_from_real_run(self) -> None:
        program = parse(SOURCE)
        recorder = IntelligenceRecorder()

        for _ in range(6):
            run(
                program,
                "main",
                None,
                inputs={"person": {"name": "Ada"}},
                intelligence=recorder,
            )

        # After 6 runs the recorder has seen 6 function_call/function_return
        # pairs for "function/make_greeting", enough for the pattern analyzer
        # to surface a pattern.
        insights = recorder.insights
        self.assertIn("operations_analyzed", insights)
        self.assertGreaterEqual(insights["operations_analyzed"], 1)

        patterns = recorder.patterns
        self.assertTrue(patterns)
        self.assertTrue(any("make_greeting" in v.get("operation_type", "")
                            for v in patterns.values()))

    def test_repeated_runs_produce_patterns_and_cache_hits(self) -> None:
        program = parse(SOURCE)
        cache = FunctionResultCache(max_entries=16)
        recorder = IntelligenceRecorder()

        for _ in range(6):
            run(
                program,
                "main",
                None,
                inputs={"person": {"name": "Ada"}},
                function_cache=cache,
                intelligence=recorder,
            )

        stats = cache.stats()
        self.assertGreater(stats["hits"], 0)

        insights = recorder.insights
        self.assertGreaterEqual(insights["operations_analyzed"], 1)
        self.assertTrue(any("make_greeting" in op["operation"] for op in insights["top_operations"]))


class ReplayWithIntelligenceTests(unittest.TestCase):
    """Verify that enabling intelligence / cache does not break replayability."""

    def test_replay_still_works_after_intelligence_run(self) -> None:
        program = parse(SOURCE)
        recorder = IntelligenceRecorder()

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "intel.jsonl"
            result = run(
                program,
                "main",
                trace,
                inputs={"person": {"name": "Ada"}},
                intelligence=recorder,
            )
            self.assertEqual(result, {"text": "Ada"})
            self.assertEqual(replay(trace), result)

    def test_replay_still_works_after_cached_run(self) -> None:
        program = parse(SOURCE)
        cache = FunctionResultCache(max_entries=16)

        with tempfile.TemporaryDirectory() as directory:
            trace = Path(directory) / "cached.jsonl"
            result = run(
                program,
                "main",
                trace,
                inputs={"person": {"name": "Ada"}},
                function_cache=cache,
            )
            self.assertEqual(result, {"text": "Ada"})
            self.assertEqual(replay(trace), result)


if __name__ == "__main__":
    unittest.main()
