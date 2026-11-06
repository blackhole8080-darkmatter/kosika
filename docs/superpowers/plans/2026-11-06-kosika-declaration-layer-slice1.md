# Kosika Declaration Layer — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a Kosika declaration body take real literals — text, whole and decimal numbers, `on`/`off`, `none`, and maps and lists nested in each other — and type-check them, identically in both engines.

**Architecture:** One general value parser in each engine produces an ordered value tree (`DeclList`/`DeclMap`/`DeclRef` over Python scalars; the same three types over Go scalars). The parser stores those values untouched on `ModelDecl.config` as an ordered tuple of pairs; the checker types them afterwards against a small type table.

The parse/check split is forced, not a preference. A `ParseError` is not comparable across engines: Python's `conformance.report` catches `ValueError` and returns `("Check failed: …\n", 2)` (`kosika/conformance.py:57-58`), while Go sends a parse failure to `fail`, which writes to **stderr and exits 1** (`native/main.go:47-49`) and reserves stdout-plus-exit-2 for checker refusals (`:50-53`). So a decimal in a whole-number field must be a `CheckError` for conformance case 10 to mean anything — which is also what the spec's §Error handling says ("**Check time** … A decimal where an integer is typed", `:182-183`). That is why the parser keeps the number as written, `int` or `float`, instead of coercing it, and why `parse_model_parameters`' existing parse-time integer rule (`kosika/parser.py:878`) is *not* the pattern this slice follows.

**Tech Stack:** Python 3 (reference engine, `kosika/`), Go (second engine, `native/`), pytest, `python -m kosika.conformance`, `go build`.

**Spec:** `docs/superpowers/specs/2026-11-05-kosika-declaration-layer-design.md` (committed `58aa8aa`). This plan implements **slice 1 only** ("Literals and checking … conformance cases 9, 10. Both engines."). Slices 2–5 get their own plans as each lands, because slice 1 fixes the shape of the value tree they all consume.

## Global Constraints

Copied from the spec and from the repository state verified this session. Every task's requirements include this section.

- **Neither canonicalization may be unified or altered.** `plan_identity` is `json.dumps(identity, sort_keys=True, ensure_ascii=False)` (`kosika/virtual_model.py:169`). `plan_hash`'s `_canonical` is `json.dumps(value, sort_keys=True, separators=(",", ":"))` (`kosika/agent.py:97`). Slice 1 touches neither file.
- **`_ABSENT_DEFAULTS` discipline (`kosika/ast.py:916`).** Any field added to an AST dataclass must default to the value a program that does not use it has, and that default must be registered, so an existing program's payload is byte-identical. The hash-stability guard enforces this and is a **merge blocker**.
- **Byte-identical refusal strings.** `tests/test_conformance.py:199` compares the Python and Go `Check failed: <message>` text exactly. Any message this plan specifies must be produced character-for-character by both engines.
- **Diagnostics coverage stays clean.** `kosika diagnostics-coverage --check` must pass. Every new raise site's message must be claimed by an existing rule in `kosika/diagnostics.py:_RULES`, or a rule must be extended.
- **No lexer change, in either engine.** `kosika/lexer.py:19` and `native/lexer.go:19` already emit every token this slice needs. `on`, `off`, `none`, `extends`, `resolved`, `flat`, `late`, `route`, `otherwise` are plain identifiers, read in context.
- **`native/std` stays a byte-for-byte copy of `kosika/std`** (`tests/test_conformance.py:843`). This slice adds no stdlib file.
- **Passage retrieval stays Python-only.** Not exercised in slice 1; `kosika/virtual_model.py` is not opened.
- **Do not add a per-model `timeout` or `budget` field.** It is not in the spec and has not been decided.
- **Git hygiene.** The repository is on branch `studio` with 99 pre-existing modified files and a large untracked stdlib that are the user's in-progress work. Never `git add -A` or `git add .`. Stage each file this plan names, by name.
- **Expected-output files are written with `newline="\n"`.** `kosika/conformance.py:77` does this; a hand-written `.expected.txt` must match or the comparison at `:79` fails on CRLF.
- **AGENTS.md (GitNexus).** Run impact analysis before editing a symbol and `detect-changes` before committing: `node .gitnexus/run.cjs impact "<symbol>" --direction upstream --repo .` and `node .gitnexus/run.cjs detect-changes --scope all --repo .`, from `C:\Users\Aryan\Aryan_Private`. If the index does not resolve a Kosika symbol it returns `risk: UNKNOWN`, which is **not** an all-clear — confirm callers by text search before editing, and say so in the commit.

## Deviations from the spec, and why

Four things this plan does differently from the spec's text. Each is a finding from reading the code, not a preference.

1. **The hash-stability guard moves from slice 3 to slice 1.** The spec places it in slice 3 ("Composition … the hash-stability guard lands here"). But slice 1 already adds a field to `ModelDecl`, which is inside `program_payload`'s reach, so the risk the guard exists to catch arrives in slice 1. Task 1 builds the guard *first*, before any AST change, and every later task re-runs it.
2. **Declaration-body fields are written `key: value`.** The spec's grammar example (`:46-62`) writes `base "qwen2.5:7b"` and `limits { … }` with no colon, while every declaration body that exists today uses a colon — `input: Question`, `deployment: "small"`, `max_output_tokens: 200` (`conformance/cases/models.kos:8-17`). This plan standardises on `key: value`. The colon-free form in the example is treated as a typo in the spec.
3. **Slice 1 adds no new diagnostic code.** The spec says slice 1 delivers "the new diagnostic codes". Reading `kosika/diagnostics.py:_RULES`, every message this slice can raise is already claimed: `check/type-mismatch` (`r"expects .*, got "`, `:577`) takes every typed-field refusal, `parse/invalid-model` (`:514`) takes `Invalid model field` / `Invalid or duplicate model field`, `parse/expected-token` (`:313`) takes a malformed value token, `parse/limit-exceeded` (`:315`) takes the nesting limit, and `check/unknown-field` (`r"^Unknown \w+ field"`, `:348`) takes an unknown key inside a nested map. Adding codes nothing needs would trip `unreachable_templates` (`kosika/diagnostics_source.py:244`). Task 4 proves the classification with assertions on `code_for`, and Task 6 runs the coverage gate.
4. **Four of the six new fields have no slot in the profile schema — carried to slice 2, not resolved here.** The virtual-model profile schema is closed at seven keys (`kosika/virtual_model.py:105-113`: `schema`, `name`, `instructions`, `sources`, `verified_examples`, `max_context_chars`, `max_results`) and rejects anything else (`:114`). The spec's grammar example names `base`, `embed`, `cache`, `fallback` and `limits`, which the schema has no room for. Slice 1 **parses and types** all six (they are in the approved grammar) and does not touch `virtual_model.py`. Slice 2, which defines the payload mapping, must decide whether the schema widens or those fields are refused. This is written into Task 6's handoff note so it cannot be lost.
5. **The guard regenerates only under `--write`; a bare run verifies.** Task 1 as first written computed the hashes, wrote the baseline unconditionally, and only then picked an exit code (`SystemExit(0 if "--write" in sys.argv else 2)`). That contradicted the module's own docstring and made Task 6's merge gate self-defeating: run bare, it overwrote the baseline it was checking and exited non-zero on a correct tree. The shipped `__main__` divides the two jobs — `--write` regenerates behind a floor that refuses to write an implausibly small corpus, and a bare run calls `test_program_hashes_have_not_moved` itself, so the CLI and pytest cannot disagree about what drift means. Fixed during Task 1's review, so the code above already shows it.

---

### Task 1: The hash-stability guard

**Files:**
- Create: `tests/test_hash_stability.py`
- Create: `tests/hash_baseline.json` (generated, then committed)

**Interfaces:**
- Consumes: `kosika.ast.program_payload(program) -> dict` (`kosika/ast.py:963`), `kosika.modules.load_program(path) -> Program`.
- Produces: `corpus_hashes(root: Path = ROOT) -> dict[str, list[str]]`, mapping a repo-relative `.kos` path to `[identity_digest, canonical_digest]`. Later tasks re-run this test unchanged; it is the merge blocker.

- [ ] **Step 1: Confirm the suite is green before touching anything**

Run: `cd C:/Users/Aryan/Aryan_Private/projects/kosika && python -m pytest tests/test_conformance.py -q`
Expected: all tests pass (or skip, if `go` is not on PATH). Also run `python -m kosika.conformance check` and note the `N of N programs print what is expected` line — Task 6 depends on the suite being green *before* new cases are added.

- [ ] **Step 2: Write the guard**

Create `tests/test_hash_stability.py`:

```python
"""No change to the AST may move a program's hash.

`plan_identity` and `plan_hash` are folded into recorded approvals
(`kosika/approval_ledger.py`), and a durable session raises `plan_mismatch`
when the hash it bound no longer matches (`kosika/durable.py:135`). So a
program whose payload shifts silently invalidates every approval made against
it. `_ABSENT_DEFAULTS` (`kosika/ast.py`) exists to stop that happening when a
field is added; this test is the enforcement.

Both canonicalizations are hashed, because they differ and neither may be
changed: `plan_identity` uses `ensure_ascii=False` with default separators
(`kosika/virtual_model.py:169`), and `plan_hash` uses `separators=(",", ":")`
(`kosika/agent.py:97`).

Regenerate only with `python tests/test_hash_stability.py --write`, and only in
a change that means to move hashes.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from kosika.ast import program_payload
from kosika.modules import load_program

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).resolve().parent / "hash_baseline.json"
#: The folders scanned for programs. A folder that does not exist yields
#: nothing, so the list can name a path before it is created.
CORPUS = ("conformance", "examples", "kosika/std")


def corpus_hashes(root: Path = ROOT) -> dict[str, list[str]]:
    """Each loadable program's two payload digests, by repo-relative path.

    A program that does not load is skipped: the suite holds refused programs
    on purpose, and their refusal text is compared elsewhere
    (`tests/test_conformance.py:199`).
    """

    hashes: dict[str, list[str]] = {}
    for folder in CORPUS:
        for path in sorted((root / folder).glob("**/*.kos")):
            try:
                program = load_program(path)
            except (ValueError, TypeError):
                continue
            payload = program_payload(program)
            identity = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            hashes[path.relative_to(root).as_posix()] = [
                hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            ]
    return dict(sorted(hashes.items()))


def test_program_hashes_have_not_moved() -> None:
    recorded = json.loads(BASELINE.read_text(encoding="utf-8"))
    current = corpus_hashes()
    moved = sorted(
        name
        for name in set(recorded) | set(current)
        if recorded.get(name) != current.get(name)
    )
    assert not moved, (
        f"{len(moved)} program(s) changed payload hash, first: {moved[:5]}. "
        "Adding an AST field must not change an existing program's hash: give "
        "it the value a program without the feature has, and register that "
        "value in _ABSENT_DEFAULTS (kosika/ast.py). Regenerate the baseline "
        "only if this change means to invalidate recorded approvals."
    )


if __name__ == "__main__":
    if "--write" in sys.argv:
        hashes = corpus_hashes()
        # Floor: the corpus holds 257 .kos files under CORPUS, 165 of which
        # load. A plausible-looking count is the only proof the loader ran;
        # without it a wholesale load_program failure would quietly write an
        # empty baseline. A low N means the loader is failing, not that the
        # corpus shrank.
        if len(hashes) < 100:
            raise SystemExit(
                f"only {len(hashes)} programs loaded (measured: 165 loadable "
                "of 257 .kos files). The loader is failing wholesale, not "
                "the corpus being small. Refusing to write the baseline."
            )
        BASELINE.write_text(
            json.dumps(hashes, indent=1, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"wrote {len(hashes)} program hashes to {BASELINE.name}")
        raise SystemExit(0)

    # Bare run: verify against the committed baseline, write nothing. Reuse
    # the pytest comparison so the two paths cannot disagree.
    recorded = json.loads(BASELINE.read_text(encoding="utf-8"))
    try:
        test_program_hashes_have_not_moved()
    except AssertionError as exc:
        print(exc)
        raise SystemExit(1)
    print(f"guard: {len(recorded)} program hashes match")
    raise SystemExit(0)
```

- [ ] **Step 3: Generate the baseline from today's code**

Run: `cd C:/Users/Aryan/Aryan_Private/projects/kosika && python tests/test_hash_stability.py --write`
Expected: `wrote 165 program hashes to hash_baseline.json`. The count is the proof the loader actually ran, so the code refuses to write below 100 with a message saying the loader is failing wholesale rather than the corpus being small — 257 `.kos` files live under those three folders and 165 of them load. If the floor fires, diagnose `load_program`; do not lower the threshold.

- [ ] **Step 4: Run the guard and see it pass**

Run: `python -m pytest tests/test_hash_stability.py -q`
Expected: PASS.

- [ ] **Step 5: Prove the guard bites**

A guard that cannot fail is not a guard. Add one field to `ModelDecl` **without** registering it, run the test, watch it fail, then revert.

In `kosika/ast.py`, after `plain: bool = False` (line 587), add temporarily:

```python
    temporary_probe: str = ""
```

Run: `python -m pytest tests/test_hash_stability.py -q`
Expected: FAIL, naming many programs. (`asdict` now emits `"temporary_probe": ""` for every model, and `""` is not a registered absent default, so every payload containing a model moves.)

Then revert that one line and run again:
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd C:/Users/Aryan/Aryan_Private/projects/kosika
git add tests/test_hash_stability.py tests/hash_baseline.json
git commit -m "Hash the corpus, so a moved program hash fails the build"
```

---

### Task 2: Declaration values in the Python AST

**Files:**
- Modify: `kosika/ast.py` — new value types beside `ModelParameters` (`:555`), a new field on `ModelDecl` (`:566-587`), one new `_ABSENT_DEFAULTS` entry (`:916`).

**Interfaces:**
- Consumes: nothing new.
- Produces, for Tasks 3 and 4 and for every later slice:
  - `DeclRef(name: str)` — a bare identifier in a declaration body.
  - `DeclList(items: tuple[Any, ...] = ())` — a `[...]`.
  - `DeclMap(entries: tuple[tuple[str, Any], ...] = ())` — a `{k: v}`.
  - `ModelDecl.config: tuple[tuple[str, Any], ...] = ()` — the new body fields, in source order, values untyped.
  - A declaration value is therefore `str | int | float | bool | None | DeclRef | DeclList | DeclMap`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_declaration_values.py`:

```python
"""The value tree a declaration body parses to, and what it does to a hash."""

from __future__ import annotations

from kosika.ast import (
    DeclList,
    DeclMap,
    DeclRef,
    ModelDecl,
    Program,
    program_payload,
)


def test_a_model_without_config_hashes_as_it_did_before() -> None:
    payload = program_payload(Program(models=[ModelDecl("m", "Question", "Answer")]))
    assert "config" not in str(payload)


def test_config_keeps_the_order_it_was_written_in() -> None:
    model = ModelDecl(
        "specialist",
        "Question",
        "Answer",
        config=(
            ("base", "qwen2.5:7b"),
            ("limits", DeclMap((("context", 8000), ("results", 6)))),
            ("sources", DeclList(("KOSIKA.md",))),
            ("cache", True),
            ("fallback", None),
            ("embed", DeclRef("nomic")),
        ),
    )
    payload = program_payload(Program(models=[model]))
    assert payload["models"][0]["config"] == [
        ["base", "qwen2.5:7b"],
        ["limits", {"entries": [["context", 8000], ["results", 6]]}],
        ["sources", {"items": ["KOSIKA.md"]}],
        ["cache", True],
        ["fallback", None],
        ["embed", {"name": "nomic"}],
    ]
```

- [ ] **Step 2: Run it to see it fail**

Run: `cd C:/Users/Aryan/Aryan_Private/projects/kosika && python -m pytest tests/test_declaration_values.py -q`
Expected: FAIL at import — `ImportError: cannot import name 'DeclList'`.

- [ ] **Step 3: Add the value types**

In `kosika/ast.py`, immediately before `@dataclass(frozen=True) class ModelParameters:` (line 555), insert:

```python
@dataclass(frozen=True)
class DeclRef:
    """A bare identifier in a declaration body: a reference, not text.

    The rule that makes a declaration worth more than JSON. `"coder"` is inert
    text; `coder` is a name the checker resolves, so a reference to nothing is
    a refusal rather than a string that fails at run time.
    """

    name: str


@dataclass(frozen=True)
class DeclList:
    """A `[...]` in a declaration body."""

    items: tuple[Any, ...] = ()


@dataclass(frozen=True)
class DeclMap:
    """A `{k: v}` in a declaration body.

    Entries and not a dict, in both engines: a Go map's iteration order is
    randomised, and the payload the two engines hash must be identical. Order
    is the source's, which is also what an author reads.
    """

    entries: tuple[tuple[str, Any], ...] = ()
```

- [ ] **Step 4: Add the field to `ModelDecl`**

In `kosika/ast.py`, at the end of `ModelDecl` (after `plain: bool = False`, line 587), add:

```python
    # The declaration-body fields a model config carries: `base`, `embed`,
    # `sources`, `limits`, `cache`, `fallback`. Values are what the parser
    # read, not yet typed; the checker types them. Ordered pairs, so the
    # payload is the same in both engines.
    config: tuple[tuple[str, Any], ...] = ()
```

- [ ] **Step 5: Register the absent default**

In `kosika/ast.py`, inside `_ABSENT_DEFAULTS` (the dict starting at line 916), add alongside the other sequence entries:

```python
    "config": ((), []),
```

- [ ] **Step 6: Run the new test and the guard together**

Run: `python -m pytest tests/test_declaration_values.py tests/test_hash_stability.py -q`
Expected: both PASS. The guard passing here is the point — a new field on `ModelDecl` did not move one existing hash.

- [ ] **Step 7: Commit**

```bash
git add kosika/ast.py tests/test_declaration_values.py
git commit -m "Give a declaration body a value tree, and keep it out of old hashes"
```

---

### Task 3: The declaration-value parser (Python)

**Files:**
- Modify: `kosika/parser.py` — the `from .ast import (...)` block (`:7`), `parse_model` (`:834-876`), and a new method beside `parse_model_parameters` (`:878`).
- Test: `tests/test_declaration_values.py` (extend).

**Interfaces:**
- Consumes: `DeclRef`, `DeclList`, `DeclMap` from Task 2; the existing `Parser.check`, `Parser.expect`, `Parser.accept`, `Parser.advance`, `Parser.identifier`, `Parser.current` (`kosika/parser.py:131-144`, `:283`).
- Produces:
  - `Parser.parse_declaration_value(self, depth: int = 0) -> Any` — one value of a declaration body.
  - `Parser.MODEL_CONFIG_KEYS: frozenset[str]` — `{"base", "embed", "sources", "limits", "cache", "fallback"}`. Task 5 mirrors both in Go.
  - `ModelDecl.config` is populated by `parse_model`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_declaration_values.py`:

```python
import pytest

from kosika.parser import ParseError, parse


def _config(source: str) -> tuple[tuple[str, object], ...]:
    return parse(source).models[0].config


def test_every_literal_form() -> None:
    assert _config(
        'model m {\n'
        '  base: "qwen2.5:7b"\n'
        '  limits: { context: 8000, results: 6.5 }\n'
        '  sources: ["a", "b"]\n'
        '  cache: on\n'
        '  fallback: none\n'
        '  embed: nomic\n'
        '}\n'
    ) == (
        ("base", "qwen2.5:7b"),
        ("limits", DeclMap((("context", 8000), ("results", 6.5)))),
        ("sources", DeclList(("a", "b"))),
        ("cache", True),
        ("fallback", None),
        ("embed", DeclRef("nomic")),
    )


def test_off_is_false_and_integers_stay_whole() -> None:
    assert _config("model m {\n  cache: off\n  limits: { context: 1e3 }\n}\n") == (
        ("cache", False),
        ("limits", DeclMap((("context", 1000.0),))),
    )


def test_a_whole_number_and_a_decimal_are_distinct_values() -> None:
    # The spec's table (`…-design.md:74`): `40`, `40.0` and `1e3` are three
    # different values, canonicalising to `40`, `40.0` and `1000.0`. Pinning
    # the Python types here, rather than comparing them at run time, because
    # `40 == 40.0` is true in Python and the distinctness that matters is in
    # the payload slice 2 emits.
    (_, limits), = _config("model m {\n  limits: { context: 40, results: 40.0 }\n}\n")
    (context_key, context), (results_key, results) = limits.entries
    assert (context_key, results_key) == ("context", "results")
    assert type(context) is int and context == 40
    assert type(results) is float and results == 40.0


def test_lists_and_maps_nest_in_each_other() -> None:
    assert _config(
        "model m {\n"
        "  sources: [{ a: [1, { b: on }] }, \"x\"]\n"
        "}\n"
    ) == (
        (
            "sources",
            DeclList(
                (
                    DeclMap(
                        (("a", DeclList((1, DeclMap((("b", True),))))),)
                    ),
                    "x",
                )
            ),
        ),
    )


def test_a_negative_number_is_one_value() -> None:
    assert _config("model m {\n  limits: { context: -8000 }\n}\n") == (
        ("limits", DeclMap((("context", -8000),))),
    )


def test_the_existing_fields_are_untouched() -> None:
    program = parse(
        "record Q { text: Text }\n"
        "record A { text: Text }\n"
        "model m {\n"
        '  input: Q\n  output: A\n  deployment: "small"\n'
        "  parameters: {\n"
        "    temperature: 0.2\n"
        "    max_output_tokens: 200\n"
        '    stop: ["END"]\n'
        "  }\n"
        '  base: "x"\n'
        "}\n"
    )
    model = program.models[0]
    assert (model.input, model.output, model.deployment) == ("Q", "A", "small")
    assert model.parameters.temperature == 0.2
    assert model.parameters.stop == ("END",)
    assert model.config == (("base", "x"),)


def test_a_key_it_does_not_know_is_still_refused() -> None:
    with pytest.raises(ParseError, match=r"Invalid model field 'nope'"):
        parse("model m {\n  nope: 1\n}\n")


def test_a_key_twice_is_refused() -> None:
    with pytest.raises(ParseError, match=r"Invalid or duplicate model field 'base'"):
        parse('model m {\n  base: "x"\n  base: "y"\n}\n')


def test_a_key_twice_inside_a_map_is_refused() -> None:
    with pytest.raises(ParseError, match=r"Invalid or duplicate model field 'a'"):
        parse("model m {\n  limits: { a: 1, a: 2 }\n}\n")


def test_a_value_that_is_not_one_is_refused() -> None:
    with pytest.raises(ParseError, match=r"^Expected value at 2:9"):
        parse("model m {\n  base: }\n}\n")


def test_nesting_has_a_limit() -> None:
    deep = "[" * 33 + "]" * 33
    with pytest.raises(ParseError, match=r"nesting exceeds 32"):
        parse(f"model m {{\n  sources: {deep}\n}}\n")
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_declaration_values.py -q`
Expected: the Task 2 tests still pass; the new ones FAIL — `ParseError: Invalid model field 'base'` for most, since `parse_model` does not know the key yet.

- [ ] **Step 3: Import the value types**

In `kosika/parser.py`, add `DeclList`, `DeclMap`, `DeclRef` to the existing alphabetically-sorted `from .ast import (...)` block that starts at line 7. They go after the `C…` names and before `EnumValueExpr`-style `E…` names — keep the block alphabetical.

- [ ] **Step 4: Add the value parser**

In `kosika/parser.py`, immediately after `parse_model_parameters` ends (the method starting at `:878`), add:

```python
    #: The model-body keys that take a declaration value rather than a record
    #: name or a typed parameter block. Slice 1 parses and types these; what
    #: they mean is settled later.
    MODEL_CONFIG_KEYS = frozenset(
        {"base", "embed", "sources", "limits", "cache", "fallback"}
    )

    def parse_declaration_value(self, depth: int = 0) -> Any:
        """One value of a declaration body.

        Text, a whole or decimal number, `on`/`off`, `none`, a bare identifier
        (a reference), a list, or a map — the last two nesting arbitrarily.
        Nothing here is typed: a decimal in a whole-number field is refused by
        the checker, so that the suite can hold it as a refusal.
        """

        if depth >= 32:
            raise ParseError("Declaration value nesting exceeds 32")
        if self.check("["):
            self.advance()
            items: list[Any] = []
            if not self.check("]"):
                while True:
                    items.append(self.parse_declaration_value(depth + 1))
                    if not self.accept(","):
                        break
            self.expect("]")
            return DeclList(tuple(items))
        if self.check("{"):
            self.advance()
            entries: list[tuple[str, Any]] = []
            seen: set[str] = set()
            if not self.check("}"):
                while True:
                    key = self.identifier()
                    if key in seen:
                        raise ParseError(f"Invalid or duplicate model field {key!r}")
                    seen.add(key)
                    self.expect(":")
                    entries.append((key, self.parse_declaration_value(depth + 1)))
                    if not self.accept(","):
                        break
            self.expect("}")
            return DeclMap(tuple(entries))
        token = self.current
        if token.kind == "STRING":
            self.advance()
            return token.value
        if token.kind == "NUMBER":
            self.advance()
            return int(token.value) if token.value.isdigit() else float(token.value)
        if token.kind == "IDENT":
            self.advance()
            if token.value == "on":
                return True
            if token.value == "off":
                return False
            if token.value == "none":
                return None
            return DeclRef(token.value)
        if token.value == "-":
            self.advance()
            number = self.current
            if number.kind != "NUMBER":
                raise ParseError(
                    f"Expected number at {number.line}:{number.column}, "
                    f"got {number.value!r}"
                )
            self.advance()
            value = (
                int(number.value) if number.value.isdigit() else float(number.value)
            )
            return -value
        raise ParseError(
            f"Expected value at {token.line}:{token.column}, got {token.value!r}"
        )
```

- [ ] **Step 5: Wire it into `parse_model`**

In `kosika/parser.py`, inside `parse_model` (`:834`), make three edits.

Add a local beside the others (after `plain = False`, `:846`):

```python
        config: list[tuple[str, Any]] = []
```

Add the new branch immediately after the `parameters` branch (which ends with `continue` at `:856`) and **before** the `if key not in {"input", "output", "deployment"}` test at `:857`:

```python
            if key in self.MODEL_CONFIG_KEYS:
                self.expect(":")
                config.append((key, self.parse_declaration_value()))
                continue
```

Pass it through in the `return ModelDecl(...)` at `:871`:

```python
        return ModelDecl(
            name,
            values["input"],
            values["output"],
            deployment,
            parameters,
            plain=plain,
            config=tuple(config),
        )
```

The early `return ModelDecl(name, GENERAL_INPUT, GENERAL_OUTPUT)` at `:839` is left alone: a model with no body has no config.

Note what this preserves: `if not values:` at `:864` still turns a body holding *only* config keys into a general text model, exactly as it already does for a body holding only `deployment` or `parameters`.

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_declaration_values.py tests/test_hash_stability.py -q`
Expected: all PASS.

- [ ] **Step 7: Check the whole Python suite did not move**

Run: `python -m kosika.conformance check`
Expected: the same `N of N programs print what is expected` line as Task 1 Step 1. Then `python -m pytest tests/ -q -x --ignore=tests/test_conformance.py` and expect no new failures.

- [ ] **Step 8: Commit**

```bash
git add kosika/parser.py tests/test_declaration_values.py
git commit -m "Read a declaration body's literals, and leave their types to the checker"
```

---

### Task 4: Typed-field checking (Python)

**Files:**
- Modify: `kosika/checker.py` — the `from .ast import (...)` block (`:9`), a type table and matcher above the model loop, and the loop itself (`:2039-2110`).
- Test: `tests/test_declaration_values.py` (extend).

**Interfaces:**
- Consumes: `ModelDecl.config`, `DeclRef`, `DeclList`, `DeclMap` from Tasks 2–3; `CheckError` from `kosika/checker.py`.
- Produces:
  - `check_declaration_value(model_name: str, path: str, value: Any, expected: Any) -> None` — raises `CheckError` on a mismatch. Task 6 mirrors it in Go as `checkDeclValue`.
  - `MODEL_CONFIG_TYPES: dict[str, Any]` — the type of each config key.
  - A type is a plain name (`"Text"`, `"Int"`, `"Bool"`, `"Null"`) or a tagged tuple: `("either", (name, …))`, `("list", type)`, `("map", ((key, type), …))`.
  - `value_kind(value: Any) -> str` — `"Text" | "Int" | "Number" | "Bool" | "Null" | "Reference" | "List" | "Map"`.
  - Refusal messages, which Go must reproduce byte-for-byte:
    - `Model {name!r} field {path!r} expects {Expected}, got {Kind}`
    - `Model {name!r} field {path!r} expects Text or Null, got {Kind}` (the `either` form, options joined by `" or "` in table order)
    - `Unknown model field {path!r}` for a key a nested map does not have, where `path` is already dotted, e.g. `'limits.deep'`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_declaration_values.py`:

```python
from kosika.checker import CheckError, check
from kosika.diagnostics import code_for


def _refuse(source: str) -> str:
    with pytest.raises(CheckError) as caught:
        check(parse(source))
    return str(caught.value)


def _model(body: str) -> str:
    return (
        "record Q { text: Text }\n"
        "record A { text: Text }\n"
        f"model m {{\n  input: Q\n  output: A\n{body}}}\n"
    )


def test_a_decimal_where_a_whole_number_is_typed_is_refused() -> None:
    message = _refuse(_model("  limits: { context: 8000.5, results: 6 }\n"))
    assert message == "Model 'm' field 'limits.context' expects Int, got Number"


def test_text_where_a_boolean_is_typed_is_refused() -> None:
    assert _refuse(_model('  cache: "yes"\n')) == (
        "Model 'm' field 'cache' expects Bool, got Text"
    )


def test_on_and_off_are_accepted_where_a_boolean_is_typed() -> None:
    check(parse(_model("  cache: on\n")))
    check(parse(_model("  cache: off\n")))


def test_a_list_where_text_is_typed_is_refused() -> None:
    assert _refuse(_model("  base: [\"a\"]\n")) == (
        "Model 'm' field 'base' expects Text, got List"
    )


def test_a_list_item_is_named_by_its_index() -> None:
    assert _refuse(_model("  sources: [\"a\", 2]\n")) == (
        "Model 'm' field 'sources[1]' expects Text, got Int"
    )


def test_a_map_where_a_list_is_typed_is_refused() -> None:
    assert _refuse(_model("  sources: { a: \"b\" }\n")) == (
        "Model 'm' field 'sources' expects List, got Map"
    )


def test_a_key_a_nested_map_does_not_have_is_refused() -> None:
    assert _refuse(_model("  limits: { context: 8000, deep: 1 }\n")) == (
        "Unknown model field 'limits.deep'"
    )


def test_a_nested_map_may_leave_a_key_out() -> None:
    check(parse(_model("  limits: { context: 8000 }\n")))


def test_none_is_accepted_where_text_or_null_is_typed() -> None:
    check(parse(_model("  fallback: none\n")))


def test_a_reference_is_refused_until_references_resolve() -> None:
    assert _refuse(_model("  fallback: specialist\n")) == (
        "Model 'm' field 'fallback' expects Text or Null, got Reference"
    )


def test_a_reference_is_refused_where_text_is_typed() -> None:
    assert _refuse(_model("  base: specialist\n")) == (
        "Model 'm' field 'base' expects Text, got Reference"
    )


def test_a_whole_number_is_refused_where_text_is_typed() -> None:
    assert _refuse(_model("  base: 8000\n")) == (
        "Model 'm' field 'base' expects Text, got Int"
    )


def test_a_number_is_refused_where_text_or_null_is_typed() -> None:
    assert _refuse(_model("  fallback: 3\n")) == (
        "Model 'm' field 'fallback' expects Text or Null, got Int"
    )


def test_every_new_message_is_claimed_by_a_diagnostic_code() -> None:
    for message in (
        "Model 'm' field 'cache' expects Bool, got Text",
        "Model 'm' field 'fallback' expects Text or Null, got Reference",
        "Unknown model field 'limits.deep'",
        "Declaration value nesting exceeds 32",
        "Invalid model field 'nope'",
        "Invalid or duplicate model field 'base'",
        "Expected value at 2:9, got '}'",
        "Expected number at 2:10, got '}'",
    ):
        assert code_for(message) != "kosika/internal-error", message


def test_the_codes_are_the_intended_ones() -> None:
    assert code_for("Model 'm' field 'cache' expects Bool, got Text") == (
        "check/type-mismatch"
    )
    assert code_for("Unknown model field 'limits.deep'") == "check/unknown-field"
    assert code_for("Declaration value nesting exceeds 32") == "parse/limit-exceeded"
    assert code_for("Invalid model field 'nope'") == "parse/invalid-model"
    assert code_for("Expected value at 2:9, got '}'") == "parse/expected-token"
```

- [ ] **Step 2: Run them to see them fail**

Run: `python -m pytest tests/test_declaration_values.py -q`
Expected: the new tests FAIL. Most fail with `ImportError` or because `check` accepts a body it should refuse; the two `code_for` tests should already PASS, which is the evidence that no new diagnostic code is needed.

- [ ] **Step 3: Add the type table and matcher**

In `kosika/checker.py`, add `DeclList`, `DeclMap`, `DeclRef` to the alphabetically-sorted `from .ast import (...)` block starting at line 9.

Then, above the function that contains the model loop, add:

```python
#: The type each model-config field must have. A plain name is that kind of
#: value; `("either", …)` accepts any of the named kinds; `("list", t)` is a
#: list whose every item is `t`; `("map", …)` is a map drawn from the given
#: keys, each typed, where a key may be left out but an unknown one is refused.
MODEL_CONFIG_TYPES: dict[str, Any] = {
    "base": "Text",
    "embed": "Text",
    "sources": ("list", "Text"),
    "limits": ("map", (("context", "Int"), ("results", "Int"))),
    "cache": "Bool",
    "fallback": ("either", ("Text", "Null")),
}


def value_kind(value: Any) -> str:
    """The name of a declaration value's kind, spelled the same in both engines.

    `bool` is tested before `int` because in Python a bool is an int.
    """

    if value is None:
        return "Null"
    if isinstance(value, bool):
        return "Bool"
    if isinstance(value, int):
        return "Int"
    if isinstance(value, float):
        return "Number"
    if isinstance(value, str):
        return "Text"
    if isinstance(value, DeclRef):
        return "Reference"
    if isinstance(value, DeclList):
        return "List"
    return "Map"


def _refuse_value(model_name: str, path: str, expected: str, value: Any) -> None:
    raise CheckError(
        f"Model {model_name!r} field {path!r} expects {expected}, "
        f"got {value_kind(value)}"
    )


def check_declaration_value(
    model_name: str, path: str, value: Any, expected: Any
) -> None:
    """Refuse a declaration value that is not what its field types.

    The path is spelled as the author reads it — `limits.context`,
    `sources[1]` — so a refusal inside a nested map names the whole way down.
    """

    if isinstance(expected, str):
        if value_kind(value) != expected:
            _refuse_value(model_name, path, expected, value)
        return
    tag, inner = expected
    if tag == "either":
        kind = value_kind(value)
        if kind not in inner:
            _refuse_value(model_name, path, " or ".join(inner), value)
        return
    if tag == "list":
        if not isinstance(value, DeclList):
            _refuse_value(model_name, path, "List", value)
        for index, item in enumerate(value.items):
            check_declaration_value(model_name, f"{path}[{index}]", item, inner)
        return
    if not isinstance(value, DeclMap):
        _refuse_value(model_name, path, "Map", value)
    allowed = dict(inner)
    for key, item in value.entries:
        if key not in allowed:
            # Not an f-string nested inside an f-string: that is a syntax error
            # before Python 3.12, and this repository does not require 3.12.
            dotted = f"{path}.{key}"
            raise CheckError(f"Unknown model field {dotted!r}")
        check_declaration_value(model_name, f"{path}.{key}", item, allowed[key])
```

`Any` is already imported in `kosika/checker.py`; if it is not, add it to the `typing` import there.

- [ ] **Step 4: Call it from the model loop**

In `kosika/checker.py`, inside the `for model in program.models:` loop, immediately after the `stop` check ends (line 2110) and before the loop closes, add:

```python
        for field_name, field_value in model.config:
            check_declaration_value(
                model.name, field_name, field_value, MODEL_CONFIG_TYPES[field_name]
            )
```

`MODEL_CONFIG_TYPES[field_name]` cannot raise `KeyError`: the parser only puts a key into `config` if it is in `MODEL_CONFIG_KEYS`, and the two sets are the same six names. Task 6 adds a test that pins this.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_declaration_values.py tests/test_hash_stability.py -q`
Expected: all PASS.

- [ ] **Step 6: Run the diagnostics gate**

Run: `python -m kosika.cli diagnostics-coverage --check` (or `kosika diagnostics-coverage --check` if the console script is installed)
Expected: clean — no unclassified raise site, no unreachable rule. If it reports an unclassified message, the fix is to extend the matching rule in `kosika/diagnostics.py:_RULES`, not to add a code; report which one and why before doing it.

- [ ] **Step 7: Confirm nothing else moved**

Run: `python -m kosika.conformance check && python -m pytest tests/ -q --ignore=tests/test_conformance.py`
Expected: the same conformance line as before, no new failures.

- [ ] **Step 8: Commit**

```bash
git add kosika/checker.py tests/test_declaration_values.py
git commit -m "Type a declaration body's fields, and refuse a decimal where a whole number is written"
```

---

### Task 5: Go parity — parser and checker

**Files:**
- Create: `native/decl.go`
- Modify: `native/ast.go` (`ModelDecl`, `:265-286`), `native/parse_models.go` (`parseModel`, `:9-56`), `native/check_workflows.go` (`checkModels`, `:235-281`).

**Interfaces:**
- Consumes: `p.expect`, `p.check`, `p.accept`, `p.advance`, `p.identifier`, `p.current` (`native/parser.go:33-97`), `parseFail` (`native/parser.go:19`), `checkFail` (`native/check.go:25`), `pyRepr`, `isDigits` (`native/parser.go:237`), `numberLiteral` (`native/parser.go:261`).
- Produces: `declRef`, `declList`, `declMap`, `declEntry`, `parseDeclValue`, `declValueKind`, `checkDeclValue`, `declConfigTypes`, `ModelDecl.Config`. These are the Go twins of Task 2–4's names.

Both `parseFail` and `checkFail` panic, so no code after either runs. That is relied on below.

- [ ] **Step 1: Write `native/decl.go`**

```go
package main

// The values a declaration body takes, as kosika/parser.py reads them and
// kosika/checker.py types them. Ordered throughout: a Go map's iteration order
// is randomised, and the two engines hash the same payload.

import (
	"fmt"
	"math/big"
	"strings"
)

// declRef is a bare identifier in a declaration body: a reference, not text.
type declRef struct{ Name string }

type declList struct{ Items []any }

type declEntry struct {
	Key   string
	Value any
}

type declMap struct{ Entries []declEntry }

// declType is a field's type. Kind "" is a plain kind name; the other three
// are the compound forms, spelled as kosika/checker.py spells them.
type declType struct {
	kind    string // "", "either", "list", "map"
	name    string // the kind name, when kind is ""
	options []string
	item    *declType
	fields  []declField
}

type declField struct {
	key string
	typ declType
}

var (
	typeText = declType{name: "Text"}
	typeInt  = declType{name: "Int"}
	typeBool = declType{name: "Bool"}
)

// declConfigTypes is MODEL_CONFIG_TYPES: the type of each model-config field.
var declConfigTypes = []declField{
	{"base", typeText},
	{"embed", typeText},
	{"sources", declType{kind: "list", item: &typeText}},
	{"limits", declType{kind: "map", fields: []declField{
		{"context", typeInt},
		{"results", typeInt},
	}}},
	{"cache", typeBool},
	{"fallback", declType{kind: "either", options: []string{"Text", "Null"}}},
}

// declConfigType is the type of a model-config key, or nil for a key the
// parser would never have accepted.
func declConfigType(key string) *declType {
	for index := range declConfigTypes {
		if declConfigTypes[index].key == key {
			return &declConfigTypes[index].typ
		}
	}
	return nil
}

// isDeclConfigKey is Parser.MODEL_CONFIG_KEYS.
func isDeclConfigKey(key string) bool { return declConfigType(key) != nil }

// parseDeclValue reads one value of a declaration body: text, a whole or
// decimal number, `on`/`off`, `none`, a bare identifier (a reference), a list,
// or a map — the last two nesting arbitrarily. Nothing here is typed; a
// decimal in a whole-number field is refused by the checker, so the suite can
// hold it as a refusal.
func (p *Parser) parseDeclValue(depth int) any {
	if depth >= 32 {
		parseFail("Declaration value nesting exceeds 32")
	}
	if p.check("[") {
		p.advance()
		items := []any{}
		if !p.check("]") {
			for {
				items = append(items, p.parseDeclValue(depth+1))
				if !p.accept(",") {
					break
				}
			}
		}
		p.expect("]")
		return declList{items}
	}
	if p.check("{") {
		p.advance()
		entries := []declEntry{}
		seen := map[string]bool{}
		if !p.check("}") {
			for {
				key := p.identifier()
				if seen[key] {
					parseFail("Invalid or duplicate model field %s", pyRepr(key))
				}
				seen[key] = true
				p.expect(":")
				entries = append(entries, declEntry{key, p.parseDeclValue(depth + 1)})
				if !p.accept(",") {
					break
				}
			}
		}
		p.expect("}")
		return declMap{entries}
	}
	token := p.current()
	switch token.Kind {
	case "STRING":
		p.advance()
		return token.Value
	case "NUMBER":
		p.advance()
		return numberLiteral(token.Value, false)
	case "IDENT":
		p.advance()
		switch token.Value {
		case "on":
			return true
		case "off":
			return false
		case "none":
			return nil
		}
		return declRef{token.Value}
	}
	if token.Value == "-" {
		p.advance()
		number := p.current()
		if number.Kind != "NUMBER" {
			parseFail("Expected number at %d:%d, got %s", number.Line, number.Column, pyRepr(number.Value))
		}
		p.advance()
		return numberLiteral(number.Value, true)
	}
	parseFail("Expected value at %d:%d, got %s", token.Line, token.Column, pyRepr(token.Value))
	return nil
}

// declValueKind is value_kind: the name of a value's kind, spelled the same in
// both engines. *big.Int before float64 matters as much as bool before int
// does in Python.
func declValueKind(value any) string {
	switch value.(type) {
	case nil:
		return "Null"
	case bool:
		return "Bool"
	case *big.Int:
		return "Int"
	case float64:
		return "Number"
	case string:
		return "Text"
	case declRef:
		return "Reference"
	case declList:
		return "List"
	case declMap:
		return "Map"
	}
	return "Map"
}

func refuseDeclValue(model, path, expected string, value any) {
	checkFail("Model %s field %s expects %s, got %s", pyRepr(model), pyRepr(path), expected, declValueKind(value))
}

// checkDeclValue is check_declaration_value. The path is spelled as the author
// reads it — `limits.context`, `sources[1]`.
func checkDeclValue(model, path string, value any, expected *declType) {
	switch expected.kind {
	case "":
		if declValueKind(value) != expected.name {
			refuseDeclValue(model, path, expected.name, value)
		}
	case "either":
		kind := declValueKind(value)
		for _, option := range expected.options {
			if kind == option {
				return
			}
		}
		refuseDeclValue(model, path, strings.Join(expected.options, " or "), value)
	case "list":
		items, ok := value.(declList)
		if !ok {
			refuseDeclValue(model, path, "List", value)
		}
		for index, item := range items.Items {
			checkDeclValue(model, fmt.Sprintf("%s[%d]", path, index), item, expected.item)
		}
	case "map":
		entries, ok := value.(declMap)
		if !ok {
			refuseDeclValue(model, path, "Map", value)
		}
		for _, entry := range entries.Entries {
			allowed := false
			for index := range expected.fields {
				field := expected.fields[index]
				if field.key != entry.Key {
					continue
				}
				allowed = true
				typ := field.typ
				checkDeclValue(model, path+"."+entry.Key, entry.Value, &typ)
				break
			}
			if !allowed {
				checkFail("Unknown model field %s", pyRepr(path+"."+entry.Key))
			}
		}
	}
}
```

- [ ] **Step 2: Add `Config` to the Go `ModelDecl`**

In `native/ast.go`, at the end of `ModelDecl` (after `Plain bool`, line 285), add:

```go
	// Config holds the declaration-body fields a model config carries, in the
	// order they were written, with the values as parsed. The checker types
	// them; see decl.go.
	Config []declEntry
```

- [ ] **Step 3: Wire the parser**

In `native/parse_models.go`, inside `parseModel`'s loop, add this branch immediately after the `parameters` branch (which ends with `continue` at line 29) and **before** the `if key != "input" && …` test at line 31:

```go
		if isDeclConfigKey(key) {
			p.expect(":")
			model.Config = append(model.Config, declEntry{key, p.parseDeclValue(0)})
			continue
		}
```

The `if len(values) == 0` general-model fallback at line 47 is left alone, so a body holding only config keys is still a general text model — matching Python.

- [ ] **Step 4: Wire the checker**

In `native/check_workflows.go`, inside `checkModels`'s `for _, model := range c.program.Models` loop, immediately after the `stop` check (line 279) and before the loop closes, add:

```go
		for _, entry := range model.Config {
			checkDeclValue(model.Name, entry.Key, entry.Value, declConfigType(entry.Key))
		}
```

`declConfigType` cannot return nil here, for the same reason the Python lookup cannot raise: the parser only appends a key `isDeclConfigKey` accepted. Step 6 pins it.

- [ ] **Step 5: Build**

Run: `cd C:/Users/Aryan/Aryan_Private/projects/kosika/native && go build -o kosika-native.exe . && go vet ./...`
Expected: a clean build and a clean vet. `kosika-native.exe` is what `tests/test_conformance.py:85` builds and runs; committing it is not required — check `git status` and leave any binary untracked if `.gitignore` does not already cover it.

- [ ] **Step 6: Check the Go engine says what was specified**

This step verifies the Go side alone, against the exact strings in Task 4's "Produces" list. Cross-engine proof is Step 7, where `tests/test_conformance.py` does it properly — do not invent an ad-hoc comparison here, because the two engines have different command surfaces: the Python CLI has `check`, the Go binary has `test`, `run`, `tokens`, `replay` and `capabilities` and no `check` at all (`native/main.go:2-10`).

Write `$TEMP/kosika_parity.kos` (outside the repository, so it cannot be committed by accident):

```kosika
record Q { text: Text }
record A { text: Text }

model good {
  input: Q
  output: A
  base: "qwen2.5:7b"
  embed: "nomic-embed-text"
  sources: ["a", "b"]
  limits: { context: 8000, results: 6 }
  cache: on
  fallback: none
}

test "a declaration body of literals is accepted" {
  expect 1 == 1
}
```

Run: `cd C:/Users/Aryan/Aryan_Private/projects/kosika/native && ./kosika-native.exe test "$TEMP/kosika_parity.kos"; echo "exit $?"`
Expected: the test passes and the exit code is 0.

Then, one at a time, replace the `base:` line with each row below and re-run the same command. Each must print exactly the stated line on stdout and give `exit 2`:

| replacement for the `base:` line | expected stdout |
|---|---|
| `embed: nomic` | `Check failed: Model 'good' field 'embed' expects Text, got Reference` |
| `base: 8000` | `Check failed: Model 'good' field 'base' expects Text, got Int` |
| `cache: "yes"` | `Check failed: Model 'good' field 'cache' expects Bool, got Text` |
| `sources: { a: "b" }` | `Check failed: Model 'good' field 'sources' expects List, got Map` |
| `limits: { context: 8000.5, results: 6 }` | `Check failed: Model 'good' field 'limits.context' expects Int, got Number` |
| `limits: { context: 8000, deep: 1 }` | `Check failed: Unknown model field 'limits.deep'` |
| `sources: ["a", 2]` | `Check failed: Model 'good' field 'sources[1]' expects Text, got Int` |
| `fallback: 3` | `Check failed: Model 'good' field 'fallback' expects Text or Null, got Int` |

The first row is the one that proves Step 4's loop is wired at all: `embed: nomic` is a reference in a `Text` field, and a Go checker that never consults `declConfigTypes` would accept it in silence. Every string above must match Task 4's Python assertions character for character — if any differs, the Go message is wrong, not the Python one.

Then delete the scratch file.

- [ ] **Step 7: Run the conformance suite on both engines**

Run: `cd C:/Users/Aryan/Aryan_Private/projects/kosika && python -m pytest tests/test_conformance.py -q`
Expected: PASS. This is the token-equality (`:262`), trace-equality (`:177`) and refusal-equality (`:199`) gate over the 125 existing programs — the proof that adding six model-body keys changed nothing any existing program does.

Also run `python -m pytest tests/test_hash_stability.py tests/test_declaration_values.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add native/decl.go native/ast.go native/parse_models.go native/check_workflows.go
git commit -m "Read and type a declaration body in the Go engine, message for message"
```

---

### Task 6: Conformance cases 9 and 10, and the merge gate

**Files:**
- Create: `conformance/cases/declaration_values.kos`, `conformance/cases/declaration_values.expected.txt`
- Create: `conformance/refused/declaration_value_int.kos`, `conformance/refused/declaration_value_int.expected.txt`
- Modify: `tests/test_declaration_values.py` (one last test).

**Interfaces:**
- Consumes: everything above.
- Produces: conformance case 9 and case 10 of the spec's fifteen (`docs/superpowers/specs/…-design.md:241-242`), discovered automatically by `kosika/conformance.py:39` — no registration list to edit.

- [ ] **Step 1: Write case 9, the passing case**

Create `conformance/cases/declaration_values.kos`:

```kosika
// Conformance: the literals a declaration body takes — text, whole and
// decimal numbers, `on` and `off`, `none`, and maps and lists nested in each
// other — and that a model carrying them still checks and runs.

record Question { text: Text }
record Answer { text: Text }

model specialist {
  input: Question
  output: Answer
  base: "qwen2.5:7b"
  embed: "nomic-embed-text"
  sources: ["KOSIKA.md", "kosika/std/nn.kos"]
  limits: { context: 8000, results: 6 }
  cache: on
  fallback: none
}

fn answer(question: Question) -> Answer {
  return {text: question.text + " ok"}
}

test "a model carrying declaration literals still runs" {
  r = answer({text: "x"})
  expect r.text == "x ok"
}
```

Two notes on what is deliberately **not** in this case. The whole/decimal distinctness of `40` and `40.0` is pinned by type in `tests/test_declaration_values.py` instead, because `40 != 40.0` is false in Python and a run-time comparison would say the opposite of what the spec means (`…-design.md:74` is about the payload, which slice 2 emits). And this case has no `workflow` — nine of the existing `cases/` programs carry none, so `run_tests` runs the `test` blocks alone.

The two source paths are inert text in this slice — nothing reads them until slice 2 — and are the spec's own example (`docs/superpowers/specs/…-design.md:50`).

- [ ] **Step 2: Write case 10, the refusal**

Create `conformance/refused/declaration_value_int.kos`:

```kosika
// Refused: a decimal where a declaration body types a whole number.

record Question { text: Text }
record Answer { text: Text }

model specialist {
  input: Question
  output: Answer
  limits: { context: 8000.5, results: 6 }
}
```

- [ ] **Step 3: Validate the hand-written refusal, then generate the rest**

Create `conformance/refused/declaration_value_int.expected.txt` with exactly this content and a trailing newline:

```
Check failed: Model 'specialist' field 'limits.context' expects Int, got Number
```

Check that string before anything is allowed to overwrite it:

Run: `cd C:/Users/Aryan/Aryan_Private/projects/kosika && python -m kosika.conformance check`
Expected: exactly two `DIFFERS` lines — `cases/declaration_values.kos`, which has no expectation yet, and `refused/declaration_value_int.kos` **only if the hand-written string is wrong**. If the refused one differs, fix the file to what Task 4 specifies and re-run until the only remaining `DIFFERS` line is the `cases/` one. Do not let `write` paper over a wrong expectation.

Do **not** hand-write `declaration_values.expected.txt` — the `PASS` column spacing and the summary line come from `kosika/testing.py`'s `to_text`. Generate it:

Run: `python -m kosika.conformance write`

This rewrites **every** expected file in the suite. Immediately confirm it touched nothing that already existed:

Run: `git status --porcelain conformance`
Expected: four `??` lines and nothing else — the two new `.kos` files and the two new `.expected.txt` files. **Any `M` line means a pre-existing expectation changed**, which means either the suite was not green before this task or something in Tasks 2–5 changed an existing program's behaviour. Run `git diff conformance` and diagnose; do not commit a mass rewrite.

Then read the generated file:

Run: `cat conformance/cases/declaration_values.expected.txt`
Expected:
```
PASS  a model carrying declaration literals still runs
1 passed, 0 failed
```

- [ ] **Step 4: Confirm the reference is self-consistent**

Run: `python -m kosika.conformance check`
Expected: `127 of 127 programs print what is expected` — 125 before this task, 2 more now.

- [ ] **Step 5: Run the suite on both engines**

Run: `python -m pytest tests/test_conformance.py -q`
Expected: PASS. This is the real gate: the Go engine must accept case 9, refuse case 10 with that exact string, produce the same token stream for both, and produce equal trace events. If `go` is not on PATH these tests skip silently (`tests/test_conformance.py:74`) — confirm they ran rather than skipped, and say so explicitly if they skipped.

- [ ] **Step 6: Pin the parser's key set to the checker's type table**

Both engines look up a config key's type without a missing-key path, on the argument that the parser only accepts keys the table has. That argument is worth a test rather than a comment. Append to `tests/test_declaration_values.py`:

```python
def test_every_key_the_parser_accepts_has_a_type() -> None:
    from kosika.checker import MODEL_CONFIG_TYPES
    from kosika.parser import Parser

    assert Parser.MODEL_CONFIG_KEYS == frozenset(MODEL_CONFIG_TYPES)
```

Run: `python -m pytest tests/test_declaration_values.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full merge gate**

Run each and record the result:

```bash
cd C:/Users/Aryan/Aryan_Private/projects/kosika
python -m pytest tests/ -q
python -m kosika.conformance check
python -m kosika.cli diagnostics-coverage --check
python tests/test_hash_stability.py && echo "guard: baseline matches"
```

Expected: the pytest run green; `127 of 127`; diagnostics coverage clean; the guard's `wrote N program hashes` with a zero exit — and N unchanged from Task 1, which is the proof the corpus did not silently shrink.

The spec's merge gate also lists five live Ollama steps (`docs/superpowers/specs/…-design.md:258-267`). **Steps 3, 4 and 5 cannot be run against slice 1**: the `payload` capability is slice 2, a live `kosika agent` run through a `.kos`-declared model needs the payload mapping, and `kosika agent` hard-defaults to a 60-second model timeout with no override (`kosika/cli.py:749`) which `qwen2.5:7b` exceeds — observed live on 2026-11-05. Steps 1 and 2 are covered by case 9 and case 10 respectively. Run the five live steps once, at the end of slice 2, and say plainly in the commit or PR which were run and which were not.

- [ ] **Step 8: Commit**

```bash
git add conformance/cases/declaration_values.kos conformance/cases/declaration_values.expected.txt \
        conformance/refused/declaration_value_int.kos conformance/refused/declaration_value_int.expected.txt \
        tests/test_declaration_values.py
git commit -m "Hold both engines to declaration literals, and to refusing a decimal typed whole"
```

Before committing, run the graph-change analysis AGENTS.md requires:

```bash
cd C:/Users/Aryan/Aryan_Private
node .gitnexus/run.cjs detect-changes --scope all --repo .
```

A `partial: true` or `truncated: true` result is not a clean check — re-run it.

---

## Handoff to slice 2

Written here so it cannot be lost between plans.

1. **The closed profile schema has no room for four of the six new fields.** `kosika/virtual_model.py:105-113` allows exactly `schema`, `name`, `instructions`, `sources`, `verified_examples`, `max_context_chars`, `max_results`, and `:114` refuses anything else. Slice 1 parses and types `base`, `embed`, `sources`, `limits`, `cache`, `fallback`. Slice 2, which turns a declaration into the payload dict, must decide for each of `base`, `embed`, `cache`, `fallback` and `limits` whether the schema widens or the field is refused — and `limits.context`/`limits.results` map naturally onto `max_context_chars`/`max_results`, whose ranges are already enforced at load (`:136-139`). Do not widen the schema silently: it is inside `plan_identity`.
2. **A `DeclRef` is parsed but never accepted yet.** Every config field's type in `MODEL_CONFIG_TYPES` excludes `Reference`, so `embed: nomic` is refused today. Reference resolution is slice 3. When slice 3 lands it changes entries in that table and in Go's `declConfigTypes` — both must move together.
3. **`config` is ordered pairs, not a map, in both engines.** Slice 2's payload emission must preserve that order or the two engines will hash differently. `DeclMap.entries` exists for the same reason.
4. **Case 9's `sources` paths are inert.** The moment slice 2 reads them, `conformance/cases/declaration_values.kos` starts touching the filesystem from inside the conformance suite. Either keep the paths inert by construction or point them at files the suite already has.
5. **Still undecided, still not to be added:** a per-model `timeout` or `budget` field.

## Verification checklist for the whole slice

- [ ] `tests/hash_baseline.json` committed, N unchanged from Task 1 through Task 6.
- [ ] `python -m pytest tests/ -q` green.
- [ ] `python -m pytest tests/test_conformance.py -q` green and **not skipped** (Go on PATH).
- [ ] `python -m kosika.conformance check` reports 127 of 127.
- [ ] `diagnostics-coverage --check` clean, with no new entry in `CODES`.
- [ ] `git log --stat` for the six commits touches only the files this plan names — no file from the 99 pre-existing modified files, nothing from the untracked stdlib.
- [ ] Live Ollama steps 3–5 explicitly recorded as not run, with the reason.
