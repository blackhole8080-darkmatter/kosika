"""Tests for the served virtual model: the key, the quota, and the journal."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from kosika.model import ModelRequest, ModelResponse, ModelUsage
from kosika.virtual_serve import (
    GROUNDING_RULE,
    AnswerCache,
    Gateway,
    Meter,
    Quota,
    UsageJournal,
    address,
    citation_faults,
    claims_in,
    keys_from,
    make_server,
    new_key,
    report,
    unbacked_claims,
    unsupported_claims,
)


class StubSpecialist:
    """Answers once per call, and remembers what it was asked."""

    def __init__(self, content: str = "the answer", usage: ModelUsage | None = None) -> None:
        self.content = content
        self.usage = usage
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(content=self.content, usage=self.usage)


def _headers(key: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"} if key else {}


def _body(**overrides) -> bytes:
    payload = {
        "model": "kosika-selfdev",
        "messages": [{"role": "user", "content": "how does the loop decide?"}],
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.journal = UsageJournal(Path(self.folder.name) / "usage.jsonl")
        self.key = new_key()
        self.specialist = StubSpecialist()
        self.meter = Meter(self.journal, Quota(calls_per_day=2, tokens_per_day=10_000, tokens_per_call=500))
        self.gateway = Gateway(
            self.specialist,
            self.meter,
            name="kosika-selfdev",
            keys={self.key: "editor"},
            clock=lambda: 1_800_000_000.0,
        )

    def test_models_lists_the_profile(self) -> None:
        status, payload, _ = self.gateway.handle("GET", "/v1/models", {}, b"")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"][0]["id"], "kosika-selfdev")

    def test_a_request_without_a_key_is_refused(self) -> None:
        status, payload, _ = self.gateway.handle("POST", "/v1/chat/completions", {}, _body())
        self.assertEqual(status, 401)
        self.assertIn("no key", payload["error"]["message"])
        self.assertEqual(self.specialist.requests, [])

    def test_a_key_that_is_not_ours_is_refused(self) -> None:
        status, payload, _ = self.gateway.handle(
            "POST", "/v1/chat/completions", _headers("not-a-key"), _body()
        )
        self.assertEqual(status, 401)
        self.assertIn("not one of this server", payload["error"]["message"])

    def test_an_answer_is_the_openai_shape_and_is_charged(self) -> None:
        status, payload, headers = self.gateway.handle(
            "POST", "/v1/chat/completions", _headers(self.key), _body()
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["choices"][0]["message"]["content"], "the answer")
        self.assertEqual(payload["model"], "kosika-selfdev")
        self.assertTrue(payload["usage"]["estimated"], "no usage reported means an estimate")
        self.assertEqual(headers["X-Kosika-Calls-Remaining"], "1")
        calls, tokens = self.journal.spent("editor", self.meter.day())
        self.assertEqual(calls, 1)
        self.assertGreater(tokens, 0)

    def test_the_daily_call_allowance_is_enforced(self) -> None:
        for _ in range(2):
            status, _, _ = self.gateway.handle(
                "POST", "/v1/chat/completions", _headers(self.key), _body()
            )
            self.assertEqual(status, 200)
        status, payload, headers = self.gateway.handle(
            "POST", "/v1/chat/completions", _headers(self.key), _body()
        )
        self.assertEqual(status, 429)
        self.assertIn("all 2 calls", payload["error"]["message"])
        self.assertEqual(headers["X-Kosika-Calls-Remaining"], "0")
        self.assertEqual(len(self.specialist.requests), 2, "the model was not asked a third time")

    def test_one_call_cannot_ask_for_more_than_its_allowance(self) -> None:
        self.gateway.handle(
            "POST", "/v1/chat/completions", _headers(self.key), _body(max_tokens=99_999)
        )
        self.assertEqual(self.specialist.requests[0].max_output_tokens, 500)

    def test_reported_usage_is_used_rather_than_estimated(self) -> None:
        self.specialist.usage = ModelUsage(prompt_tokens=7, completion_tokens=3, total_tokens=10)
        _, payload, _ = self.gateway.handle(
            "POST", "/v1/chat/completions", _headers(self.key), _body()
        )
        self.assertEqual(payload["usage"]["total_tokens"], 10)
        self.assertFalse(payload["usage"]["estimated"])

    def test_a_bad_body_is_a_400_not_a_crash(self) -> None:
        status, payload, _ = self.gateway.handle(
            "POST", "/v1/chat/completions", _headers(self.key), b"not json"
        )
        self.assertEqual(status, 400)
        status, payload, _ = self.gateway.handle(
            "POST", "/v1/chat/completions", _headers(self.key), json.dumps({"messages": []}).encode()
        )
        self.assertEqual(status, 400)
        self.assertIn("non-empty", payload["error"]["message"])

    def test_a_provider_failure_is_a_502(self) -> None:
        class Broken:
            def complete(self, request: ModelRequest) -> ModelResponse:
                raise RuntimeError("the model is asleep")

        gateway = Gateway(Broken(), self.meter, keys={self.key: "editor"})
        status, payload, _ = gateway.handle(
            "POST", "/v1/chat/completions", _headers(self.key), _body()
        )
        self.assertEqual(status, 502)
        self.assertIn("the model is asleep", payload["error"]["message"])

    def test_a_new_day_starts_the_allowance_again(self) -> None:
        for _ in range(2):
            self.gateway.handle("POST", "/v1/chat/completions", _headers(self.key), _body())
        self.assertEqual(
            self.gateway.handle("POST", "/v1/chat/completions", _headers(self.key), _body())[0],
            429,
        )
        tomorrow = Gateway(
            self.specialist, self.meter, name="kosika-selfdev", keys={self.key: "editor"},
            clock=lambda: 1_800_000_000.0 + 86_401,
        )
        status, _, _ = tomorrow.handle(
            "POST", "/v1/chat/completions", _headers(self.key), _body()
        )
        # The meter decides the day, and it was built with the same clock as the
        # gateway in production; here the meter's own clock is what must move.
        self.specialist.requests.clear()
        fresh = Meter(self.journal, Quota(calls_per_day=2), clock=lambda: 1_800_000_000.0 + 86_401)
        self.assertIsNone(fresh.refuse("editor"))


class KeyTests(unittest.TestCase):
    def test_keys_are_read_from_pairs(self) -> None:
        self.assertEqual(
            keys_from("editor=abc,  bot=def"),
            {"abc": "editor", "def": "bot"},
        )

    def test_a_pair_without_a_key_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            keys_from("editor")

    def test_a_new_key_is_long_enough_to_be_a_secret(self) -> None:
        self.assertGreaterEqual(len(new_key()), 24)


class ServerTests(unittest.TestCase):
    def test_the_same_rules_over_http(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            journal = UsageJournal(Path(folder) / "usage.jsonl")
            key = new_key()
            meter = Meter(journal, Quota(calls_per_day=1))
            gateway = Gateway(
                StubSpecialist("served"), meter, name="kosika-selfdev", keys={key: "editor"}
            )
            server = make_server(gateway, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = address(server)

            with urllib.request.urlopen(base + "/v1/models", timeout=5) as reply:
                listed = json.loads(reply.read())
            self.assertEqual(listed["data"][0]["id"], "kosika-selfdev")

            request = urllib.request.Request(
                base + "/v1/chat/completions",
                data=_body(),
                headers={"Content-Type": "application/json", **_headers(key)},
            )
            with urllib.request.urlopen(request, timeout=5) as reply:
                answer = json.loads(reply.read())
            self.assertEqual(answer["choices"][0]["message"]["content"], "served")

            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(raised.exception.code, 429)
            raised.exception.close()


class CacheTests(unittest.TestCase):
    """A question already paid for is not paid for twice."""

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.journal = UsageJournal(self.root / "usage.jsonl")
        self.key = new_key()
        self.specialist = StubSpecialist("kept answer")
        self.cache = AnswerCache(self.root / "answers.json")
        self.gateway = self.make()

    def make(self, *, ground: bool = False, calls: int = 5, on_hit=None) -> Gateway:
        return Gateway(
            self.specialist,
            Meter(
                self.journal,
                Quota(calls_per_day=calls, tokens_per_day=100_000, tokens_per_call=500),
            ),
            name="kosika-selfdev",
            keys={self.key: "editor"},
            cache=self.cache,
            ground=ground,
            on_hit=on_hit,
            clock=lambda: 1_800_000_000.0,
        )

    def ask(self, gateway: Gateway | None = None, **overrides):
        return (gateway or self.gateway).handle(
            "POST", "/v1/chat/completions", _headers(self.key), _body(**overrides)
        )

    def test_the_same_question_is_asked_once(self) -> None:
        first = self.ask()
        second = self.ask()
        self.assertEqual(len(self.specialist.requests), 1, "the model was asked once")
        self.assertEqual(second[0], 200)
        self.assertEqual(
            second[1]["choices"][0]["message"]["content"],
            first[1]["choices"][0]["message"]["content"],
        )
        self.assertTrue(second[1]["usage"]["cached"])
        self.assertEqual(second[1]["kosika"]["cache"], "hit")
        self.assertEqual(second[2]["X-Kosika-Cache"], "hit")
        self.assertEqual(
            second[1]["usage"]["saved_tokens"], second[1]["usage"]["total_tokens"]
        )

    def test_a_repeat_uses_no_allowance(self) -> None:
        gateway = self.make(calls=1)
        self.ask(gateway)
        self.ask(gateway)
        third = self.ask(gateway)
        self.assertEqual(third[0], 200, "a kept answer is served after the allowance is spent")
        self.assertEqual(third[2]["X-Kosika-Calls-Remaining"], "0")
        fresh = self.ask(
            gateway, messages=[{"role": "user", "content": "a question not asked before"}]
        )
        self.assertEqual(fresh[0], 429, "a question that was not asked still costs allowance")

    def test_whitespace_and_case_do_not_make_a_new_question(self) -> None:
        self.ask()
        self.ask(
            messages=[{"role": "user", "content": "  How  does the LOOP decide? "}]
        )
        self.assertEqual(len(self.specialist.requests), 1)

    def test_a_different_question_is_a_new_call(self) -> None:
        self.ask()
        self.ask(messages=[{"role": "user", "content": "which files does it read?"}])
        self.assertEqual(len(self.specialist.requests), 2)

    def test_a_different_budget_is_a_new_call(self) -> None:
        self.ask(max_tokens=100)
        self.ask(max_tokens=200)
        self.assertEqual(len(self.specialist.requests), 2)

    def test_a_temperature_that_asks_for_variety_is_not_kept(self) -> None:
        self.ask(temperature=0.7)
        self.ask(temperature=0.7)
        self.assertEqual(len(self.specialist.requests), 2)
        self.assertEqual(len(self.cache), 0)

    def test_a_bad_key_never_reaches_the_cache(self) -> None:
        self.ask()
        status, _, _ = self.gateway.handle(
            "POST", "/v1/chat/completions", _headers("not-a-key"), _body()
        )
        self.assertEqual(status, 401)
        self.assertEqual(len(self.specialist.requests), 1)

    def test_a_kept_answer_survives_a_restart(self) -> None:
        self.ask()
        restarted = AnswerCache(self.root / "answers.json")
        self.assertEqual(len(restarted), 1, "the answer was written down")
        self.specialist = StubSpecialist("kept answer")
        again = self.make()
        status, payload, _ = self.ask(again)
        self.assertEqual(status, 200)
        self.assertEqual(payload["kosika"]["cache"], "hit")
        self.assertEqual(self.specialist.requests, [])


    def test_a_hit_is_counted_and_reported(self) -> None:
        seen: list[int] = []
        gateway = self.make(on_hit=seen.append)
        first = self.ask(gateway)
        self.ask(gateway)
        total = first[1]["usage"]["total_tokens"]
        self.assertEqual(gateway.hits, 1)
        self.assertEqual(gateway.saved_tokens, total)
        self.assertEqual(seen, [total], "the operator is told what the repeat saved")

    def test_a_hit_writes_down_what_it_saved(self) -> None:
        first = self.ask()
        self.ask()
        entries = self.journal.entries(day=self.gateway.meter.day())
        self.assertEqual(len(entries), 2)
        self.assertTrue(entries[1]["cached"])
        self.assertEqual(
            entries[1]["saved_tokens"], first[1]["usage"]["total_tokens"]
        )
        self.assertEqual(entries[1]["tokens"], 0, "a repeat spends no tokens")

    def test_a_kept_answer_expires(self) -> None:
        now = 1_800_000_000.0
        path = self.root / "aging.json"
        AnswerCache(path, max_age_seconds=3600, clock=lambda: now).put(
            "k", {"answer": "a"}
        )
        fresh = AnswerCache(path, max_age_seconds=3600, clock=lambda: now + 3500)
        self.assertIsNotNone(fresh.get("k"))
        stale = AnswerCache(path, max_age_seconds=3600, clock=lambda: now + 3700)
        self.assertIsNone(stale.get("k"), "an hour-old answer is not served")

    def test_an_answer_with_no_age_limit_is_kept(self) -> None:
        path = self.root / "forever.json"
        AnswerCache(path, max_age_seconds=0, clock=lambda: 1_800_000_000.0).put(
            "k", {"answer": "a"}
        )
        later = AnswerCache(path, max_age_seconds=0, clock=lambda: 1_900_000_000.0)
        self.assertIsNotNone(later.get("k"))


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.journal = UsageJournal(Path(self.folder.name) / "usage.jsonl")

    def charge(
        self,
        caller: str,
        *,
        prompt: int,
        completion: int,
        cached=False,
        saved=0,
        refused=False,
    ) -> None:
        meter = Meter(self.journal, Quota(), clock=lambda: 1_800_000_000.0)
        meter.charge(
            caller,
            model="kosika-selfdev",
            prompt_tokens=0 if cached else prompt,
            completion_tokens=0 if cached else completion,
            estimated=False,
            cached=cached,
            saved_tokens=saved,
            refused=refused,
        )

    def test_a_day_is_summed_per_caller(self) -> None:
        self.charge("editor", prompt=100, completion=20)
        self.charge("editor", prompt=100, completion=20)
        self.charge("editor", prompt=0, completion=0, cached=True, saved=120)
        self.charge("bot", prompt=50, completion=10)
        spends = {(item.day, item.caller): item for item in report(self.journal)}
        editor = spends[("2027-01-15", "editor")]
        self.assertEqual((editor.calls, editor.tokens), (2, 240))
        self.assertEqual((editor.repeats, editor.saved_tokens), (1, 120))
        self.assertEqual(spends[("2027-01-15", "bot")].calls, 1)

    def test_refusals_are_counted_without_hiding_the_cost(self) -> None:
        self.charge("editor", prompt=100, completion=20)
        self.charge("editor", prompt=100, completion=20, refused=True)
        spend = report(self.journal)[0]
        self.assertEqual(spend.calls, 2, "a refusal is still a call that was paid for")
        self.assertEqual(spend.tokens, 240)
        self.assertEqual(spend.refusals, 1)

    def test_only_the_requested_days_are_reported(self) -> None:
        self.charge("editor", prompt=100, completion=20)
        later = Meter(self.journal, Quota(), clock=lambda: 1_800_000_000.0 + 86_400)
        later.charge(
            "editor", model="kosika-selfdev", prompt_tokens=70, completion_tokens=10,
            estimated=False,
        )
        self.assertEqual(len(report(self.journal)), 2)
        self.assertEqual([item.day for item in report(self.journal, days=1)], ["2027-01-16"])


class SelfdevGroundingTests(unittest.TestCase):
    """A proposal that names what the profile never sent is not applied."""

    class Specialist:
        plan_identity = "test"

        def __init__(self, content: str, reference: str, sources: str = "") -> None:
            self.content = content
            self.reference = reference
            self.sources = sources
            self.requests: list[ModelRequest] = []

        def sources_text(self) -> str:
            return self.sources

        def context_for(self, request: ModelRequest) -> str:
            return self.reference

        def complete(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            return ModelResponse(content=self.content)

    def proposer(self, content: str, reference: str, sources: str = ""):
        from kosika.selfdev import VirtualModelProposer

        return VirtualModelProposer(self.Specialist(content, reference, sources))

    def test_an_invented_path_never_reaches_the_gate(self) -> None:
        from kosika.selfdev import ProposalError

        proposer = self.proposer(
            '{"edits": [], "note": "see tests/nope.py"}',
            "The gate compiles, imports and runs ruff.",
        )
        with self.assertRaises(ProposalError) as raised:
            proposer.propose(None, "do the task")
        self.assertIn("documents do not have", str(raised.exception))
        self.assertIn("tests/nope.py", str(raised.exception))
        self.assertEqual(
            proposer.provider.requests[0].messages[0].content,
            GROUNDING_RULE,
            "the model is told the rule before it answers",
        )

    def test_a_name_the_task_prompt_showed_is_grounded(self) -> None:
        from kosika.selfdev import Proposal

        proposal = self.proposer(
            '{"edits": [], "note": "change kosika/thing.py"}',
            "The gate compiles, imports and runs ruff.",
        ).propose(None, "The file below is kosika/thing.py, shown with its lines.")
        self.assertIsInstance(proposal, Proposal)

    def test_a_name_in_the_sources_is_grounded_when_retrieval_missed_it(self) -> None:
        from kosika.selfdev import Proposal

        proposal = self.proposer(
            '{"edits": [], "note": "change kosika/thing.py"}',
            "The gate compiles, imports and runs ruff.",
            sources="kosika/thing.py is a small module.",
        ).propose(None, "do the task")
        self.assertIsInstance(proposal, Proposal)

    def test_a_grounded_answer_is_a_proposal(self) -> None:
        from kosika.selfdev import Proposal

        proposal = self.proposer(
            '{"edits": [], "note": "nothing safe"}',
            "The gate compiles, imports and runs ruff.",
        ).propose(None, "do the task")
        self.assertIsInstance(proposal, Proposal)


class CitingStub(StubSpecialist):
    """A specialist that can say what it read, and resolve a cited line."""

    def __init__(
        self, content: str, reference: str, lines: dict[str, list[str]]
    ) -> None:
        super().__init__(content)
        self.reference = reference
        self.lines = lines

    def context_for(self, request: ModelRequest) -> str:
        return self.reference

    def line_at(self, source: str, line: int) -> str | None:
        rows = self.lines.get(source)
        return rows[line - 1] if rows and 1 <= line <= len(rows) else None


class CitationTests(unittest.TestCase):
    LINES = {
        "kosika/selfdev.py": ["", "", "A change is kept only if it passes ruff check."],
    }

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.journal = UsageJournal(Path(self.folder.name) / "usage.jsonl")
        self.key = new_key()

    def gateway(self, content: str, *, require_citations: bool = False) -> Gateway:
        specialist = CitingStub(
            content,
            "[kosika/selfdev.py:3; relevance 0.9] A change is kept only if it passes ruff check.",
            dict(self.LINES),
        )
        return Gateway(
            specialist,
            Meter(self.journal, Quota(calls_per_day=5, tokens_per_day=100_000)),
            name="kosika-selfdev",
            keys={self.key: "editor"},
            ground=True,
            require_citations=require_citations,
            clock=lambda: 1_800_000_000.0,
        )

    def ask(self, content: str, *, require_citations: bool = False):
        return self.gateway(content, require_citations=require_citations).handle(
            "POST", "/v1/chat/completions", _headers(self.key), _body()
        )

    def test_a_citation_that_resolves_passes(self) -> None:
        answer = "A change must pass ruff check [kosika/selfdev.py:3]."
        _, payload, _ = self.ask(answer)
        self.assertTrue(payload["kosika"]["grounded"])
        self.assertEqual(payload["kosika"]["bad_citations"], [])
        self.assertEqual(payload["choices"][0]["message"]["content"], answer)

    def test_a_line_that_does_not_exist_is_refused(self) -> None:
        _, payload, _ = self.ask("A change must pass ruff check [kosika/selfdev.py:99].")
        self.assertFalse(payload["kosika"]["grounded"])
        self.assertEqual(payload["kosika"]["bad_citations"], ["kosika/selfdev.py:99"])
        self.assertIn("do not resolve", payload["choices"][0]["message"]["content"])

    def test_a_quotation_not_on_its_line_is_refused(self) -> None:
        answer = (
            "A change must pass ruff [kosika/selfdev.py:3] "
            '"a change is kept if it compiles"'
        )
        _, payload, _ = self.ask(answer)
        self.assertFalse(payload["kosika"]["grounded"])
        self.assertEqual(payload["kosika"]["bad_citations"], ["kosika/selfdev.py:3"])

    def test_a_quotation_on_its_line_passes(self) -> None:
        answer = (
            "A change must pass ruff [kosika/selfdev.py:3] "
            '"kept only if it passes ruff check"'
        )
        _, payload, _ = self.ask(answer)
        self.assertTrue(payload["kosika"]["grounded"])

    def test_an_answer_with_no_citation_is_not_faulted_for_it(self) -> None:
        _, payload, _ = self.ask("A change must pass ruff check.")
        self.assertTrue(payload["kosika"]["grounded"])
        self.assertEqual(payload["kosika"]["bad_citations"], [])

    def test_a_bare_line_reference_is_counted_but_not_refused(self) -> None:
        answer = "A change must pass ruff check [kosika/selfdev.py:3]."
        _, payload, _ = self.ask(answer)
        self.assertTrue(payload["kosika"]["grounded"])
        self.assertEqual(
            payload["kosika"]["unquoted_citations"], ["kosika/selfdev.py:3"]
        )

    def test_a_quoted_citation_is_not_listed_as_unquoted(self) -> None:
        answer = (
            "A change must pass ruff [kosika/selfdev.py:3] "
            '"kept only if it passes ruff check"'
        )
        _, payload, _ = self.ask(answer)
        self.assertEqual(payload["kosika"]["unquoted_citations"], [])

    def test_a_named_file_with_no_citation_is_unbacked(self) -> None:
        def line_at(source: str, line: int) -> str | None:
            return "text" if source == "kosika/selfdev.py" else None

        self.assertEqual(
            unbacked_claims("It reads kosika/selfdev.py.", line_at),
            ("kosika/selfdev.py",),
        )

    def test_a_line_that_writes_the_name_out_backs_it(self) -> None:
        def line_at(source: str, line: int) -> str | None:
            return "the ledger is .kosika/selfdev/ledger.jsonl"

        self.assertEqual(
            unbacked_claims("It writes `ledger.jsonl` [a/b.py:4].", line_at),
            (),
        )

    def test_the_cited_file_itself_backs_a_path(self) -> None:
        def line_at(source: str, line: int) -> str | None:
            return "a change is kept only if it passes"

        self.assertEqual(
            unbacked_claims(
                "It is checked in kosika/selfdev.py [kosika/selfdev.py:3].", line_at
            ),
            (),
        )

    def test_a_flag_needs_a_line_that_shows_it(self) -> None:
        def line_at(source: str, line: int) -> str | None:
            return "run with --gate 'ruff check'"

        self.assertEqual(unbacked_claims("Pass `--gate` [a/b.py:4].", line_at), ())
        self.assertEqual(
            unbacked_claims("Pass `--answer-age-hours` [a/b.py:4].", line_at),
            ("--answer-age-hours",),
        )
        self.assertEqual(
            unbacked_claims("Pass `--gate`.", line_at),
            ("--gate",),
            "a flag with no citation at all is unbacked",
        )

    def test_an_answer_that_names_nothing_needs_no_citation(self) -> None:
        self.assertEqual(
            unbacked_claims("A change is kept when it compiles.", lambda s, line: "x"),
            (),
        )

    def test_a_named_file_with_no_citation_is_reported_by_default(self) -> None:
        answer = "A change must pass ruff check in kosika/selfdev.py."
        _, payload, _ = self.ask(answer)
        self.assertTrue(payload["kosika"]["grounded"])
        self.assertEqual(payload["kosika"]["unbacked"], ["kosika/selfdev.py"])
        self.assertEqual(
            payload["choices"][0]["message"]["content"],
            answer,
            "measured: refusing this refuses real names, so it is not the default",
        )

    def test_requiring_citations_refuses_an_unbacked_name(self) -> None:
        answer = "A change must pass ruff check in kosika/selfdev.py."
        _, payload, _ = self.ask(answer, require_citations=True)
        self.assertFalse(payload["kosika"]["grounded"])
        self.assertIn("no citation shows", payload["choices"][0]["message"]["content"])

    def test_a_named_file_with_a_showing_citation_passes(self) -> None:
        answer = "A change must pass ruff [kosika/selfdev.py:3]."
        _, payload, _ = self.ask(answer)
        self.assertTrue(payload["kosika"]["grounded"])
        self.assertEqual(payload["kosika"]["unbacked"], [])

    def test_a_citation_helper_finds_both_shapes(self) -> None:
        def line_at(source: str, line: int) -> str | None:
            return "text" if source == "a/b.py" and line == 4 else None

        faults = citation_faults(
            "see [a/b.py:4] and `a/b.py:9` and a/b.py:12", line_at
        )
        self.assertEqual(faults, ("a/b.py:9", "a/b.py:12"))


class GroundingStub(StubSpecialist):
    """A specialist that can say what it would put in front of the model."""

    def __init__(self, content: str, reference: str) -> None:
        super().__init__(content)
        self.reference = reference

    def context_for(self, request: ModelRequest) -> str:
        return self.reference


class ClaimTests(unittest.TestCase):
    def test_paths_flags_and_folders_are_claims(self) -> None:
        found = claims_in(
            "Run --gate 'ruff check', then look at .kosika/selfdev/ledger.jsonl "
            "and the session folder."
        )
        self.assertIn(".kosika/selfdev/ledger.jsonl", found)
        self.assertIn("--gate", found)
        self.assertIn("session", found)

    def test_a_claim_the_documents_name_is_supported(self) -> None:
        self.assertEqual(
            unsupported_claims(
                "The ledger is .kosika/selfdev/ledger.jsonl.",
                "selfdev keeps .kosika/selfdev/ledger.jsonl and closes it on exit.",
            ),
            (),
        )

    def test_a_path_made_of_named_parts_is_supported(self) -> None:
        self.assertEqual(
            unsupported_claims(
                "It writes `.kosika/selfdev/ledger.jsonl`.",
                "The state folder is .kosika under selfdev, and the file is ledger.jsonl.",
            ),
            (),
        )

    def test_a_bare_name_is_a_folder_only_when_the_documents_show_it_in_a_path(self) -> None:
        self.assertEqual(
            unsupported_claims("It goes in the `reports` folder.", "the state folder"),
            ("reports",),
        )
        self.assertEqual(
            unsupported_claims("It goes in the `reports` folder.", "it writes reports/ too"),
            (),
        )

    def test_a_name_in_the_sources_is_not_an_invention_when_retrieval_missed_it(self) -> None:
        answer = "It reads `docs/specialists/selfdev.json`."
        elsewhere = 'sources: [".."], and docs/specialists/selfdev.json names it.'
        self.assertEqual(
            unsupported_claims(answer, "The gate runs compilation and ruff.", elsewhere=elsewhere),
            (),
        )
        self.assertEqual(
            unsupported_claims(
                answer, "The gate runs compilation and ruff.", elsewhere="nothing here"
            ),
            ("docs/specialists/selfdev.json",),
        )

    def test_an_identifier_is_a_name_even_outside_a_path(self) -> None:
        self.assertEqual(
            unsupported_claims(
                "The budget is `max_context_chars`.",
                "The gate runs compilation and ruff.",
                elsewhere='"max_context_chars": 6000',
            ),
            (),
            "an underscored identifier in the sources is that name",
        )

    def test_the_rule_asks_for_a_quoted_path_not_the_word_source(self) -> None:
        self.assertIn('"the exact words on that line"', GROUNDING_RULE)
        self.assertNotIn("[source:line]", GROUNDING_RULE)

    def test_a_name_nowhere_in_the_documents_is_missing(self) -> None:
        self.assertEqual(
            unsupported_claims(
                "See `tests/test_cli_coverage.py`.",
                "The gate runs compilation, import and ruff.",
            ),
            ("tests/test_cli_coverage.py",),
        )


class GroundingTests(unittest.TestCase):
    REFERENCE = (
        "[src/selfdev.py:12; relevance 0.900]\n"
        "A change is kept only if it compiles and passes `ruff check`, and the "
        "ledger lives at .kosika/selfdev/ledger.jsonl."
    )
    ANSWER = (
        "A change must compile and pass `ruff check`; the ledger is "
        ".kosika/selfdev/ledger.jsonl."
    )

    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.journal = UsageJournal(self.root / "usage.jsonl")
        self.key = new_key()
        self.cache = AnswerCache(self.root / "answers.json")
        self.gateway = self.build(self.ANSWER)

    def build(
        self, answer: str, *, ground: bool = True, reference: str | None = None
    ) -> Gateway:
        self.specialist = GroundingStub(
            answer, self.REFERENCE if reference is None else reference
        )
        return Gateway(
            self.specialist,
            Meter(self.journal, Quota(calls_per_day=5, tokens_per_day=100_000)),
            name="kosika-selfdev",
            keys={self.key: "editor"},
            cache=self.cache,
            ground=ground,
            clock=lambda: 1_800_000_000.0,
        )

    def ask(self, gateway: Gateway | None = None, **overrides):
        return (gateway or self.gateway).handle(
            "POST", "/v1/chat/completions", _headers(self.key), _body(**overrides)
        )

    def test_an_answer_the_documents_support_passes(self) -> None:
        status, payload, _ = self.ask()
        self.assertEqual(status, 200)
        self.assertEqual(payload["choices"][0]["message"]["content"], self.ANSWER)
        self.assertTrue(payload["kosika"]["grounded"])
        self.assertEqual(payload["kosika"]["unsupported"], [])

    def test_an_answer_naming_a_file_the_documents_lack_is_refused(self) -> None:
        invented = (
            "A change must compile and run "
            "`python -m pytest -q tests/test_cli_coverage.py`."
        )
        gateway = self.build(invented)
        status, payload, _ = self.ask(gateway)
        self.assertEqual(status, 200)
        self.assertFalse(payload["kosika"]["grounded"])
        self.assertIn("tests/test_cli_coverage.py", payload["kosika"]["unsupported"])
        content = payload["choices"][0]["message"]["content"]
        self.assertNotEqual(content, invented, "the invented answer is not passed on")
        self.assertIn("cannot answer", content)

    def test_a_folder_the_documents_do_not_name_is_refused(self) -> None:
        gateway = self.build("The report goes into the `session` folder.")
        _, payload, _ = self.ask(gateway)
        self.assertFalse(payload["kosika"]["grounded"])
        self.assertEqual(payload["kosika"]["unsupported"], ["session"])

    def test_a_name_the_documents_only_have_inside_a_longer_word_is_refused(self) -> None:
        # The failure this exists for: a document that says "fails" and
        # "failed" does not name a folder called "fail".
        gateway = self.build(
            "The report goes into the `fail` folder.",
            reference=(
                "A change that fails the check is undone, and a failed attempt "
                "is recorded. The ledger lives at .kosika/selfdev/ledger.jsonl."
            ),
        )
        _, payload, _ = self.ask(gateway)
        self.assertFalse(payload["kosika"]["grounded"])
        self.assertEqual(payload["kosika"]["unsupported"], ["fail"])

    def test_a_miss_says_it_was_a_miss(self) -> None:
        _, payload, _ = self.ask()
        self.assertEqual(payload["kosika"]["cache"], "miss")

    def test_the_rule_reaches_the_model_in_front_of_the_question(self) -> None:
        self.ask()
        messages = self.specialist.requests[0].messages
        self.assertEqual(messages[0].role, "system")
        self.assertIn("name no file, path, folder", messages[0].content)
        self.assertEqual(messages[-1].role, "user")
        self.assertIn("how does the loop decide", messages[-1].content)

    def test_nothing_is_claimed_when_the_profile_cannot_say_what_it_read(self) -> None:
        gateway = Gateway(
            StubSpecialist("the answer"),
            Meter(self.journal, Quota(calls_per_day=5)),
            name="kosika-selfdev",
            keys={self.key: "editor"},
            ground=True,
        )
        status, payload, _ = self.ask(gateway)
        self.assertEqual(status, 200)
        self.assertIsNone(payload["kosika"]["grounded"], "unchecked, and said so")
        self.assertEqual(payload["choices"][0]["message"]["content"], "the answer")

    def test_a_refused_answer_is_not_kept(self) -> None:
        gateway = self.build("The report goes into the `session` folder.")
        self.ask(gateway)
        self.ask(gateway)
        self.assertEqual(len(self.specialist.requests), 2, "a wrong answer cannot be reused")
        self.assertEqual(len(self.cache), 0)

    def test_a_refusal_is_written_down_as_one(self) -> None:
        gateway = self.build("The report goes into the `session` folder.")
        self.ask(gateway)
        line = self.journal.entries()[-1]
        self.assertTrue(line["refused"])
        self.assertFalse(line["grounded"])

    def test_a_grounded_answer_is_written_down_as_grounded(self) -> None:
        self.ask()
        line = self.journal.entries()[-1]
        self.assertFalse(line["refused"])
        self.assertTrue(line["grounded"])

    def test_checking_can_be_turned_off(self) -> None:
        gateway = self.build("The report goes into the `session` folder.", ground=False)
        _, payload, _ = self.ask(gateway)
        self.assertIsNone(payload["kosika"]["grounded"])
        self.assertEqual(
            payload["choices"][0]["message"]["content"],
            "The report goes into the `session` folder.",
        )


if __name__ == "__main__":
    unittest.main()
