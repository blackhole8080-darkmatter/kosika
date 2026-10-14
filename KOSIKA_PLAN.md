# Kosika implementation plan

This document is the shared plan for human developers and coding agents working in the actual Kosika checkout:

`C:\Users\Aryan\Aryan_Private\projects\kosika`

## Mission

Kosika is an AI-native programming language and runtime for building typed AI applications, agents, multi-agent systems, tools, workflows, and understandable system maps. Its current implementation is Python-hosted, but the language and product are not restricted to Python applications.

The long-term principle is:

> Unlimited expression, bounded authority.

Kosika should become more expressive than a collection of Python/JavaScript scripts while keeping execution, capabilities, model use, external effects, resources, and approvals explicit and fail-closed.

## Non-negotiable engineering rules

- Preserve unrelated uncommitted work; never reset or clean the checkout.
- Treat this checkout as authoritative. Do not confuse it with prototype folders or the separate Synapse/ai_agent project.
- Do not modify protected `agent-lab` content.
- Do not commit unless explicitly requested.
- Keep the current implementation dependency-light and maintain its supported Python runtime. Treat Python as the host implementation, not as the boundary of the Kosika language or its interoperability goals.
- The checker, schema validation, approval system, and replay validator remain authoritative.
- Model output is a proposal, never execution permission.
- Unknown tools, invalid manifests, undeclared capabilities, invalid schemas, and unsafe imports fail closed.
- Mindmaps, explanations, design graphs, and replay summaries must be deterministic from checked source, manifests, and traces.
- Add focused tests for every new branch, then run the full suite, Ruff, and MyPy.

## K01 — Reconciled capability baseline

This matrix is the implementation baseline for subsequent work. It reconciles
the checked source, existing tests, CLI smoke scenarios, and browser observation.
It is deliberately narrower than `KOSIKA_VISION.md`.

Status meanings:

- **Exercised** — K01 directly observed a representative local scenario.
- **Implemented** — connected source and existing tests exist, but K01 did not
  exercise the complete capability.
- **Experimental** — a partial implementation or material integration gap exists.
- **Planned** — no supported end-to-end implementation was found.

| Capability | Status | Evidence and current boundary |
|---|---|---|
| Typed records, arrays, expressions, mutable locals, conditions, match, bounded loops | Exercised | `kosika/parser.py`, `kosika/checker.py`, and `kosika/runtime.py`; typed input/output scenario returned its checked result. |
| Pure functions, nested functions, closures, higher-order values, bounded recursion | Exercised | `examples/closures.kos` returned `{'value': 115}`; `examples/recursion_match.kos` returned factorial `120`, parity `odd`, and classification `zero`. Recursion requires an explicit bound; async functions, generators, vectors, matrices, tensors, and signals remain planned. |
| Local modules, explicit exports, typed workflow composition | Exercised | `kosika/modules.py` enforces bounded local `.kos` loading; the three-file `examples/composed` program checked and ran. There is no package registry or dynamic/remote import contract. |
| Declared models and typed model calls | Exercised | The composed program ran with `ScriptedModelProvider` fixtures. `kosika/model.py` also contains an OpenAI-compatible HTTP provider, but K01 did not establish a successful live endpoint. |
| Bounded agents and typed `ask` calls | Implemented | `kosika/agent.py`, `kosika/agent_steps.py`, and runtime dispatch are connected. Direct agent approval support exists; approval plumbing for an execute tool requested inside workflow `ask` is not established end to end. |
| Tools, pinned MCP, capabilities, durable sessions, exact-action approval | Implemented | `kosika/runtime.py`, `kosika/durable.py`, `kosika/approval_ledger.py`, and existing approval tests implement fail-closed host authority. This is not an OS sandbox, authenticated reviewer identity, rollback, or distributed exactly-once execution. |
| Trace validation and offline replay | Exercised | The composed fixture run replayed the same typed result without provider access. Replay validates and returns recorded outcomes; it does not re-execute source or provide tamper-proof attestation. |
| Budgets, cancellation, deadlines, and retries | Implemented | `kosika/execution.py` and `kosika/controls.py` provide bounded accounting and cooperative checks. They do not hard-stop arbitrary host code or guarantee a remote token bill cannot overshoot. |
| Read-only parallel workflow DAG | Implemented | `kosika/dag.py` schedules host-defined observe-only nodes with bounded concurrency and replay. There is no corresponding language syntax and it is not the swarm runtime. |
| Swarm declarations, rounds, consensus, and group approval | Experimental | Swarms parse, check, and render through `kosika/parser.py`, `kosika/checker.py`, and `kosika/mindmap.py`. `kosika/swarm.py` (K10b) runs one swarm's workflow in bounded rounds under one ledger-claimed approval and a shared budget, exercised from the CLI with fixtures. There is no member messaging, scheduling policy, shared round state, or consensus algorithm. |
| CLI and developer commands | Exercised | `check`, `run`, `replay`, `mindmap`, `editor`, and `analyze` were exercised through `python -m kosika.cli`. The CLI also exposes agent, explanation, network, design, manifest, extension, and journal surfaces that were not all exercised in K01. |
| Editor data, LSP, and VS Code integration | Exercised | `kosika/editor_features.py`, `kosika/lsp.py`, `kosika_lsp`, and `editors/vscode` provide diagnostics and navigation. K04 drove the stdio LSP process and verified stable-code publication for both entry and imported document URIs. Path-based diagnostics use the authoritative bounded module loader; a running VS Code client was not exercised. |
| Browser workbench | Exercised | The server advertises `.kos` as its source contract and the frontend consumes it for startup, imports, validation, status, and file creation. K02 exercised the default sample through check, run, checked-source map, and exact replay in a real Chrome session. |
| Desktop application | Experimental | The Electron and legacy launchers wrap the workbench, but K02 did not build or exercise packaged artifacts. Distribution remains unsigned and Windows-specific. |
| Deterministic purpose/structure maps and execution timelines | Exercised | `mindmap` produced a deterministic composed-program summary, and K09 adds `audience-map`: five audience profiles over one project with every entry labelled `checked`, `declared`, `observed`, or `human`. Omissions are counted in `coverage` rather than read as absence, and a trace whose recorded program hash does not match contributes nothing. Heuristic foreign-source observations remain a separate evidence layer and are not merged into an audience map. |
| Python, JavaScript, TypeScript, and Rust source analysis | Exercised | `kosika.language_adapters` provides bounded read-only structure extraction. TypeScript analysis was exercised and explicitly reported that it does not execute code or provide full semantic analysis. |
| Cross-language execution and component contracts | Exercised | `kosika.component/v1` pins one local process artifact, typed tool schemas, access, effects, capabilities, declared errors, termination, and SHA-256 implementation identity. `examples/component_uppercase.kos` executed the JavaScript artifact through the normal `tool_bindings` boundary. The host still supplies the runtime and OS authority; this is not a sandbox, remote ABI, signature system, or package manager. |
| Grounded assistant and fixture evaluation | Implemented | `kosika/assistant.py` explains checked source without tools; `kosika/evaluation.py` performs fresh bounded cases with exact/substring assertions and traces. Neither is an autonomous author/check/repair/run loop. |
| AI authoring and repair loop | Exercised | `kosika/authoring.py` runs K06 over `kosika.development/v1` with the benchmark's search/replace patch seam; proposals stay in memory, authority is host-only, and attempts are bounded and recorded. `kosika/authoring_evaluation.py` (K07) measures it over the corpus: 266/422 repaired by the reference evaluator, 0 authority violations, 26/26 clean programs left alone. No live-model run yet. |
| Intelligence, memory, assumptions, judgments, and personality | Experimental | Runtime-connected cache/trace pattern hooks exist, while typed cognitive state remains a vision target. Standalone heuristics must not be described as language semantics or autonomous scheduling. |
| Remote cloud execution and reproducible distribution | Experimental | Wheel and source-distribution builds now install the `kosika` and `kosika-lsp` commands, include workbench assets, and pass an isolated local check/run/replay/LSP/workbench smoke. CI defines Python 3.10, Python 3.12, and Windows coverage. No published release, remote execution service, authentication boundary, or cross-platform CI result is claimed yet. |

### K01 verification record

The following bounded observations establish the **Exercised** labels:

```text
python -B -m kosika.cli check examples/hello_world.kos
  -> OK
python -B -m kosika.cli run examples/typed_input_output.kos --input examples/typed_input.json --trace <temp>
  -> {'score': 0.87, 'accepted': True}
python -B -m kosika.cli run examples/closures.kos --trace <temp>
  -> {'value': 115}
python -B -m kosika.cli run examples/recursion_match.kos --trace <temp>
  -> {'factorial': 120, 'parity': 'odd', 'classification': 'zero'}
python -B -m kosika.cli run examples/composed/main.kos --input examples/composed/inputs.json --model-fixture examples/composed/responses.json --trace <temp>
python -B -m kosika.cli replay <temp>
  -> both returned {'text': 'Typed, capability-checked AI workflows with offline replay.'}
python -B -m kosika.cli mindmap examples/composed/main.kos --format text
  -> 3 modules, 12 nodes, 22 edges
python -B -m kosika.cli editor examples/hello_world.kos
  -> structured editor JSON
python -B -m kosika.cli analyze examples/order_service.ts --format text
  -> bounded TypeScript structure with explicit semantic-analysis limitations
```

The failing-before browser observation for K02 opened the local workbench, then
loading **Research pipeline** failed with
`A workspace needs 1–16 valid .syn source files.` The frontend and backend
filename contracts disagreed. Temporary traces and the assessment host were removed.

The project quality gates remain:

```powershell
python -m pytest -q
ruff check kosika tests
python -m mypy kosika
```

K01 did not rerun the complete gates because it changes documentation only.
Later implementation slices must run focused scenarios plus these gates.

### K02 completion record

K02 established one `.kos` source-file contract:

- `kosika.workbench.SOURCE_EXTENSION` owns the backend suffix and `/api/session`
  advertises it to the browser.
- The frontend uses that session value for workspace validation, new files,
  status text, and examples. Legacy `.syn` names fail before dispatch.
- The CLI usage list now includes `diagnostics-coverage`, `editor`, and `city`,
  matching their registered parsers.
- In a real Chrome session, the default three-file Research pipeline opened with
  `main.kos`, passed checking, ran with fixtures, produced an 18-node checked-source
  map, and replayed its typed result without model or tool calls.
- The browser also rejected `helpers.syn` and created `helpers.kos` successfully.
- A `.kosika.json` workspace containing `main.kos` imported successfully and
  exported back to a 424-byte workspace retaining the `.kos` entry and source.

Verification:

```text
Focused: 22 tests + 18 subtests passed
Full suite: 662 tests + 316 subtests passed
Ruff: passed
Final CLI usage regression after command-list reconciliation: passed
```

Pytest emitted one environment warning because it could not write `.pytest_cache`.
The repository-wide MyPy gate remains red with 54 errors across 16 files, including
unrelated integration, intelligence, optimizer, agent, runtime, and CLI code.
K02 did not broaden into those pre-existing worktree changes.

## Delivery phases

### Phase 1 — Structural language and deterministic graph

- Stabilize records, structured values, modules, model declarations, agents, tools, workflows, and nested calls.
- Keep imports bounded, explicit, export-controlled, and content-hashed.
- Keep mindmaps and explanations derived from checked AST/module graphs.
- Make module ownership, direct imports, call relationships, permissions, and data flow inspectable.

### Phase 2 — Design and visual understanding

- Expose module containers, declaration ownership, and import edges in the vector design graph.
- Keep workflow, network, and mindmap views deterministic and human-readable.
- Preserve stable node/edge identifiers where possible.
- Add visual proposals only as checked, approval-gated source proposals; diagrams never become execution authority.
- Add future design primitives for timelines, vector fields, matrix transforms, scenes, and memory graphs without coupling presentation to runtime authority.

### Phase 3 — AI systems and bounded assistant behavior

- Expand provider-neutral model policies: structured output, routing, local/cloud policy, version pins, budgets, caching, and telemetry.
- Expand agent capabilities, bounded reasoning, context limits, memory access, assumptions, judgments, evidence, and personality as typed inspectable state.
- Keep cognitive state explainable through records, diagnostics, event IDs, traces, and replay.

### Phase 4 — Runtime, MCP, and event stream

- Normalize execution events across workflows, models, tools, MCP servers, extensions, networks, approvals, failures, and visualizations.
- Add live SSE/WebSocket delivery to the accessible prototype while retaining offline JSONL replay as the source of truth.
- Keep network access, extension activation, remote execution, and mutation approval-gated.
- Bound cancellation, cleanup, retries, process termination, and uncertain external outcomes.

### Phase 5 — Remaining mathematical and expressive programming

- Preserve the implemented functions, nested functions, closures, bounded recursion,
  higher-order values, conditions, pattern matching, assignment, and bounded loops.
- Add generators, async behavior, vectors, matrices, tensors, sequences, and signals
  only after defining their resource limits, type rules, and replay semantics.
- Connect mathematical values to safe animation and visualization primitives.

### Phase 6 — Migration, optimization, interoperability, and ecosystem

- Extend the existing language adapters, LSP, editor features, evaluations, and
  deterministic maps rather than replacing them.
- Add explicit cross-language component contracts before promising arbitrary
  Python/JavaScript/Rust execution or automatic semantic understanding.
- Add grounded translation and modernization with behavior verification.
- Preserve unsupported behavior behind typed adapters and mark it in maps and
  migration reports.
- Add reproducible packages, benchmarks, deployment adapters, and release gates.
- Claim improvements only when measured for correctness, latency, memory, token
  use, source size, network use, or cost.

## Completed implementation slice

The module-visualization slice is complete:

- `kosika/design.py` now creates deterministic module nodes from `Program.module_hashes`, `Program.module_declarations`, and `Program.module_imports`.
- Direct module dependencies render as `imports` edges.
- Module-to-declaration ownership renders as `contains` edges.
- Nested relative module paths receive safe stable design identifiers while preserving their source-path labels.
- Single-file design output remains unchanged because it has no module graph metadata.
- Focused composed-program tests cover imported records, models, workflows, ownership, imports, and deterministic digests.
- Design documentation describes the module graph semantics.

The normalized event-envelope and strict-JSONL slice is complete:

- `kosika/events.py` formalizes the existing `synapse.kosika.event/v1` bridge contract without creating a competing schema.
- Required schema, bridge version, identity, sequence, timestamp, routing, type, status, and replay fields are validated before publication.
- Event streams require contiguous sequences and unique event IDs; payloads are strict JSON and bounded by depth, item, text, and serialized-size limits.
- JSONL rejects duplicate keys, non-finite numbers, blank lines, malformed values, and non-object records.
- Normalized JSONL writes validate first and replace files atomically; normalized reads validate the complete stream.
- `kosika/synapse_bridge.py` now validates creation, execution, and replay streams while preserving raw runtime traces and existing replay versions.
- Focused tests cover valid envelopes, required fields, schema/version/type/status failures, ordering, duplicate IDs, payload bounds, strict JSONL failures, round trips, replay markers, failure status, sensitive summaries, and raw-trace compatibility.

The read-only SSE delivery slice is complete:

- `kosika/sse.py` provides a dependency-light, thread-safe bounded normalized-event stream with optional validated JSONL persistence.
- The workbench exposes token-protected `GET /api/events` over the existing loopback Host/Origin boundary.
- Reconnect accepts `Last-Event-ID` or `after=<sequence-or-event-id>` and bounds each replay with `limit`.
- Cursors older than the retained in-memory buffer return HTTP 409 instead of an ambiguous partial replay.
- Subscriber counts, heartbeat writes, socket disconnect monitoring, stream shutdown, and cleanup are bounded.
- Successful fixture/local runs publish normalized events into the stream while preserving the existing raw trace response and replay path.
- Focused tests cover ordering, sequence and event-ID reconnects, cursor eviction, strict publication, JSONL persistence, subscriber limits, SSE framing, token enforcement, HTTP replay, disconnect cleanup, and real workbench-run publication.
- `WORKBENCH.md` documents `/api/events`, reconnect behavior, the optional `--event-trace` JSONL path, and the observation-only authority boundary.

## Completed slice: CLI event-stream publication and timeline design

The stream-integration slice is complete:

- `kosika/event_stream_design.py` provides the bounded stream-to-design adapter: validated normalized events become one deterministic `event_timeline` design manifest rendered through the existing design layer (JSON, text, Mermaid, SVG) without granting authority.
- Known event types map to coarse stages (`execution`, `approval`, `check`, `failure`, `replay`); unknown `kosika_*` types remain observable for forward compatibility; non-`kosika` types fail closed. Malformed, empty, and oversized sources fail closed with a 512-node bound.
- `kosika.cli run` accepts `--event-trace` (normalized JSONL), `--design`, `--design-id`, and `--design-name`. Completed runs normalize the raw trace through the existing bridge contract; approval pauses publish a `kosika_approval_pending` stream.
- The normalized stream must not overwrite the raw trace path; the raw trace remains the replay source of truth and normalized publication is additive.
- Focused tests cover determinism, renderability, stage mapping, failure branches, path-collision rejection, and CLI publication.

## Completed slice: workbench event publication and stream robustness

The workbench-events slice is complete:

- `kosika/event_stream_design.py` now hosts shared lifecycle-event helpers (`publish_check_event`, `publish_failure_event`, `publish_approval_event`, `program_hash`, `BridgePublicationError`) so the CLI and workbench emit one validated identity scheme; a missing program omits the hash instead of inventing one.
- The workbench publishes a `kosika_check` event when a check/run/mindmap dispatch validates a program, and a `kosika_error` event when a dispatch fails. Events use the live stream sequence, so publication is additive after any prior content.
- A bounded `timeline` workbench action (max 512 raw events, object-checked) renders deterministic Mermaid/SVG timeline designs from posted raw run events without granting authority.
- The SSE handler now breaks cleanly when the bounded buffer evicts a held cursor mid-connection; clients reconnect from their last event ID instead of surfacing an error.
- A stream-collision bug (hardcoded sequence 1 for lifecycle events) was caught by the new ordering test before release and fixed by using `event_stream.next_sequence`.
- Focused tests cover append-after-publication ordering with unique IDs, check-failure publication, the timeline action plus malformed-input branches, cursor eviction during a live connection, stream shutdown with a connected client, and the end-to-end CLI approval pause writing both a normalized stream and an approval timeline.

## Workbench timeline view slice (complete)

The workbench-timeline-visibility slice is complete:

- The workbench frontend now renders the deterministic design timeline inside the Timeline output tab: after each run, the raw event array is posted to the existing `timeline` action and the returned SVG, stage chips, and design digest are prepended above the step inspector, with a graceful fallback that keeps the step list if the design render fails.
- A gap this exposed: `dispatch("timeline", ...)` existed and was tested, but the HTTP route allowlist in the workbench server did not include `/api/timeline`, so the browser received 404. The route was added and is now covered by an HTTP-level test posting a real run's events through the server.
- New focused tests cover the run-dispatch → timeline-dispatch round trip (deterministic digest across repeats, SVG and Mermaid rendered, stage summary present), malformed timeline payloads failing closed at the dispatch layer, and the live HTTP route with both the success and the 400 branch.
- A live browser session (workbench on 127.0.0.1) ran the research-pipeline sample end to end: Timeline tab shows the design section (digest, six `execution` stage chips, inline SVG) above the recorded step rows.
- The same pipeline was demonstrated from the CLI: `python -m kosika.cli run examples/nested_functions.kos --event-trace ... --design ...` produced `{'value': 36}` from Strawberry's nested closures, a 6-event normalized bridge stream, and a deterministic SVG timeline design on disk.

## Live event visibility slice (complete)

The live-publication-visibility slice is complete:

- The workbench browser UI now subscribes to the event stream: a new `events.js` client consumes the authenticated `/api/events` SSE endpoint incrementally (reader loop over `data:` frames, cursor tracking by `event_id`) and renders the newest twelve events in a live activity strip in the sidebar. The endpoint is a true streaming response, so the first client draft that awaited a completed JSON body hung forever — the reader-loop rewrite is the documented fix.
- Events arrive with type chips (check / workflow / step / error / approval coloring), subject (workflow · step), status, sequence, and timestamp; the strip reconnects with backoff, pauses while the tab is hidden (aborting the in-flight stream), and resumes from the last event ID. Replayed tail events from the server buffer prove cursor continuity in practice.
- Two concurrency tests now cover the stream under real threading: one publisher with three concurrent subscribers (attachment gated on subscriber slots, `max_replay` sized to the whole run) receives every event exactly once in contiguous order, and a slow consumer on a two-event buffer either keeps pace or receives a deterministic replay-gap signal instead of skipped events. A bulk event helper avoids the seconds-only timestamp trap of the existing test helper.
- A suspicious heartbeat byte sequence in the SSE handler was verified against the running code and is already well-formed (`: keep-alive\n\n`); no change was made, and the false lead is recorded here so it is not chased again.
- A "Nested functions" workbench sample exercises Strawberry's lexical-closure slice in the UI: nested `fn` helpers capture outer variables, run under fixtures with no model or tools, and produced `{"value": 36}` with a six-event deterministic timeline live in the browser. The editor highlighter now colors the `fn` keyword.
- True mid-run publication from inside `runtime.py` remained out of scope for that slice. The current runtime still accumulates events and hands them to the caller after completion.

## K03 supported-language contract (complete)

- `KOSIKA.md` is now the normative `kosika-language/0.1.0` contract. It
  separates supported language behavior from host APIs, tooling-only
  experiments, and planned vision work.
- The contract defines the implemented lexical and module grammar, value and
  typing rules, pure-function and workflow execution, model/tool/agent
  declarations, capability and approval boundaries, whole-run controls, and
  trace-version-5 validated outcome replay.
- Parser-recognized but non-executable surfaces are explicit: swarms remain
  tooling-only, anonymous inline functions and `try ... retry` are outside the
  runtime contract, and method calls are checker-rejected.
- `tests/test_language_contract.py` executes and replays the supported
  hello-world, typed-input, closure, recursion/match, and composed-module/model
  examples with exact observable results. The model replay path proves that
  replay performs no live provider call.
- Focused conformance passed: two contract tests execute five public examples,
  validate exact outputs, emit trace version 5, and replay offline. Full Ruff
  lint and the pinned MCP manifest check also passed.
- The repository-wide gate remains red outside K03: the full suite ran 649
  tests and failed only the concurrent corpus repair test on a trailing-newline
  mismatch; branch coverage was 77% against the 80% gate after concurrent
  modules entered the tree. The format check also found seven unrelated
  diagnostics/corpus files still needing formatting after the K03 test was
  formatted. Focused MyPy reached only pre-existing dependency errors and
  reported none in `tests/test_language_contract.py`.

## K04 structured diagnostics (complete)

- `kosika.diagnostics/v1` is the stable JSON report contract. Lexical, parser,
  checker, capability, and module failures map to a closed code registry with a
  coverage check for every explicit compiler raise site.
- Parser diagnostics cover the actual offending token and expose
  `expected`/`actual` values when available. Checker diagnostics expose
  repair-relevant type, field, argument, operator, and capability data.
- Checker spans identify only a unique owning declaration or step. Missing or
  ambiguous ownership returns no JSON range and sets `anchor_missing`; it does
  not fabricate source precision.
- `analyze_path` and `report_path` check the authoritative bounded module graph,
  retain each failure's originating path, and support in-memory source
  overrides. CLI JSON checking, editor features, and the LSP all use that path
  for imported programs.
- The stdio LSP publishes imported failures against the imported document URI
  and republishes direct dependents after an open-buffer change.
- Focused parser/checker/module, CLI, editor, LSP, workbench, and trailing-newline
  repair checks passed. A clean child process ran 690 tests successfully while
  excluding the previously reported corpus stored-patch failure; that excluded
  failure remains an unresolved repository gate.
- Full Ruff formatting and lint, the pinned MCP manifest check, and focused
  MyPy for `diagnostics.py`, `editor_features.py`, and `lsp.py` passed. Package
  branch coverage measured 63.90%, below the configured 80% repository gate;
  broad experimental modules remain untested.

## K05 machine-readable development interface (complete)

- `kosika.development/v1` provides one response envelope for checked project
  inspection, K04 diagnostics, bounded workflow execution, validated trace
  inspection, and deterministic checked-AST maps.
- `inspect_project`, `check_project`, `run_project`, `inspect_trace`, and
  `map_project` delegate to the existing module loader, diagnostic registry,
  runtime, replay validator, schema generator, and mindmap builder. No language
  semantics were reimplemented.
- Strict JSON requests support `inspect`, `check`, `run`, `trace`, and `map`.
  Unknown schema versions, operations, fields, limit names, and malformed JSON
  return stable `request/invalid` responses.
- Execution exposes applied limits, counters, raw events, typed values, and
  structured runtime failures. Model and tool authority can only be injected
  by a Python host; the JSON CLI does not mint bindings.
- CLI JSON checking and imported-document editor diagnostics now consume the
  shared interface while preserving `kosika.diagnostics/v1`.
- Eleven focused development-interface tests cover all operations, strict
  request validation, imported project metadata, deterministic maps, bounded
  execution, host-binding denial, exact trace replay, and the real CLI process.
  Focused branch coverage for `kosika/development.py` is 96.17%.
- Full Ruff formatting/lint, focused MyPy, and the pinned MCP manifest check
  passed. A clean child process passed 702 of 703 discovered tests; the
  previously reported corpus stored-patch failure was the sole excluded test.



## K06 bounded AI authoring loop (complete)

- `kosika.authoring` runs `intent -> check -> propose -> repair -> run ->
  inspect` and reports through `kosika.authoring/v1`.
- Checking, running, and trace inspection delegate to `kosika.development/v1`;
  diagnostic text is the K04 contract and the patch format is the benchmark's
  search/replace seam, extracted as `build_proposal_messages` so the authoring
  loop and the benchmark solver share one prompt and one answer format.
- Model output is a proposal only: patches are applied to an in-memory copy of
  one source file and returned under `proposals`. The loop never writes to disk.
- The host supplies the proposing provider, the run's model and tool bindings,
  MCP commands, execution limits, cancellation, and trace path. The loop mints
  no authority; a missing run binding fails closed as `runtime/error`, and a
  diagnostic that needs a proposal with no provider fails closed as
  `authoring/no-provider`. A project that already checks needs no model at all.
- A diagnostic names its originating path, so only the failing file is
  patchable. Search text matching zero or several lines is rejected, and an
  out-of-project diagnostic is reported as `authoring/unmapped` rather than
  repaired against the wrong file.
- `AuthoringLimits.max_attempts` bounds the whole loop at one provider call per
  attempt. Provider errors (`authoring/provider`), reasoning-only answers
  (`authoring/no-answer`), and an exhausted budget (`authoring/uncheckable`)
  each stop the loop with a distinct code instead of retrying blindly.
- `kosika.cli author` exposes the loop with separate proposal and run authority:
  `--proposal-fixture` or `--proposal-base-url`/`--proposal-model` for proposals,
  `--model-fixture` for the run, `--no-run` to stop after proposing. It exits 0
  on a clean loop, 1 when the program never checked, 2 on invalid setup.
- 29 focused tests cover the clean path, an imported-file repair, the refusal to
  write proposals, trace inspection, missing run authority, the attempt budget,
  ambiguity feedback, a non-converging patch, missing patch blocks, provider and
  reasoning-only failures, absent proposal authority, host source overrides,
  unreadable and out-of-project files, input validation, limit validation, both
  prompt strategies, and the CLI in-process paths. Branch coverage for
  `kosika/authoring.py` is 96%.
- Also fixed in this slice: the top-level `--help` usage list was a hand-maintained
  tuple that had drifted from the registered subparsers, so `benchmark` and
  `corpus` were registered, dispatchable, and invisible in discovery. It is now
  derived from the registered parsers via `cli.build_parser()`, and the guard
  test asserts the whole registered set rather than a spot-check list.

## K07 AI authoring evaluations (complete)

- `kosika.authoring_evaluation` measures the K06 loop over the repair corpus and
  reports through `kosika.authoring-evaluation/v1`.
- Each corpus case is materialised as a standalone project (its origin
  directory's `.kos` sources copied flat) and run through `author()`, so the
  measurement covers project context, the attempt budget, and the run -- not
  just patch text.
- Metrics per case: `checked_clean`, `first_pass_clean`, `needless_edits`,
  `attempts`, per-attempt model calls/tokens/provider latency, aggregate model
  time, whole-case time, and `behavior`.
- Behavior is compared against the *unmutated* program wherever both can run,
  and reported as `not-measured` with a reason otherwise. A repair that checks
  clean but changes what a program returns is therefore distinguishable from one
  that does not.
- The authority audit is observed, not reported: file hashes before and after
  detect writes, proposals are checked for escaping the project root, and valid
  declarations in the in-memory proposal are compared against the *original*
  program's tools, MCP sources, `agent.can` grants, `agent.cannot` denials, and
  tool access levels. Invalid proposals are not misread as empty authority sets.
- Two reference evaluators bracket every real run. `identity` (no proposing
  model) measures 0.000; `reference` (the corpus's own stored repair replayed as
  a patch) is the ceiling over the cases whose fix is quotable as a unique
  search/replace block, with the remainder reported as `unexpressible_cases`
  rather than scored as loop failures.
- `kosika.cli author-eval` exposes the evaluation with per-case progress on
  stderr, `--solver reference|identity|model`, `--limit`, `--attempts`,
  `--no-behavior`, `--slowest`, and `--format text|json`.
- **Measured on the full corpus (448 cases; 422 repair, 26 valid), reference
  evaluator: 266/422 repaired (0.630); 26 valid programs untouched with zero
  model calls; 0 authority violations; 43/43 measurable cases behaviorally
  correct, 0 incorrect; ~7s aggregate case time against a scripted provider
  (0.004s of it in the provider); 156 cases not quotable as a
  patch.** Identity floor: 0.000, 0 violations. Per-code repair rates are
  bimodal: most checker codes and `lex/unexpected-character` repair at 1.00,
  while `check/unknown-tool`, `check/empty-workflow`,
  `check/unknown-argument`, `check/unknown-field`, `check/field-access`,
  `check/invalid-swarm`, `check/exhaustiveness`, and the
  `parse/expected-identifier` / `parse/invalid-declaration` families repair at
  0.00 -- all of them cases whose stored fix removes a declaration, or a line
  the patch format cannot quote uniquely.
- 36 focused tests cover reference-patch expressibility, case selection, project
  materialisation, both baselines over the real corpus, behavioral comparison
  and non-claims, per-attempt cost accounting, valid-case scoring, all seven
  authority violation kinds plus a real source-swap violation, explicit denial
  semantics, restoring revoked authority as *not* a violation, invalid-proposal
  comparison, limit validation, crash handling, and the CLI. Branch coverage
  for `kosika/authoring_evaluation.py` is 93%.
- The first live-model measurement has now been performed; see the local model
  protocol and live evaluation slice below for the numbers and what they do and
  do not support.

## Local model protocol and first live evaluation

- `OpenAICompatibleProvider` takes a `protocol` of `openai` (default, unchanged)
  or `ollama`. The `ollama` protocol targets Ollama's native `/api/chat`.
- The reason this exists is measured. Ollama's OpenAI-compatible `/v1` endpoint
  accepts a `think` field and ignores it, so a reasoning model spends its entire
  output budget on the trace and returns empty content. Through the provider on
  one machine, same model and prompt: `openai` 45.3s / 152 completion tokens /
  675 reasoning characters, `ollama` 15.2s / 2 completion tokens / 0 reasoning
  characters. The native endpoint also requires `stream: false` explicitly,
  because Ollama streams by default and a streamed body fails to decode.
- `kosika.authoring_evaluation.live_provider_factory` takes the protocol through
  to the provider. `author-eval` and `benchmark` expose `--protocol`; `author`
  exposes `--proposal-protocol` and `--proposal-think`, whose default is now
  `off` because a machine-readable patch cannot be read out of a reasoning trace.
- The Ollama protocol refuses tool calling rather than silently dropping it, and
  reads the native `message`, `thinking`, `eval_count`, and `prompt_eval_count`
  fields. A reasoning-only reply stays distinguishable from a reply with no
  content. 28 focused tests cover endpoint derivation, payload shape, response
  parsing, failure branches, the CLI wiring, and that the `openai` path is
  unchanged.
- **First live measurement, qwen3:1.7b over Ollama on CPU, 12 corpus cases
  (11 repair, 1 valid):** repair rate 0/11; 22 attempts and 22 model calls;
  15,646 prompt and 1,510 completion tokens; 733.7s model time and 734.2s wall
  time; 0 authority violations; 0 needless edits on the valid program. Eight
  cases had an expressible reference patch and none were repaired.
- What this supports: the harness, the authority guarantees, and the protocol
  are exercised end to end against a real local model, and the loop's safety
  properties held under a real model rather than only under fixtures.
- What this does not support: any claim that the model can author patches. A
  1.7B model on CPU is far below the 0.630 reference ceiling, and this run says
  nothing about whether a larger model would do better. Throughput improved by
  roughly 3.7x per case against the same model on the `openai` path, which made
  a bounded run practical; it did not make the model more capable.
- The full corpus remains out of reach on this hardware. It is CPU-only
  inference with roughly 2-4 tokens per second and about 15 GB of RAM against a
  3 GB resident model.

## K08 cross-language component contracts (complete)

- `kosika.components` defines the strict `kosika.component/v1` manifest and the
  language-neutral `json-stdio/v1` call/result protocol.
- A contract pins component/tool version and identity, exact input/output
  schemas, access class, required effects and capabilities, declared errors,
  termination deadline, and implementation artifact SHA-256.
- `bind_component(...)` compares the contract against the checked Kosika
  `ToolDecl` and generated record schemas, requires explicit host grants, and
  produces a normal `tool_bindings` callable. No parser, checker, or runtime
  execution fork was added.
- Artifacts must be relative regular files inside the component root and no
  larger than 16 MiB. Every call re-hashes the artifact, invokes the
  host-selected runtime without a shell, bounds request/response capture to
  1 MiB, validates both JSON boundaries, rejects undeclared errors, and kills
  the process at the contract deadline.
- `kosika cli component check` validates contracts and artifacts without
  executing them. `kosika cli run` accepts one component contract, optional
  runtime executable, and repeatable effect/capability grants.
- `examples/component_uppercase.kos` and
  `examples/components/uppercase.js` exercise a JavaScript implementation
  through the existing typed workflow tool boundary.
- 11 focused tests plus 8 validation subtests cover deterministic identity,
  path/digest/version/deadline validation, exact declaration and host-grant
  matching, workflow execution, declared and undeclared errors, invalid output,
  forced timeout termination, JavaScript execution, and both CLI surfaces.
- The artifact hash identifies bytes; it does not prove behavior. Processes
  inherit host OS authority. Remote transport, native in-process ABI, artifact
  signatures, component packaging, and sandboxing remain future work.

K01 through K09 are complete: the capability matrix, verification records,
versioned language and host contracts, structured diagnostics, development
interface, bounded authoring loop, its evaluations, cross-language component
execution, and audience-adaptive maps above are the baseline. Subsequent work
must update the matrix and contracts when a status or behavior changes.

## K09 audience-adaptive maps (complete)

- `kosika.audience_maps` renders one checked project for one named audience
  (`outsider`, `overview`, `operator`, `reviewer`, `maintainer`) through
  `kosika.audience-map/v1`.
- Four evidence classes stay distinct and are never collapsed: `checked` from the
  verified AST, `declared` from a K08 component contract, `observed` from a
  completed hash-matched trace, and `human` for explicit purpose notes. There is
  deliberately no `inferred` class.
- A component contract is validated against a checked tool before it is shown:
  name, operation, access, and both schemas must match, or the map fails closed.
- Executing a component does not make its contract `observed`. Declared authority
  is a claim by the component; the artifact hash identifies bytes, not behavior.
- A trace whose recorded program hash does not match the checked program
  contributes no entries and records `trace-program-hash-mismatch`; attaching one
  program's behavior to another is the collapse this module exists to prevent.
- `coverage` reports `checked_shown` against `checked_total`, so a narrowed
  audience reads as a deliberate omission rather than a smaller program.
- `verify()` re-derives the digest and rejects unknown evidence classes, missing
  source links, mislabelled human notes, duplicate ids, and edges referencing
  omitted entries. `kosika.cli audience-map` runs it before printing and exits 2
  on any problem, so an unverifiable map is never emitted.
- 57 focused tests cover all five audiences, determinism across repeated builds,
  comment-safe source links, per-audience detail filtering, contract mismatch and
  absent-tool rejection, trace hash mismatch, unreadable and missing traces,
  purpose-note validation, all eight `verify()` failure branches, and the CLI
  surfaces. Branch coverage for `kosika/audience_maps.py` is 95%.
- The evidence-merge rule is tested directly because no current example makes a
  checked node and an observed node share an id; leaving it unexercised would
  have made the central claim untested.
- Fixed in this slice: an out-of-root entry raised a bare `ValueError` from a
  function whose contract is `AudienceMapError`, and a duplicate `relative_to`
  computation existed for the same value.
- Not claimed: the map describes what the checker verified, what a contract
  claims, and what one recorded run observed. It is not a behavioural proof of a
  component implementation, and it is not a semantic understanding of foreign
  source.

The next tasks are ordered by dependency:

1. **K02 — Dependable first-use path (complete).** `.kos` is standardized across
   the workbench contract and the real-browser open → check → run → map → replay
   path passed. CLI discovery now lists every currently registered command.
2. **K03 — Supported language contract (complete).** `KOSIKA.md` now publishes
   versioned grammar, typing, execution, capability, and replay semantics, and
   `tests/test_language_contract.py` executes the representative examples.
3. **K04 — Structured diagnostics (complete).** Stable codes, truthful source
   spans, repair data, authoritative imported-program diagnostics, and
   repair-oriented tests now share `kosika.diagnostics/v1`.
4. **K05 — Machine-readable development interface (complete).** Project
   inspection, K04 checking, bounded execution, validated trace inspection, and
   deterministic maps now share `kosika.development/v1`.
5. **K06 — Bounded AI authoring loop (complete).** `kosika.authoring/v1` runs
   intent → check → propose → repair → run → inspect with host-held authority,
   proposal-only output, and a bounded, fully recorded attempt budget.
6. **K07 — AI authoring evaluations (complete).** `kosika.authoring-evaluation/v1`
   measures clean-check reach, unnecessary edits, attempts, calls, tokens,
   provider and whole-case latency, behavioral equality with the unmutated
   program, and seven kinds of authority violation, with a floor and a ceiling
   evaluator.
7. **K08 — Cross-language component contracts (complete).**
   `kosika.component/v1` versions typed boundaries, authority requirements,
   errors, termination, and pinned artifact identity for local process
   implementations.
8. **K09 — Audience-adaptive maps (complete).** `kosika.audience-map/v1` renders
   one project per audience with `checked`, `declared`, `observed`, and `human`
   evidence kept apart, counted omissions, hash-gated trace observations, and an
   integrity check that blocks output.
9. **K10 — Executable swarm semantics (K10a complete).** Define typed messages,
   scheduling, state ownership, rounds, consensus, termination, failures, and
   aggregate budgets. K10a makes the current limit explicit; K10b makes one swarm
   invocable.
10. **K11 — Approval-controlled AI changes.** Apply exact checked proposals only
    after authenticated host review; resolve workflow-agent mutation integration.
11. **K12 — Reproducible delivery (local slice exercised).** Build wheel and
    source artifacts containing the CLI, LSP, and workbench assets; install the
    wheel outside the checkout; and smoke check, run, replay, LSP, and workbench.
    Published releases and remote execution remain planned.

**Active task: K10c.** K10b (below) made one swarm invocable under a one-use
approval with deterministic rounds and an aggregate budget. K10c is what K10b
left out: typed messages between members, state carried from one round to the
next, and a termination judgment over observed evidence rather than result
equality. Swarm output must not acquire authority the way model output cannot.

## Reliability: evidence (built; measured above)

- `classify`, `extract`, and `decide` accept `with evidence`. The model is
  asked for the record plus quotes; `kosika/evidence.py` checks every quote
  against the step's input text and rejects the answer otherwise. The failure
  is an ordinary broken answer, so `otherwise` recovers from it.
- The step's value and type are unchanged. Quotes are stored on the
  `model_return` trace event, and replay verifies them again from the recorded
  input, so a trace cannot claim evidence its input does not contain.
- Programs without `with evidence` keep the same tree and hash.
- 15 tests in `tests/test_evidence.py`, all with scripted answers.
- Not claimed: a quote proves the words were in the input, not that they
  support the answer. A model can quote a real sentence and still conclude
  wrongly. Number values are not compared with their quote. The program
  cannot read the quotes yet; they are in the trace only. Traces with
  evidence are not readable by an older Kosika (they fail closed).

## Why the assistant was slow, and what was done (2026-10-07)

- Reported by the user: "hello" took 40 seconds and more.
- Measured: Ollama runs qwen2.5:7b entirely on the CPU here. It reads about
  16 tokens a second and writes about 5.5. Loading the model after it has
  been idle takes about 16 seconds. And the assistant asked the model twice
  for an unrecognised message: once to sort it, once to answer.
- Changed: the assistant no longer asks the model to sort a message; plain
  words route everything and anything unrecognised is one question.
  Greetings and thanks are answered in code. Answers are capped at 160
  tokens. `kosika chat` now loads the model before the first prompt and says
  so ("Getting the model ready... Ready in 16 s.").
- After, from a cold start: "hello" 0.3 seconds (was 10.9 warm, 40 or more
  cold), "thanks" 0.2 (was 14.9), a short question 18 then about 9 seconds
  once warm.
- Also fixed: the instruction telling the model it cannot browse made it
  refuse a plain request for help with physics. It now answers from its own
  knowledge.
- Not changed, and the real limit: a question still costs 9 to 25 seconds
  because of the hardware. A smaller model, a graphics card, or a hosted
  model would each be faster. Answers do not appear word by word as they
  are written; that would make the wait feel shorter and is not built.

## Reading the web, on chosen sites (complete; tried on Wikipedia)

- `source system "web"`, operation `fetch` (`kosika/web.py`): input
  `{url}`, output `{url, title, text}`. May be `access: observe`. Bound only
  when the runner passes `--allow-site SITE` to `run`, `chat`, or `app`.
- One `https` GET on the standard port to an allowed host or a subdomain of
  one. No IP addresses, no credentials in the address, no cookies, no body.
  Redirects are checked against the allowed sites before they are followed.
  At most 2 MiB, text pages only, 20 seconds, reduced to readable text of at
  most 20,000 characters with scripts, styles, and menus removed.
- The assistant gained `news` (Wikipedia's current events page) and
  `look up TOPIC` (a Wikipedia article), each summarised with its source.
- Tried 2026-10-07 with qwen2.5:7b and `--allow-site en.wikipedia.org`:
  both worked and named their page. `http://`, another site, an IP address,
  a look-alike host, and an address with a user name were refused. Without
  `--allow-site` the assistant said it could not read the page.
- 15 tests in `tests/test_web.py`, using a stand-in for the network.
- Not claimed: the summaries took 115 and 158 seconds on this machine, which
  is the local model reading 5,000 characters. Only the first 5,000
  characters of a page are summarised. A summary is the model's reading and
  was not checked against the page. Fetched text can try to mislead a model;
  it cannot make one act, because a thinking step only returns its typed
  answer, but it can produce a wrong summary. A host is checked by name
  only, not by where it resolves. There is no search: the program must know
  the address. No robots.txt handling and no caching.

## Swarms that talk: boards (complete; one live run)

- `board NAME of Record`, `post value to NAME as AGENT`, and `read NAME`
  (`kosika/board.py`). Lowered to two ordinary tools, like memory. A run gets
  its own board automatically; `run_swarm` keeps one for the whole swarm run,
  which gives swarm rounds the shared state they lacked.
- `kosika story` and the app describe posts and reads in words, and the app
  lists a program's boards.
- `kosika swarm` gained `--model`, `--models`, `--model-url`,
  `--model-protocol`, and `--model-timeout`; before this a swarm could only
  run on saved answers.
- First live swarm, 2026-10-07, qwen2.5:7b through Ollama,
  `my_examples/studio_swarm`: a writer and a critic over three rounds, six
  model calls, 116 seconds, under one ledger-claimed approval. In rounds 2
  and 3 the writer's prompt contained the critic's remark from the round
  before, read from the board.
- 13 tests in `tests/test_board.py`.
- Not claimed: this is message passing, not agreement. There is no quorum or
  consensus, and the swarm still stops at a fixed point or the round cap. In
  that live run the third draft was worse than the second: the model wrote
  the critique into the line. Passing messages does not make the result
  better by itself. Agents still run one after another. The author on a post
  is asserted by the program, not proved. A swarm run cannot be told by
  `kosika story`, which reads a single run.

## Acting from a chat, with approval each time (complete for one action)

- `kosika chat --approve`: a turn that reaches an `execute` tool stops with
  the pending action, the person is shown the tool and its exact arguments,
  and on yes the turn resumes from that step under a per-turn journal
  (`chat.chat_loop`, reusing `ExecutionSession` and `ApprovalRecord`). The
  approval is bound to that call's plan and action hashes. On no, the turn
  ends with nothing done. Nothing carries over to the next turn.
- New files operation `create`: writes a new file and fails if anything is
  already at the path, so it can add but never destroy.
- A step reached only through a `match` branch no longer needs an approval
  session for the run to start. In exchange there is now a guard at the call
  itself: an `execute` tool never runs with no session. Before this, that
  was enforced only by the up-front check.
- The assistant gained `write PATH: TEXT` and `save notes to PATH`, using
  `create` only.
- Tried in a scratch folder with qwen2.5:7b bound: an approved file was
  created; a refused one was not; an approved overwrite of an existing file
  and an approved `../escape.txt` were both rejected by the tool; the
  existing file was unchanged.
- 14 tests in `tests/test_chat_approval.py`; two more in the assistant.
- Not claimed: only creating a text file. No edit, move, delete, or running
  a program from a chat. At the time the Kosika app could not approve
  anything (Kosika Studio, below, later added that). The approval is typed at the terminal, so it is
  only as trustworthy as whoever is at the keyboard. This was not tried on
  the real `Aryan_Private` folder, only on a scratch folder.

## The app (first version)

- `kosika app FOLDER [--model NAME] [--allow-folder FOLDER]`
  (`kosika/app.py`, `kosika/app_assets/index.html`): a local page listing
  every runnable program under a folder as a card, and for each one its
  facts, its workflows drawn step by step, a chat or JSON run box, its tests,
  its past runs told by `kosika story`, and its code.
- No build step and no dependencies: one HTML file with plain JavaScript,
  served by Python's standard library. Text reaches the page only as text,
  never as markup.
- Authority is the starting command's. No model flag means view and test
  only. Tools whose access is `execute` are never bound. The server binds
  127.0.0.1, checks the Host and Origin headers, and requires a random token
  on every request.
- Tried on `my_examples` (11 programs) with qwen2.5:7b: the assistant's nine
  tests ran from the page's API, a game move ran in 0.3 seconds with its
  story, and requests without the token, with a wrong token, another Host,
  another Origin, an agent outside the folder, and a trace outside the agent
  were all refused.
- 15 tests in `tests/test_app.py`.
- Not claimed: the page was checked through its data and one screenshot of
  the home screen, not clicked through tab by tab by a person. It cannot
  edit code. It shows a declared swarm's members but no swarm running,
  because swarms do not yet exchange messages. One run at a time. Runs it
  starts write trace files into the agent's `runs` folder and are never
  cleaned up. It is separate from the older `kosika.workbench` editor, which
  predates today's language and was not updated.

## The assistant can look at files (complete); changing them is not built

- A `source system "files"` tool whose operation is `read` or `list` may now
  be `access: observe`: it needs the runner's folder grant and no approval.
  `write` and every shell tool still must be `execute`.
- `kosika chat --allow-folder FOLDER` binds those read-only tools. A chat
  still cannot write a file or start a program.
- The assistant gained `list`, `read`, and `summarize` for paths inside the
  granted folder. Tried on `Aryan_Private` with qwen2.5:7b: listing and
  reading answered in 0.3 seconds; a summary of a 3,500-character file took
  28 seconds; `../secret.txt` and `C:/Windows/win.ini` were refused.
- A model that does not answer in time now fails its step as a provider
  failure instead of escaping as a socket error, which had ended a chat.
  `--model-timeout` sets the wait (60 seconds for `run`, 300 for `chat`).
- 6 tests in `tests/test_readonly_files.py`; three more tests in the
  assistant itself.
- Not claimed: nothing can be changed from a chat. Approval inside a chat
  (write, move, delete, run) is the next piece and is not built. Reading
  sends a file's text to whichever model the runner bound; with a local
  model it stays on the machine, with a hosted one it would not. Only UTF-8
  text files up to 1 MiB can be read.

## A laptop assistant, and what it needed (first version)

- Building an assistant showed two gaps, now closed:
  - A workflow could not do different things for different requests. A step
    may now choose between calls: `x = match value { case "a" { call ... }
    case _ { ... } }` (`Step.subject`, `Step.branches`,
    `checker._check_branches`). Every branch is authorized before the run.
  - A program could only be run once per command. `kosika chat` runs one
    workflow per typed line (`kosika/chat.py`); the program keeps the
    conversation in memory.
- Also: the right side of `and` / `or` now uses what the left side proved
  about an optional (`n.exists() and n > 0`).
- `my_examples/assistant/main.kos`: routing by plain words first and by the
  model only when unsure, chat with the last six lines of conversation,
  notes, and tic-tac-toe played entirely in code. Six tests in the language.
- First real conversation, 2026-10-06, qwen2.5:7b through Ollama: game moves,
  notes, and the refusals answered in 0.1 to 0.2 seconds because no model is
  asked; chat answers took 20 to 23 seconds each, and the first model call
  69 seconds while the model loaded.
- 15 tests in `tests/test_chat_and_step_match.py`.
- Not claimed: the assistant has no internet, so no news. It cannot open,
  change, or run anything on the laptop; that needs the approval flow inside
  a chat, which is not built. It does not reach other AI tools. Its speed on
  questions is the local model's speed. Voice is not built. A `match` branch
  cannot carry its own retry, vote, or fallback.

## Understanding and starting agents (complete)

- `kosika story TRACE` (`kosika/story.py`) narrates a run from its trace
  alone: steps, answers, evidence, rejections and retries, votes and which
  model answered each ballot, memory reads and writes, fallbacks, and cost.
  It states whether the trace replays. `--json` gives the same as data.
- `kosika new DIR --kind support|expense|notes` starts from one of three
  working agents (`kosika/agent_starters.py`), each with tests in the
  language and a README.
- The mind map shows a step's `retry`, `vote`, and the models that vote.
- 9 tests in `tests/test_story.py`.
- Not claimed: the story does not expand a called workflow or an agent's
  tool loop; it reports their result. Tokens are those of the accepted answer
  of each step, not of rejected attempts or losing ballots. The three agents
  were each run once on qwen2.5:7b; that is a demonstration, not a
  measurement.

## Memory (complete; first run on a live model done)

- `memory NAME of Record`, `recall NAME under key`, and
  `remember value in NAME under key` (`kosika/memory.py`). Lowered to two
  ordinary tools and a generated agent, so nothing else in the pipeline
  needed to learn about memory.
- Granted by the runner with `kosika run --memory FOLDER`; one JSON file per
  memory, written whole and swapped in. Refused before the run starts when
  not granted. Tests in the language get a fresh in-process memory each.
- First real use, 2026-10-06, qwen2.5:7b through Ollama: the support desk
  agent in `my_examples/agents/support_desk` greeted a new customer, and on a
  second run recalled them ("Welcome back", ticket count 2). The second trace
  replayed.
- 15 tests in `tests/test_memory.py`.
- Not claimed: there is no `forget`, no listing or search of a memory, and no
  recall by meaning; a key must match exactly. Two runs writing the same
  memory at once can lose one write. A memory must be declared in the module
  that uses it. Nothing is encrypted.

## Sharing between devices: packs (complete, first step only)

- `kosika pack make | check | unpack` (`kosika/pack.py`). A pack holds every
  module and the program hash. Checking verifies each module's SHA-256,
  reloads the program in a temporary folder, compares the program hash, and
  reports the models and tools the program would need. `--test` runs the
  pack's own tests.
- A pack holds no binding, approval, or credential, so authority stays with
  the receiving device. Module paths that could escape the folder, unused
  extra modules, and a non-empty unpack target are refused.
- 12 tests in `tests/test_pack.py`.
- Not claimed: a pack is not signed, so it proves what the program is and
  not who sent it. Devices are not connected live; one agent cannot yet call
  an agent on another device. Nothing keeps two devices in step after the
  first copy.

## Model routing (built; run once on two local models)

- A program can declare several models, mark one `default model NAME`, and
  send each thinking step to one with `using NAME`.
- `using a, b, c` on a `vote N` step spreads the ballots over those models
  (`Step.voters`), so agreement is between different models instead of
  repeats of one. The trace records which model answered each ballot.
- `kosika run --models FILE` grants a different real model, by URL or by
  command, to each declared model. The file is the runner's; a `.kos` file
  only names roles.
- 17 tests in `tests/test_routing.py`, using scripted providers and stand-in
  command programs.
- Not claimed: no run against two real models yet. There is no automatic
  routing by difficulty or cost; the author picks the model for each step.
  `use_model` steps and agents cannot vote across models, only thinking steps.

## Reliability: voting (built, not yet measured)

- `vote N` (3, 5, 7, 9) on a model step asks N times and accepts only what
  more than half agree on (`runtime._ask_with_votes`, `Step.votes`). Agreement
  is on the label, the answer, or the whole record. It composes with evidence
  and retry inside each ask, and with `otherwise` when there is no majority.
- The tally and the rejected ballots are stored on the `model_return` event.
- 12 tests in `tests/test_vote.py`.
- Not claimed: no measurement on a live model. Voting cannot help a model run
  without randomness, and agreement between runs of one model is weaker
  evidence than agreement between different models, which is not built.
  Replay keeps the tally but does not re-derive it.

## Tests in the language: tools and checking (complete)

- `answers` also scripts a tool whose access is `observe`. A tool that
  changes the world cannot be scripted, because running one needs a recorded
  approval.
- `testing.check_tests` checks names and types in every test without running
  it, and the module loader calls it, so `kosika check` reports a mistake in
  a test (`check/test`).
- 5 more tests in `tests/test_kosika_tests.py`.

## Tests in the language (complete)

- `test "name" { answers / name = ... / expect ... }` and `kosika test FILE`
  (`kosika/testing.py`). A test runs functions and whole workflows on scripted
  model answers, so thinking steps, evidence, retry, `otherwise`, and budgets
  can all be tested with no model.
- Tests are outside the program hash: adding one changes no trace or
  approval.
- A failure names the line, the expectation as written, and both sides of a
  comparison.
- 15 tests in `tests/test_kosika_tests.py`.
- Not claimed: there is no setup shared between tests, and no test of what
  a run's trace contains.

## Algorithms: larger limits (complete)

- Arrays built by methods may hold 1,000,000 elements (was 4,096), maps
  100,000 entries (was 4,096), and `recurse N` and the call depth go to 128
  (was 32). Replay accepts the same depth.
- 128 is measured, not chosen: the Python-hosted interpreter fails between
  200 and 250 nested calls, so 128 leaves room for deep expressions.
- Not claimed: deep recursion. An algorithm that needs thousands of nested
  calls must be written with a loop.

## Algorithms: arguments by position (complete)

- A call may leave every argument unnamed: `gcd(48, 18)`,
  `text.slice(0, 3)`, `items.set(0, 9)`. `sugar.resolve_unnamed` names them
  from the callee's parameters, so a positional call and the named call are
  the same program with the same hash.
- Kept from before: when every unnamed argument is a variable or field named
  like a parameter, they match by name, so same-named values passed in
  another order are never silently swapped. Everything else goes by position.
- Naming only some arguments of a call is still refused.
- `var items = [1, 2, 3]` now infers `Number[]` from its items. An empty
  literal, or items of different types, still needs a written type.
- 11 tests in `tests/test_positional_arguments.py`.
- Not claimed: no default parameter values and no variable-length arguments.

## Algorithms: loops that can do real work (complete)

- Found by writing binary search, gcd, Fibonacci, a prime sieve, and bubble
  sort: there was no `while`, no way to count, no `+=`, no element update,
  and every `for` stopped at 128 elements (512 at most).
- Added `while`, `for i in a to b` / `a until b` (Number methods `to` and
  `until`), `repeat` with any Number count, `+=` `-=` `*=` `/=`, and
  `items[i] = v` / `map["k"] = v` (array method `set`). None adds a tree node:
  `while` is a repeat that breaks, ranges are method calls, and the updates
  are assignments, so the checker, traces, and tools needed no change.
- Loops are bounded by a run-wide budget instead of a small per-loop cap:
  `ExecutionLimits.max_loop_iterations`, default 1,000,000. A `for` enforces
  `limit N` only when it is written.
- 20 tests in `tests/test_writing_algorithms.py`.
- Behaviour change: a `for` over more than 128 elements used to fail and now
  runs. A program that wrote `limit N` gets a different program hash
  (`capped`); programs without one keep theirs.
- Not claimed: speed. Kosika is interpreted in Python, so a loop here is
  many times slower than the same loop in Python. `items[i] = v` copies the
  array, which makes an in-place sort quadratic in copying. Arrays built with
  Recursion stops at depth 128.

## Reliability: budgets in the language (complete)

- `budget N model calls, N tool calls, N seconds` inside a workflow
  (`WorkflowDecl.budget`). `execution.within_budget` lowers the caller's
  limits for the run; it can never raise one. Retries count, called workflows
  are covered, and the mind map shows the budget on the workflow.
- 11 tests in `tests/test_budget.py`.
- Not claimed: there is no budget in money or tokens, because Kosika does not
  know a model's price and not every provider reports token use. A called
  workflow's own budget is ignored, and `kosika swarm` does not apply a
  workflow budget.

## Reliability: measurement of evidence and retry (run on two local models)

Run on 2026-10-06 with Ollama 0.35.1 on this machine.
`benchmarks/thinking/evidence_measure.py` extracts an `Order` with three
optional fields from 16 hand-labelled tickets, 48 fields in all, of which 19
are facts the ticket does not state. One run per ticket and mode.

| Model and mode | Right | Invented | Wrong | Missed | Fields in rejected runs | Calls | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| qwen3:1.7b alone | 39 | 6 | 0 | 0 | 3 | 16 | 355 |
| qwen3:1.7b, evidence only | 20 | 3 | 1 | 0 | 24 | 16 | 1852 |
| qwen3:1.7b, evidence + retry 2 + fallback | 34 | 5 | 2 | 7 | 0 | 28 | 2165 |
| qwen2.5:7b alone | 39 | 6 | 0 | 0 | 3 | 16 | 179 |
| qwen2.5:7b, evidence + retry 2 + fallback | 43 | 0 | 0 | 5 | 0 | 22 | 434 |

What this shows:

- On the larger model the guards removed every invented value (6 to 0) and
  raised correct fields from 39 to 43. The price was 5 fields left empty that
  the ticket did state, and 2.4 times the running time.
- On the small model the guards did not help: fewer correct fields (34
  against 39) and nearly as many invented (5 against 6), at six times the
  running time. Half of its evidence-only runs were rejected, mostly because
  it left a field out instead of writing it as null, and it sometimes copied
  the quote into the value ("Priya Nair here").
- So the guards are not a way to make a weak model strong. They need a model
  able to follow the evidence format, and then they trade invented answers
  for empty ones.
- The inventions that survived on the small model are misattributions whose
  words are in the ticket: the support agent's name or a cafe as the
  customer, a friend's price as the amount. A quote check cannot catch
  those. This is the limit stated in `kosika/evidence.py`, now observed.

What it does not show: 16 tickets and one run each is too few for a rate, and
the tickets and labels were written by the same author as the feature, to be
tempting. Scoring is exact match, so "Priya Nair here" counts as wrong. Two
models from one family were tried. `vote` and model routing were not
measured.

Fixed afterwards: a model answer that omits a field is now read as null when
the field's type allows it (`model_steps.fill_absent`, 8 tests in
`tests/test_absent_fields.py`; replay applies the same reading). Re-measured
on qwen3:1.7b. The run was cut off after 12 of the 16 tickets, so the
comparison is over those 12 (36 fields):

| qwen3:1.7b, 12 tickets | Right | Invented | Wrong | Missed | Rejected runs |
| --- | --- | --- | --- | --- | --- |
| alone, before | 28 | 5 | 0 | 0 | 1 |
| alone, after | 32 | 4 | 0 | 0 | 0 |
| evidence only, before | 15 | 2 | 1 | 0 | 6 |
| evidence only, after | 26 | 6 | 1 | 3 | 0 |
| all guards, before | 28 | 4 | 2 | 2 | 0 |
| all guards, after | 25 | 4 | 3 | 4 | 0 |

The fix did what it was for: no run was rejected any more. It did not change
the conclusion. With every guard on, the small model is still no better than
it is alone (25 right against 32), and the differences between the two
"all guards" rows are within what one run of a model that answers
differently each time would give.

## Reliability: retry with feedback (built; measured above)

- `retry N` (1 to 5) on a model step re-asks after a rejected answer, adding
  the rejected answer and the reason to the conversation
  (`model_steps.RejectedAnswer`, `Step.retries`). It composes with evidence
  and `otherwise`: quote check, then retry, then fallback.
- Every attempt goes through the execution limits, so `max_model_calls` and
  the deadline bound a retrying step. Rejected answers are stored on the
  `model_return` event.
- 11 tests in `tests/test_retry.py`.
- Not claimed: replay does not re-derive the rejected attempts, it only keeps
  them. A provider or transport failure is not retried. No measurement yet of
  how often a second attempt succeeds.

## Fundamentals: narrowing optionals (complete)

- A test of `name.exists()` makes `name` its plain type where the test is
  known to have passed: the matching branch of an `if` (with `not` and `and`),
  the lines after a guard that returns, breaks, or continues, and the body of
  a `for ... where`. Checker only (`_proved_present`, `_present_after`,
  `_Env.present`); the runtime is unchanged.
- Sound by restriction: only immutable plain names are narrowed. A `var`, a
  field, and an index are not. `or` never narrows its true side.
  `exists`/`value`/`or` still check on a narrowed name, so older code is
  unaffected.
- Found while testing: a nested block could not continue after an `if`, `for`,
  or other expression (`if ... { continue }` then more lines failed to parse).
  A block now holds expression statements like a function body does.
- 11 tests in `tests/test_narrowing.py`.
- Not claimed: no narrowing through `match`, through a helper function that
  tests presence, or of record fields.

## Fundamentals: thinking steps on a live model (first run done)

- `kosika run --model-command PROGRAM ARGS...` (`kosika/command_model.py`)
  grants a command-line program as the provider for every declared model. The
  request goes to standard input; the program runs without a shell in an empty
  temporary folder; tool calling is refused; one fenced JSON block is unwrapped
  and nothing else is repaired.
- First real run, 2026-10-06, `gemini-3.5-flash-lite` through the Gemini
  command-line tool: one ticket ("charged twice ... 30 dollars each time")
  through `classify`, `extract`, `summarize`, and `decide` in one workflow. All
  four answers passed validation on the first attempt (label `billing`,
  customer and amount extracted, decision true with a reason), the run took 33
  seconds, and the trace replayed to the same result.
- 13 tests in `tests/test_command_model.py`, using a stand-in Python script as
  the program.
- Small measurement, same day and model: `benchmarks/thinking/measure.py`
  ran `classify` (four queues) and `decide` over six hand-labelled tickets,
  twelve model calls. Queue 6/6, urgency 6/6, no failed or invalid answer,
  about 15 seconds per ticket. Results in
  `benchmarks/thinking/results/gemini-3.5-flash-lite.json`.
- What that does not show: the tickets were written by the same author as the
  labels and are clear-cut, so this is a smoke test, not an accuracy figure.
  The reported confidence was 1.0 on five of six and 0.95 on the other, so
  the self-reported confidence carried no information here.
- Not claimed beyond that: one model, no hard or ambiguous cases. There is no run of `ask` with tools on a live
  model. The Ollama program is no longer installed on this machine, so no
  local model was tried.

## Fundamentals: early return and `if` without `else` (complete)

- `return value` may appear inside an `if`, `match` arm, or loop body
  (`ReturnExpr`). The checker gives it the type `Never`, requires its value to
  fit the enclosing function's output type, and rejects it outside a function.
  The runtime unwinds to the function and validates the value like the final
  return. Recursion detection sees calls made in an early return.
- `if condition { ... }` with no `else` has `null` as its other branch, so it
  is a do-nothing statement or an optional value.
- A function still ends with a final `return`, so a body cannot fall off its
  end without a value.
- 12 tests in `tests/test_early_return.py`.
- The mind map now draws a step's fallback: an edge from the step to the
  fallback's target, with the ordinary call relation and the label
  `otherwise: computes` (or `otherwise: asks`, and so on), so every view that
  reads relations shows it.

## Fundamentals: `otherwise` (complete)

- `step = call otherwise fallback` gives a step one fallback call
  (`Step.fallback`, absent from the hashed payload when unset).
- Runtime: the step loop is now a queue. A recoverable failure discards the
  step's partial events, marks a durable step as not started, and queues the
  same step with the fallback as its call. The retried step's `step_start`
  event carries `recovered_from`, so traces keep their four-events-per-step
  shape and trace version 5 replay is unchanged.
- Recoverable: `RuntimeError` and `ValueError` raised by the step's work.
  Never recovered: `ExecutionError` (budget, deadline, cancellation),
  `ApprovalRequiredError`, and `PermissionError` (missing bindings).
- Checker: a program with fallbacks is valid when it is valid with every
  fallback removed and also with every fallback swapped in, and each pair of
  calls returns one type. `execute` tools are refused on either side.
- Preflight authorizes the fallback's model and tools before the run starts.
- 14 tests in `tests/test_otherwise.py`.
- Limits: one fallback per step, no chaining; the swapped program is checked
  with all fallbacks at once, not each alone; the mind map does not draw the
  fallback edge yet; a function body still has no way to recover from its own
  failure, and there is no early `return`.

## Fundamentals: gates and error positions (complete)

- Quality gates re-measured on 2026-10-06. MyPy over `kosika` reports no
  issues in 86 files (it was 54 errors in 16 files at K02; most were in the
  removed prototype modules, and the last one was fixed here). Branch coverage
  is 86 percent overall against the 80 percent gate, and 93 percent across the
  twelve core language modules. Ruff passes. 1162 tests pass.
- Checker errors now point at the token they name. When a message names a
  field, variable, method, or argument that occurs exactly once inside the
  owning function or step, the diagnostic's range is that token and
  `data.anchor` is `name`. A name that occurs more than once is ambiguous and
  is never guessed: the range falls back to the local's `let` line when the
  checker named one, and otherwise to the declaration, as before.
- The local type-mismatch message now carries its function, so it has an owner
  and a position.
- 9 tests in `tests/test_error_positions.py`.
- Still open: error handling in the language, early `return`, narrowing of
  optionals, and a run of the thinking steps against a live model.

## Closing the size gap (second measurement)

- Changes, each a short form rewritten to the full language before checking
  (`kosika/sugar.py`), so the checker and runtime are unchanged:
  1. A call's single argument may be unnamed: `double(n + 1)`,
     `use_model assistant("hi")`, `words.join("-")`, `stock.get("pen").or(0)`.
     It binds to the callee's only parameter. Function, model, agent, tool,
     and workflow calls are named by `name_sole_arguments`; built-in methods
     in the checker, which knows the receiver type.
  2. `fn main` with record parameters and a record result is an entry point:
     the one-step `workflow main` is added when the program has none.
  3. A `for` body may be a record literal when the loop's array type is known.
  4. `a.min(b)` and `a.max(b)` on two Numbers.
- A same-named variable argument (`show(input)`) lowers to exactly the tree it
  did before, so those programs keep their hash; a test pins this.
- Changed behaviour: `f(x)` where `f` has one parameter with a different name
  used to be an "unknown argument" error and is now accepted, with a type
  error if the value does not fit.
- **Re-measured** after rewriting the Kosika solutions: Kosika 84 lines (was
  103), plain Python 81, checked Python 108 plus 35 helper lines. Data tasks:
  Kosika 50 (was 69), plain 38, checked 47. Model tasks unchanged at 34, 43,
  and 61. Tokens: Kosika 817 (was 955), plain 633, checked 899 plus 286.
  Bad cases refused are unchanged: 20, 9, and 20 of 20.
- Honest reading: Kosika now matches plain Python in lines overall but is
  still about 30 percent longer on data tasks and 30 percent heavier in
  tokens. What remains is mostly record declarations, which are also what
  makes the checking possible. The Python solutions were not re-tuned; the
  author wrote both sides, so treat the numbers as indicative.
- The diagnostics raise-site pin is now keyed by file rather than line, since
  it broke on six unrelated edits.
- 15 tests in `tests/test_sugar.py`.

## Phase 6: comparison with Python (first measurement)

- `benchmarks/compare` holds six tasks, each with `solution.kos`, `plain.py`
  (the short Python most people write first), and `checked.py` (Python that
  also rejects wrongly shaped inputs and model answers, using a shared
  `pyhelpers.py`). `kosika.comparison` proves all three return the same result
  for the same inputs and scripted model answers, then runs 20 bad-data cases.
  `kosika compare-python` prints the table.
- **Measured:** lines of code are Kosika 103, plain Python 81, checked Python
  108 plus 35 helper lines. Tokens are 955, 633, and 899 plus 286. On the four
  data tasks Kosika is longer (69 lines against 38 plain and 47 checked). On
  the two model tasks it is shorter (34 against 43 and 61). Bad cases refused:
  Kosika 20 of 20, plain Python 9 of 20, checked Python 20 of 20.
- What this supports: Kosika gives the checked behaviour at roughly the size
  of unchecked Python, and is clearly smaller where a model is called.
- What it does not support: "more concise than Python" in general. For plain
  data work Kosika is about 1.8 times the lines of plain Python. The bad cases
  were chosen by the author to be ones Kosika's types cover, six tasks is a
  small sample, a library such as pydantic would shrink checked Python, and
  the comparison ignores traces, replay, budgets, and approvals, which the
  Python versions simply do not have. No model wrote any of these solutions.
- Why Kosika was longer on data tasks, each a candidate fix: every value needs
  a declared record; a one-function program still needs a workflow wrapper; a
  record literal cannot appear in a `for` body, forcing a helper function;
  there is no two-argument `min` or `max`; named arguments repeat names; and
  an optional needs `.exists()` then `.value()` because there is no narrowing.
- 8 tests in `tests/test_comparison.py`.
- Not done: the AI-authoring half of the proof (how often a model writes each
  version correctly). It needs a live model and runs model-written Python on
  this machine, so it waits for an explicit decision.

## Phase 5, second slice: shell and files, approved every time (complete)

- `source system "shell"` and `source system "files"` declare system tools
  (`kosika/system_tools.py`). The checker requires `access: execute` and exact
  record shapes, so the existing rule forces `require approval` on any
  workflow that uses one.
- Three independent gates: the declaration, a host grant (`--allow-folder`,
  plus `--allow-shell` for shell tools), and an approval for each exact call
  through the existing session and ledger. No new approval mechanism was added.
- `kosika run --approve` asks at the terminal before each pending action and
  resumes from the journal, so approved steps are not repeated. Without a
  terminal it pauses with exit code 3, as before. Refusal runs nothing.
- Enforced after approval too: one fixed program per shell tool, arguments as
  a list with no shell, the granted folder as working directory, a 60 second
  limit, clipped output; file paths confined to the granted folder with `..`,
  absolute paths, and symlinks refused; 1 MiB UTF-8 files.
- 18 tests in `tests/test_system_tools.py`, including path-escape and symlink
  attempts, refusal at the first and at a later action, and no terminal.
- Not claimed: this is not an operating-system sandbox, the approver name is a
  label, an approved shell program can do anything that program can do, and an
  agent inside `ask` choosing a system tool has not been exercised.

## Phase 5, first slice: MCP server (complete)

- `kosika.mcp_server` (`kosika-mcp`) serves five tools over stdio JSON-RPC:
  `kosika_guide`, `kosika_check`, `kosika_run`, `kosika_replay`, `kosika_map`.
- It delegates to `check_project`, `load_program`, `run`, `replay`, and
  `build_mindmap`; no language behaviour is reimplemented.
- No authority: sources are text checked in a temporary folder, no tool
  accepts a path, address, or command, and `kosika_run` binds only scripted
  answers supplied by the caller under fixed execution limits.
- 16 tests in `tests/test_mcp_server.py`, including a real client process
  driving the server through initialize, list, run, replay, and an error.
- Not done: connecting it to a live assistant and watching one author a
  program through it; an audience-specific map; multi-file projects.

## Phase 4: thinking steps (complete, not yet run on a live model)

- `classify ... into ...`, `extract R from ...`, `summarize ...`, and
  `decide ... given ...` are workflow steps with built-in typed results
  (`Choice`, the named record, `Summary`, `Decision`). `model name` declares a
  general text model.
- `kosika.thinking.lower_thinking` rewrites each step into an ordinary
  `ModelCall` against a generated `ModelDecl` whose `provider` names the
  declared model. The checker, runtime, traces, replay, budgets, mindmap, and
  bindings therefore needed no second path; only the provider lookup, the
  system instruction, and an answer check were added to the model-call branch.
- Guarantees enforced at run time: a `classify` label is one of the choices;
  `classify` and `decide` confidences are from 0 through 1.
- The module loader lowers once over the merged program, so a model declared
  in one file serves steps in another and built-in records are added once.
- Diagnostics: `check/thinking-model-required`, `check/thinking-record-clash`,
  `check/thinking-unknown-record`.
- 21 tests in `tests/test_thinking.py`, all with scripted providers.
- Not claimed: the four verbs have not been run against a live model. The
  local Ollama server was not running when this was built. Confidence is the
  model's self-report. There is no evidence quoting, retry on a bad answer, or
  memory yet, and `--model` grants one provider to every declared model.

## Program hashes are stable again

- The core-fixes slice claimed that programs written before it kept their
  hash. That was wrong: the tree was equal, but `FunctionDecl.order` and later
  `ForExpr.condition` added keys to the `asdict` form every hash is taken
  over, so hashes of programs with functions or loops changed and older
  traces stopped matching in `explain` and `audience-map`.
- `ast.program_payload` now produces the hashed form and leaves out newer
  fields at their absent value. All seven hash sites use it. Hashes of five
  examples were compared against the code at commit `fc82697` and are
  identical.

## Phase 3: built-in algorithms (complete)

- Scalar arrays: `sort()` (Number and Text), `unique()`, and for `Number[]`
  `sum()`, `min()`, `max()`, `average()`. The last three return `Number?`
  because an empty array has no answer.
- Arrays of records: `sort_by`, `sum_by`, `min_by`, `max_by`, and `group_by`,
  each taking `field: "name"`. The field is a text literal the checker
  resolves against the element record, including its type (`sum_by` needs a
  Number field, `group_by` a Text field), so no lambdas are needed and a wrong
  field cannot reach the runtime.
- Sorting is stable, ties in `min_by`/`max_by` keep the first element, and
  `unique` keeps first-seen order, so results are deterministic.
- 6 tests in `tests/test_algorithms.py`.
- Found while testing: a method cannot follow a bare `for` expression; the
  loop must be parenthesised. Not done: descending sort (use `reverse()`),
  sorting by more than one field, and predicates such as `find` or `count`
  (use `for ... where` with `first()` or `length()`).

## Prototype modules removed

- Removed seven modules that were never part of the language: `cloud.py`,
  `cloud_server.py`, `cli_cloud.py`, `meta_agent.py`,
  `integration_adapters.py`, `backend.py`, and `optimizer.py` (about 2,950
  lines), along with their tests and the `cloud-demo` optional dependency.
  Nothing in the parser, checker, runtime, or CLI imported them. The design
  notes that described them are in `archive/`, and git history keeps the code.

## Phase 2, second slice: optional values and maps (complete)

- **`T?`** is a `T` or `null`. Assignability widens one way only (`T` and
  `null` into `T?`); a conditional or match whose branches are `T` and `null`
  has type `T?`. Methods: `or(default:)`, `exists()`, `value()`. An optional's
  own fields and methods are unreachable until one of those is used.
- **`T{}`** is a map from Text to `T`, with `get` (returns `T?`), `has`, `set`,
  `remove`, `keys`, `values`, and `length`. Literals reuse `RecordExpr` with
  quoted or bare keys, so no new tree node was needed. Maps are capped at 4,096
  entries.
- New optional-returning methods: `Text.to_number()`, `first()`, `last()`.
- Types are still strings with suffixes; `function_types` gained `base_type`,
  `assignable`, and `unify`, and the checker's equality comparisons at
  assignment, argument, field, element, return, and branch sites now use them.
- JSON schemas: `T?` is `anyOf [T, null]`, `T{}` is an object whose
  `additionalProperties` is the value schema; `schema.validate` supports both.
- `break` and `continue` now have the type `Never`, which agrees with any
  other branch. This closes a gap an older test pinned: a loop body
  `if x == "b" { break } else { x }` now checks and runs.
- 18 tests in `tests/test_optional_and_map.py`.
- Known limits: an optional record field must be present as `null` in input
  JSON and in model output (it cannot be omitted); there is no narrowing, so
  `if x.exists() { ... }` still needs `x.value()` inside; map keys are Text
  only; tuples are not a separate type (use a small record).

## Phase 2, first slice: building and slicing (complete)

- `for item in values where condition { ... }` filters as well as maps.
  `ForExpr.condition` defaults to `None`, so loops without `where` parse to the
  same tree as before.
- New methods: arrays gain `append`, `concat`, `reverse`, and `slice`, each
  returning a new array of the receiver's type; Text gains `slice`; Number
  gains `to_text`. Arrays built by methods are capped at 4,096 elements and
  `slice` positions outside the value fail the run.
- 8 more tests in `tests/test_core_fixes.py`.
- Still open in phase 2: maps, optional values, and text-to-number conversion
  (which needs optional values to report failure).

## Simpler syntax (complete)

- **One-line steps.** Inside a workflow, `name = call` is a step. The parser
  expands it to the long form, so both produce the same tree and nothing
  downstream changed.
- **Short arguments.** `f(order)` means `f(order: order)` and `f(question.text)`
  means `f(text: question.text)`. The name is read from the expression, never
  guessed; any other expression must be named (`parse/unnamed-argument`).
- **"Did you mean" hints** on unknown variables, fields, record fields,
  argument names, methods, method arguments, and unknown type or function
  names, from the closest known spelling (`difflib`, cutoff 0.7). No hint is
  printed when nothing is close.
- Errors in a one-line step anchor to that line.
- `kosika new`, `TUTORIAL.md`, and `AGENT_GUIDE.md` now use the short forms.
  The long forms remain valid and every existing program is unchanged.
- 15 tests in `tests/test_simpler_syntax.py`, including tree equality between
  the short and long forms and a traced, replayed run of the short form.
- Not done: the write-from-spec benchmark has not been rerun on a live model
  with the revised guide (the Gemini free quota is 20 requests a day).

## Core fixes from the first benchmark (complete)

- **Function bodies run in written order.** The parser used to hoist every
  `let` and `var` above every statement, so a `let` after a loop silently read
  pre-loop values. `FunctionDecl.order` now records the source order whenever a
  declaration follows a statement, and the checker and runtime walk
  `ast.function_body`. A statement may use only names declared above it. A body
  that already lists declarations first parses to the same tree as before, so
  its program hash, traces, and approvals are unchanged.
- **`else if` chains.** The else branch may be another conditional. A final
  `else` is still required.
- **Remainder `%`**, at multiplication precedence, floored (sign of the
  divisor), failing the run on a zero divisor.
- **Built-in methods** on Text, Number, and arrays (`kosika/builtin_methods.py`,
  one table used by both checker and runtime): `length`, `upper`, `lower`,
  `trim`, `contains`, `starts_with`, `ends_with`, `split`, `join`, `floor`,
  `ceil`, `round`. The parser already produced `MethodCallExpr`; the checker
  used to reject every one. Calls outside the set are check errors with stable
  codes `check/unknown-method` and `check/method-arguments`.
- **Unknown names have a position.** `module/private-symbol` now anchors at the
  first use of the unknown name, lists the names in `data.names`, and says
  "unknown names" in plain words. Plain `kosika check` prints the line and
  column whenever the first diagnostic has a truthful position.
- 22 tests in `tests/test_core_fixes.py`; one older test that pinned "every
  method call is rejected" now pins the closed set instead. The diagnostics
  raise-site coverage check still classifies every site.
- Known limits: a nested function whose parent mixes statements and
  declarations is lowered as before (`lexical.py` rewrites bindings and the
  return expression only). There is no early `return`.

### What blocks writing Kosika's own tooling in Kosika

Kosika's tools (lexer, parser, checker) need things the language does not have.
In rough order of cost:

1. Character access and slicing on Text (`text[i]`, `slice`). Cheap; a lexer
   cannot be written without it.
2. Text to Number conversion, with a way to signal failure. Cheap once item 4
   exists.
3. Growing an array (`append`) or a `for` that filters. Cheap; today an array
   can only be mapped one-to-one.
4. Optional and tagged-union values (`Option`, `Result`, variants). Medium; an
   AST is a tree of variants, and every "not found" needs a typed absent value.
5. Recursive record types. Medium; an expression tree is recursive and `0.1.0`
   forbids recursive schemas.
6. Maps (text keys to values) for symbol tables. Medium.
7. Unbounded or data-bounded loops and recursion deeper than 32. Hard, because
   bounded execution is a core promise; it needs a budget model, not a removal.
8. File reading as a declared capability. Medium; the host adapter exists but
   has no language surface.

Items 1 to 3 are small, self-contained additions. Items 4 and 5 are the real
gate: without them a parser written in Kosika has nothing to return.

## Write-from-spec benchmark (first measurement)

- The question: can an AI author write a whole Kosika program from a spec,
  given only a short guide? The K07 repair corpus cannot answer it, because it
  asks for a one-line fix to an existing program.
- `AGENT_GUIDE.md` (about 1,000 words) is the guide for models. Its claims were
  run against the real parser, checker, and runtime before use.
- `benchmarks/spec/tasks.json` holds 20 specs: 10 pure computation, 4 model
  calls, 4 tools and agents (least privilege, approval gate), 2 agent and swarm.
  Each names its declarations exactly and carries inputs, fixtures, and the
  expected result. `benchmarks/spec/reference` holds a passing program for each.
- `kosika.spec_benchmark` judges a candidate with Kosika only: checker, required
  structure in the checked program, then an exact result from a bounded
  fixture-backed run. Solvers: `reference`, `model` (any endpoint), and
  `command` (a command-line model on standard input, run without a shell in an
  empty temporary folder). Reports use `kosika.spec-benchmark/v1` and keep
  every attempted program.
- **Measured, 2026-10-04, through the Gemini command-line tool:** Gemini 3.5
  Flash-Lite passed 16 of 16 tasks on the first attempt (about 13.5s each);
  the report is `benchmarks/spec/results/gemini-3.5-flash-lite.json`. Gemini
  3.8 Flash passed the other 4 (01, 02, 03, 08) on the first attempt before its
  free daily quota of 20 requests ran out; those four are from the run log, not
  a saved report. 2 of the 16 saved programs equal the reference; the rest are
  the model's own.
- What this supports: with the guide, a small current model writes correct
  Kosika for fully specified, single-file programs, including tools, agents,
  approval gates, and a swarm declaration.
- What it does not support: anything about harder work. The specs name every
  declaration, no task needs more than about 40 lines, and the guide was written
  after the reference programs, so it already warns about the traps they
  exposed. A 100 percent score means the benchmark has no headroom left, not
  that authoring is solved. A local `qwen2.5:7b` run on CPU was stopped as too
  slow and produced no result.
- Language problems found while writing the references and the guide:
  1. `let` and `var` lines all run before any loop or assignment, whatever
     their position. A `let` written after a loop silently reads the pre-loop
     value (the first invoice reference returned discount 0).
  2. `KOSIKA.md` showed `repeat 8 { ... }`; the parser requires
     `repeat 8 times { ... }`. The contract is corrected.
  3. There is no `else if`, no `%`, no array length, and no text functions.
  4. An unknown type or function name reports `unavailable or private symbols`
     with no line number.
- Next: harder specs (underspecified names, multi-step agents, 100+ lines,
  repair of a failing run) until a strong model drops below 100 percent, and
  fix problem 1, which is a silent wrong-result trap.
- 23 tests in `tests/test_spec_benchmark.py` cover every reference, the judge's
  check, structure, run, and result stages, both live solvers through
  stand-ins, bounded attempts, provider failure, task-file validation, and the
  CLI.

## Python SDK (complete)

- `kosika.sdk` builds a program from Python: `App.record`, `model`, `tool`,
  `agent`, `workflow`, `swarm`, and `source`; workflow steps `use_model`, `ask`,
  `execute`, `call`, and `function`. Step methods return a `Ref` to their
  result so later steps can use it; `ref("question.text")` passes any Kosika
  expression, and Python values become Kosika literals.
- It is a source builder, not a second front end. `to_source()` renders `.kos`
  text and `build()` runs `parse` and `check` on it, so the checker stays the
  only authority and no AST is constructed by hand. `save()` writes a file only
  after the program checks.
- The SDK mints no authority: `run()` forwards explicit bindings to
  `kosika.runtime.run`, and an unbound model fails closed.
- 13 tests in `tests/test_sdk.py` prove AST equality between SDK-built programs
  and three hand-written ones (the starter, `examples/research_agent.kos`, and
  `examples/swarm_review.kos`), plus run, trace, replay, an approved swarm run
  of an SDK-built program, literal rendering, 17 refused builder calls, and
  that a checker rejection still surfaces.
- Not claimed: no builder for pure functions, expressions, or imports (use
  `source()` or a `.kos` file); Python only, so a TypeScript SDK would need its
  own renderer; and `bind` cannot be used as an argument name.

## Beginner path (complete)

- `kosika new DIR` writes a four-file starter (`kosika/starter.py`): a typed
  question-and-answer workflow, its input, an offline model fixture, and a
  README. It refuses a non-empty folder, so nothing is overwritten.
- `kosika run FILE --model NAME` is the one-flag live path. Without
  `--model-binding` it grants that provider to every declared model and prints
  the grant to stderr. The endpoint is `--model-url`, then `KOSIKA_MODEL_URL`,
  then a local Ollama server; `--model-protocol` selects `openai` or `ollama`.
  A live model is still never enabled implicitly: no flag, no binding.
- `--model-binding NAME --model M --model-url URL` keeps its single-model
  meaning. `--model-url` or `--model-binding` without `--model` is refused.
- A run that fails because a declared model has no binding now says how to
  supply one (`--model` or `--model-fixture`).
- `TUTORIAL.md` walks the path; each step was run against the starter.
- Exercised live: the starter returned a schema-valid `Answer` from
  `qwen3:1.7b` over local Ollama with `--model` alone (26s on the `openai`
  protocol, 16s on `ollama`). The answer text was wrong about what Kosika is;
  the run proves the path and the typed boundary, not the model's knowledge.
- 15 tests in `tests/test_beginner_path.py` cover the starter (create, check,
  offline run, replay, no overwrite), endpoint precedence, both protocols,
  refused flag combinations, the missing-binding hint, and a full CLI run
  through a stand-in provider.

## K10b approved swarm runs (complete)

- `kosika.swarm.run_swarm` runs a declared swarm's workflow once per round, in
  order, up to `max_rounds`, and reports `fixed_point` when a round returns the
  same result as the previous one. Results use `kosika.swarm-run/v1`.
- One approval authorizes one run. `swarm_plan_hash` covers the swarm, its
  members, workflow, round cap, and the checked program hash. The claim is
  committed to the approval ledger before any member executes; a missing,
  unattributed, or mismatched approval, or a missing ledger, runs nothing.
- The ledger records the outcome: `succeeded` with the final result hash, or
  `failed` with the error type. A failed round does not release the approval,
  because members may already have acted.
- One `ExecutionControl` is shared by all rounds, so step, model-call, and
  tool-call limits are aggregate.
- `kosika swarm` exposes `--plan` (print the hash, run nothing) and the approved
  run, with `--model-fixture`, `--tool-fixture`, or a pinned MCP server.
  `examples/swarm_review.kos` runs end to end with the two fixture files beside
  it and stops at a fixed point after two rounds.
- `run` still refuses a swarm; its error, the `SwarmDecl` docstring, and
  `explain` now point to `kosika swarm`. The mindmap attribute
  `executable: false` and the `would_run` relation are unchanged and mean "not a
  `run` entry point".
- 17 tests in `tests/test_swarm_run.py` cover fixed-point and round-cap
  termination, digest determinism, ledger outcomes, one-use approval, the four
  refusal codes, plan-hash sensitivity to the program, a failed round spending
  the approval, the shared budget, missing bindings, and six CLI paths. Four
  wording assertions in the older swarm tests were updated; no enforcement was
  loosened. Full suite: 967 tests passed; Ruff lint passed.
- Not claimed: members do not exchange messages or share state, there is no
  consensus or quorum, the approver name is a label rather than an authenticated
  identity, and one plan hash can be approved only once per ledger.

## K10a swarm declarations are honest about not running (complete)

- The gap this closes: `examples/swarm_review.kos` passed `kosika check`, and the
  mindmap stated that the swarm *runs* `review`, while nothing in `cli.py`,
  `runtime.py`, or `approval_ledger.py` referenced a swarm at all. Running it
  reported `Unknown workflow 'main'`, which reads as a forgotten `main` and sends
  the reader looking for the wrong bug.
- `require approval` was already mandatory at parse time and re-checked at
  `checker.py`, so the declaration's one meaningful field was a human gate that
  nothing could grant. The declaration is kept, because a checked and mapped
  intent is still true, but it now grants nothing and says so.
- `runtime._entry_point_error` reports a swarm-only program honestly: it names the
  swarms, states that a declared swarm is not an executable entry point, and lists
  the workflows that can actually run. A program without a swarm keeps the plain
  `Unknown workflow` message.
- Swarm nodes carry `executable: false` as a mindmap attribute, so the limit is
  machine-readable rather than a matter of prose. The swarm-to-workflow relation
  is `would_run`, not `runs`; `design.py` maps it to the existing `invokes` design
  edge, because that view describes intended architecture.
- `explain` now reads "declares" rather than "runs" and adds an explicit
  "Declared only" line. The audience map inherits both, since it is built from
  mindmap attributes.
- `SwarmDecl`'s docstring states that a declared swarm is not executable, so the
  limitation is visible to anyone reading the type.
- 12 new tests in `tests/test_swarm_declarations.py` cover the entry-point error,
  the plain message when no swarm exists, the unreachable no-workflow branch
  exercised on the value directly, the mindmap attributes and relation, map
  determinism, and that `require approval` is still enforced. Three existing
  assertions in `tests/test_swarm.py` were updated to the truthful relation and
  wording; no enforcement was loosened.
- Not claimed: no swarm executes, no approval gate is enforced at runtime, and no
  round, consensus, or budget semantics exist. This slice only stops the language
  from implying otherwise.

## Kosika Studio

- `kosika studio FOLDER [--model NAME] [--allow-folder FOLDER]
  [--allow-site SITE]` replaces the first app's page. `kosika app` serves
  the same page in a browser tab; `studio` opens it as its own window with
  Edge or Chrome's `--app=` when one is installed (`app.open_window`).
- Live runs. `runtime.run(..., on_event=...)` tells a watcher about each
  event as it is recorded, with the nesting depth of the workflow that
  produced it (`runtime.LiveEvents`). Watching changes nothing: the same
  events are written in the same order, and a watcher that raises is
  ignored. The app turns this into jobs: `POST /api/start`, `GET /api/poll`,
  `POST /api/decide`, `GET /api/memory`. Only the event kind, names and
  counts travel to the page while a run is going, never the values.
- Approval in the window. A tool whose access is `execute` is now bound in
  the app (shell still never is), and the runtime still refuses it without
  an approval record. The run pauses, the page shows the one action and its
  arguments, and Allow once adds a record for exactly that action hash. No
  answer within ten minutes, or Refuse, ends the run with nothing changed.
  The older `POST /api/run` waits for the run and refuses any approval.
- The page: sidebar (Assistant, Agents, Runs, Permissions); per agent the
  tabs Live, Workflow, Parts, Data, Tests, Runs, Code; in Live a chat or a
  JSON run box beside Live flow, What it did, Requests, and Memory. Story
  steps for model calls now carry what was sent, the raw reply, and the
  model, which is what Requests shows.
- Tried on `my_examples` with qwen2.5:3b through the built-in browser: a
  question went through the assistant with the nested `chat` workflow
  unfolding and the answer arriving (about 20 seconds, one model call, 279
  tokens); `write hello.txt: ...` paused on the card and, after Allow once,
  created the file in a scratch folder; every tab and sidebar page was
  opened with no console errors.
- 22 tests in `tests/test_app.py` (7 new) and 7 in `tests/test_studio.py`.
- Not claimed: the answer still arrives all at once, not word by word. A
  swarm's rounds are not animated; only one agent's run is. One run at a
  time. The own-window path was unit tested but not seen opening on this
  machine, since the try-out used the built-in browser. It is a local page
  in a browser window, not an installed program with an icon. The page
  cannot edit code or create an agent. Approving is only as trustworthy as
  whoever is at the screen. Runs started here leave trace files in the
  agent's `runs` folder and write to its `memory` folder, as the try-out
  did for the assistant.

## Studio: streaming, programs, swarms, and building

- Word by word. A provider with `on_text` set asks for a streamed reply
  (Ollama's newline JSON or the OpenAI event stream), hands each piece of
  text to the listener, and returns the same `ModelResponse` as without it,
  so traces, replay and validation are untouched. Studio sets the listener
  and sends the pieces to the page as `model_text` events, which are never
  written to a trace. The page shows the readable part of the JSON answer
  as it grows.
- Programs. `kosika studio --allow-shell` (needs `--allow-folder`) binds
  shell tools. Nothing else changed about them: one named program per tool,
  arguments as a list, inside the granted folder, 60 seconds, and an
  approval for every call. The approval card names the program and shows
  the command. Without the flag a shell tool is not bound and the run fails.
- Swarms. `run_swarm(..., on_event=...)` reports each round's events live
  and a `swarm_round` marker. Studio runs a swarm from `POST /api/swarm`,
  only with `allow: true`, which the page sends after its own Allow once
  card; that one yes is the swarm's single approval. A board post travels
  to the page with its author and a clip of its words. A program that only
  declares a swarm, with no `main`, is now listed.
- Building. `POST /api/check` checks unsaved text in the file's place
  through `source_overrides`, writing nothing. `POST /api/save` writes the
  file (the person's own Save is the yes) and reports whether it checks.
  `POST /api/new` creates a starter agent in a new folder under the root;
  the name is limited to lowercase letters, digits and underscores.
- A step that uses an `execute` tool still cannot have `otherwise`: the
  checker refused it while the two agents below were written, and the
  agents were changed, not the rule.
- Two agents in `my_examples/agents`, written in Kosika:
  `workbench` starts git and python (status, log, explain changes, run
  tests, run a file, check a Kosika file, work out an expression), reads
  what they print in code, keeps a journal, and has the model explain the
  last failure; 6 tests. `planner` keeps tasks in order, plans a day into
  50-minute blocks with breaks in code, has the model break a goal into
  tasks, writes a standup from finished tasks and today's commits, and
  saves a plan to a new file; 8 tests.
- Tried with qwen2.5:3b in a scratch git project: `status`, `test tests`,
  `run report.py`, `py: 2 ** 10` and `changes` each paused on the card and
  ran after Allow once; `why` explained a NameError; the planner's
  `standup` started `git log` and `save plan to plan.md` created the file.
  The example swarm ran three rounds with six posts shown as they were
  made. In the page, 43 partial answers were seen before the final one, and
  the editor reported a good text and a misspelt type name correctly.
- 15 tests in `tests/test_studio_building.py`.
- Not claimed: the small model's explanations are often shallow or wrong
  (it suggested `repr()` for the misspelt name `repot`); the facts above it
  come from code. A swarm in Studio cannot use tools that need approval,
  because a swarm has one approval for the whole run. Tests cannot script a
  shell tool, so the agents' program steps are covered by the live try-out
  and by tests of the code around them, not by their own tests. A program
  that needs more than 60 seconds is stopped, which rules out this
  project's own full test suite. Streaming was tried only against Ollama.
  The editor is a plain text box with no highlighting while typing and no
  undo history beyond the browser's. An approved program runs with your
  own authority: the folder limit is on where it starts, not a sandbox.

## The tool loop, made usable

- The loop was already in the core (`AgentLoop`, reached by `ask AGENT(...)`
  with `can` and `max_steps`), with recorded and replay-checked transcripts.
  It was recommended to the user as something new before the code was
  read; that was wrong, and the user was told. This slice closes what
  stopped it being used.
- Ollama. The `ollama` protocol now offers tools and reads the model's
  choices (`_ollama_message`, tool calls in `_parse_ollama_response`). It
  used to refuse any request with tools, so Studio could not run the loop.
- Words for tools. `agent.describe_tool` tells the model what each granted
  tool does and what to put in it. Before, every tool was described as
  "MCP operation SOURCE/OPERATION".
- `tell_failures` (set for `ask` steps): a failed call to a tool that only
  looks, or one with invalid arguments, is given to the model as
  `{"error": ...}` instead of ending the run. Transcript validation accepts
  that shape. Not for tools that change the world, and a request for an
  undeclared tool still ends the run.
- Reshaping. When the final answer is not the output record, the model is
  asked once more, without tools and in JSON mode, to put its answer into
  the record. The first answer is kept in the trace as `reformatted_from`.
  If that second ask fails or still does not fit, the first answer is
  rejected as before.
- Approval inside the loop. `defer_approval` leaves gating to the run's
  session. When the agent picks an `execute` tool the run pauses with
  `ApprovalPending`, the `ask` step is marked not started, and after the
  approval the step begins again. One approved change per `ask` step; a
  second raises, because beginning again must not repeat the first.
- `peek`: a read-only files operation that returns the first 4000
  characters of a file.
- Seen live. A watcher is told `agent_tool` for each choice (told, not
  recorded). Studio shows the chain under the step, the story lists each
  choice with what it gave back, and Requests shows the whole exchange.
- `my_examples/agents/scout`: answers a question about a folder by
  choosing `list_folder` and `look_at` itself.
- Tried on a scratch project with three questions. qwen2.5:7b answered two
  correctly, finding an undefined name by reading two files, and gave a
  partial answer to the third while naming a file it had not opened; about
  100 seconds a question on this CPU. qwen2.5:3b answered one correctly,
  one wrongly without opening a file, and gave up on one; 30 to 50
  seconds. With an approval-gated git tool the small model chose
  `git log -1`, paused, and after Allow once reported the commit message.
- 14 tests in `tests/test_tool_loop.py`. Three older tests changed: the
  Ollama protocol test now expects tools to be sent, and the city test now
  expects `ApprovalPending` where the loop's own approval error used to
  come first.
- Not claimed: the sources an agent names are not checked against the
  files it opened in a workflow `ask`. When a step begins again after an
  approval the model is asked again and may choose different arguments,
  which asks the person again; that happened once in the try-out. The loop
  does not stream its answer. Three questions on one small project is a
  try-out, not a measurement. No MCP server was connected in this slice.

## MCP servers in Studio

- `kosika/mcp_tools.py`. `kosika studio --mcp NAME=COMMAND` (repeatable)
  grants a server by the name programs use in `source mcp "NAME"`. `MCPHub`
  starts a server on first use, keeps one process per server, and closes
  them when Studio stops. `bind_mcp_tools` binds a program's tools whose
  source is a granted name through the ordinary host tool bindings, so
  approval, budgets, traces and the tool loop treat them like any tool.
- This sits beside the older pinned-manifest path (`--mcp-server` with
  `--mcp-manifest` on `run`, `agent` and `swarm`), which is unchanged. That
  path requires schemas to match a pinned manifest exactly and starts the
  server anew for each call. The new path is looser on purpose: the result
  is checked against the declared output record only, and a plain-text
  answer is put into a record with one Text field, cut at 20,000
  characters.
- Read-only by the server's word. A tool may be declared `observe` only
  when the server marks it `readOnlyHint: true`; otherwise the call is
  refused with the instruction to declare `execute`. A server that marks
  nothing therefore needs approval for every call.
- The loop is told each server tool in the server's own description
  (`MCPToolCall.describe`, passed through `call_agent`).
- Studio: `GET /api/mcp`, a Tools page listing each server's tools, whether
  each only reads, and a Kosika declaration to copy
  (`mcp_tools.declaration`; fields Kosika has no type for are left out and
  named in a comment). The approval card names the server. Permissions
  lists the granted servers.
- `examples/mcp/notes_server.py`, a small MCP server in standard-library
  Python, and `examples/mcp/librarian.kos`; copies in
  `my_examples/agents/librarian`.
- Tried with the notes server as a real separate process and qwen2.5:3b:
  "When is my dentist appointment?" chose `search_notes` and answered from
  the note; "Remember that the car service is on the 14th" chose
  `add_note`, paused on the card, and after Allow once the note was in the
  file; "What do I need to buy?" was answered wrongly without any tool.
- 13 tests in `tests/test_mcp_tools.py`, most against the real server
  process.
- Not claimed: only servers that speak MCP over standard input and output
  as newline-delimited JSON were tried, and only this one. No third-party
  server was downloaded or run. Remote servers over HTTP, sign-in,
  resources and prompts are not supported. A server's read-only mark is
  taken on trust. Nested input fields cannot be declared. A granted server
  is not sandboxed. `kosika run` and `kosika chat` do not take `--mcp` yet.

## Agents for the laptop, and file search

- `search`, a read-only files operation: `{path, query}` to `{matches}`.
  It walks the granted folder, skipping hidden folders, tool stores such
  as `node_modules`, symlinks, files over 1 MiB and files that are not
  UTF-8, and returns up to 40 lines as `path:line: text`, lines holding
  more of the words first, then by file and line so a search is
  repeatable. Limits: 5000 files, 8 seconds, 240 characters a line. It is
  word matching, not meaning: a question phrased in other words than the
  file finds nothing.
- Three new agents, in `examples/agents` with the three earlier ones (and
  in `my_examples/agents`):
  `researcher` (7 tests) searches, has the model answer from the found
  lines with one exact quote, and checks in code that the quote is in a
  found line before showing the answer with its file and line.
  `doctor` (7 tests) runs read-only Python measurements after approval and
  parses the numbers in code; the model only gives advice from them.
  `coach` (6 tests) has the model write a question with one right and
  three wrong answers; code shuffles, marks, scores and re-asks misses.
- The pattern in all three: code does everything that must be exact, and
  the model's part is small and checked. This is why they work with the
  3b model where the tool-choosing `scout` did not.
- Tried on this laptop with qwen2.5:3b. Researcher: three questions
  answered correctly with a verified quote and line in 8 to 20 seconds,
  an unknown subject refused in 0.3 seconds with no model call, and one
  question refused because no quote backed it. Doctor: one approval gave
  disk 63.4 GB free of 475.5 GB, memory 89% in use with the six largest
  users named, battery 79%. Coach: questions, hint, marking, review and
  score worked; the model wrote the same question twice in a row, so the
  prompt now asks for a different kind of question each turn and code
  refuses an exact repeat.
- `tests/test_example_agents.py`: every example agent must check and pass
  its own tests, and six tests of `search`, including that it never looks
  outside the granted folder.
- Not claimed: the quote check proves the words are in the file, not that
  the answer follows from them. The coach's questions and answer keys come
  from the model and can be wrong. The doctor's advice was generic. The
  doctor's programs are Windows-only and were run on one machine. The
  varied-question change to the coach was tested by its tests, not tried
  again on the model. Search has no index, so a very large folder is cut
  off at its limits.

## `verify`, `eval`, and `kosika measure`

- `verify CHECK(arguments)` on a model step (`Step.verify`). CHECK is a
  function of the program that returns Text: the complaint, or `""`. It
  sees the answer under the step's own name. A complaint raises the same
  `RejectedAnswer` a malformed answer does, so `retry` shows the model its
  answer and the complaint, and `otherwise` takes over when retries run
  out. The check of the accepted answer is recorded on `model_return` as
  `verified` (its function call and return), and replay requires it to be
  well formed and to have returned `""`.
- The checker handles `verify` the way it handles `otherwise` and `match`:
  it checks the program in which the check is an ordinary step placed
  after the verified one. Absent, the field leaves a program's hash as it
  was.
- `eval "name" { ... }` parses as a test with `live` set. `run_tests`,
  packs and Studio's test list skip evals. `testing.measure` runs each eval
  on each named model with the real provider, scripted answers allowed
  only for tools, and counts passes, seconds and model calls, including
  the calls of runs that failed. `kosika measure FILE --model A --model B
  [--repeat N] [--only NAME] [--json]`.
- The checker now reports a faulty declaration before the fallback type
  disagreement it causes (found by the corpus when the new examples
  joined it).
- The researcher uses both: `read_and_prove` verifies the quote with one
  retry, and four evals fix what the search "found" so models are compared
  on the same lines.
- Measured on this laptop, three runs of each of the four evals. First
  run, before a fix: qwen2.5:7b 8 of 8, qwen2.5:3b 4 of 8. The measure
  showed the 3b failing "it copies a number exactly" every time; looking
  at its answers, it quoted "Rent is 900 euros a month." where the line
  continues with a comma. The check was changed to ignore punctuation at
  the two ends of a quote and nothing else. After: qwen2.5:7b 12 of 12 in
  146 seconds, qwen2.5:3b 8 of 12 in 66 seconds; the number eval went from
  0 of 2 to 3 of 3. The 3b still failed "it picks the right line among
  several" 3 of 3 and the date eval 1 of 3, with one model call each, so
  it gave up rather than misquoted.
- 14 tests in `tests/test_verify_and_measure.py`; the researcher has 8
  tests.
- Not claimed: four evals with three runs each is a small sample, enough
  to see a large difference and not a small one. `verify` is for model
  steps only, not `ask` agents or `match` steps. Replay checks that the
  recorded check passed and is well formed; it does not run the function
  again. A check is only as good as the function written: the quote check
  proves the words are in the file, not that the answer follows. No
  automatic tuning of prompts was built. Meaning-based search was not
  started: it needs an embedding model that is not installed.

## `escalate`: fast model first, strong when refused

- `escalate NAME` on a model step (`Step.escalate`), after any `retry` and
  before `otherwise`. When every answer of the step's own model is
  rejected, the same question with the same record, `verify` and `retry`
  is asked of the provider bound to NAME. Provider failures do not
  escalate. The return event records `escalated: {to, because}`; the story
  and Studio say so. Preflight requires NAME to be bound.
- `--model fast,NAME=big` in the short form of `--model` (run, chat,
  studio, measure): every declared model is answered by the first model,
  except the names given their own.
- The researcher asks `reader`, escalates to `strong`, and its check now
  refuses an empty answer too, so that giving up is a reason to escalate.
- Measured, four evals three times each: qwen2.5:3b 9 of 12 in 92 s;
  qwen2.5:7b 12 of 12 in 141 s; 3b escalating to 7b 12 of 12 in 286 s.
  Escalation reached the large model's accuracy and was twice as slow as
  the large model alone. Two of the four evals escalate every time (one
  the small model cannot do, one that has no answer, where both models
  are asked), and this laptop cannot keep both models in memory, so each
  switch reloads one. It pays only when most questions stop at the fast
  model and both models stay loaded; here neither held.
- 11 tests in `tests/test_escalate.py`.
- Not claimed: no speed benefit was shown. A legitimate "the lines do not
  say" now costs a call to each model. One fixed pair of models was tried.

## The `kosika` tool and the author agent

- `source system "kosika"` with `operation: "check"`: `{source}` to
  `{ok, problem, tests, passed}`. The text is written to a temporary
  folder, loaded, and its tests run with `run_tests`. It is bound without
  any grant, in runs, Studio, tests and evals, because it reaches nothing:
  no import resolves, and tests use scripted answers and their own
  memories. Limits: 64 KiB of source, 30 tests.
- `examples/agents/author`: the model is given a guide to Kosika of about
  2300 characters and writes a program with tests as `{source}`. A
  `verify` function refuses what is plainly not Kosika (no `workflow
  main`, no tests, Python or Markdown) and escalates to `strong`. The real
  check then runs, and a failing program goes back with the checker's own
  message, up to two more times. `test: ...` runs a test of the person's
  own against the last program; `fix: ...` repairs with a hint; `save
  NAME` creates `NAME.kos` after approval. 7 tests, in which the checking
  is real, and 3 evals that each run a hidden test against what the model
  wrote.
- Measured once each: qwen2.5:7b passed 3 of 3 hidden tests, each program
  right on the first attempt, 143 s in all; qwen2.5:3b passed 2 of 3 in
  114 s with 5 model calls. In Studio the 7b model was asked for total,
  count and average of a list of prices: the first attempt failed the
  check, the second passed, a test of mine it had not seen passed, and the
  saved file passed `kosika test`; 171 s.
- Found while writing the tests: importing a missing file raised an
  `OSError` out of the check. It is now reported as a problem with the
  program.
- 6 tests in `tests/test_kosika_tool.py`; the examples test now covers
  seven agents.
- Not claimed: three small tasks, run once, on programs of one function.
  Nothing here shows it can write an agent with tools, memory or model
  steps; the guide does not teach those. A program that passes its own
  tests can still be wrong, since the model wrote both; only a test
  written by someone else says more. The agent did not write any part of
  Kosika itself. The check runs the candidate's tests in the same process,
  bounded by the language's loop and depth limits and nothing else.

## The author learns model steps and memory

- The author's guide is now chosen per request: the basics always, plus a
  lesson on model steps (`classify`, `summarize`, `decide`, with how tests
  script their answers) when the request needs a model to read text, and a
  lesson on memory (`recall`, `remember`) when it must keep something
  between runs. Each lesson ends with a whole example program, and a test
  proves each example is valid Kosika that passes its own tests.
- Measured once each, six tasks with hidden tests: qwen2.5:7b 4 of 6 in
  676 s, qwen2.5:3b 4 of 6 in 350 s. Both wrote a working program that
  remembers. The 7b wrote a working classify program; the 3b did not.
  Neither wrote the summarize-and-count program. The 7b failed "counts
  long words", which it had passed in the earlier run of three.
- Not claimed: one run per task is noisy, as the long-words result shows;
  these numbers say what is possible, not how often. The summarize lesson
  has no example of its own and that task failed on both models. Tools and
  agents are still not taught. Other work was using the same CPU during
  this run, so the times are longer than they would be alone.

## Comforts: text templates, the clock, and chance

- The user asked for classes, exceptions, a clock, random numbers, direct
  file and network access, text formatting, and parallel steps, to make
  Kosika a replacement for Python. Plan agreed in the thread, in this
  order: templates; clock and random; `fail` with `try`/`otherwise`;
  functions that belong to a record, without inheritance; parallel steps;
  one-line `read`, `write` and `fetch` that still need the host's grant.
- `f"..."` templates (`Parser.parse_template`): lowered while parsing to
  the same `+` joining a person would write, so nothing downstream changed
  and the hash is the same either way. `f` must touch the quotation mark;
  a variable called `f` is still a variable.
- `now` and `random LOW to HIGH` (`kosika/ambient.py`): workflow steps
  lowered to read-only tool calls through an agent named `world`, with
  built-in records `Moment`, `RandomRange` and `RandomNumber`. Bound in
  every run without a grant; recorded in the trace, so replay sees the
  same time and number. A test scripts them like any read-only tool.
- 12 tests in `tests/test_comforts.py`.
- Not claimed: the lexer ends a text at the first quotation mark, so an
  expression inside braces cannot contain one. Only Text and Number can be
  written in. There is no formatting of numbers (decimal places, padding).
  `now` is local time with no time zones and no date arithmetic. `random`
  gives whole numbers only. A program that already declares an agent named
  `world` does not get these steps.

## Comforts, second part: `fail`/`try`, record methods, one-line files

- `fail TEXT` (`FailExpr`) raises `runtime.Failure`. `try { } otherwise
  [NAME] { }` completes the `TryExpr` that was parsed and type-checked
  before but never evaluated: the runtime now runs it, drops whatever the
  body recorded before failing, retries if asked, and evaluates the
  fallback with the reason bound. `ExecutionError` (limits, cancellation)
  is never caught. `fail` is a keyword only directly before a text, so a
  variable named `fail` still works. 9 tests.
- Record methods: `fn name(self, ...)` inside a record becomes the function
  `Record$name` with `self` first; `RecordDecl.methods` lists them. The
  checker turns `value.name(...)` on a record that has the method into
  that call. At run time there are no types, so the record is told from
  its field names; the checker therefore refuses the cases where that
  could be wrong (a method named like a map method, or two records with
  identical fields sharing a method name). 8 tests.
- `read file`, `list files`, `write file ... with ...`, `fetch`
  (`kosika/direct.py`): lowered to ordinary system tools through an agent
  named `direct`, so grants, the folder boundary, approval for writes,
  traces and replay are those of the tools. 7 tests.
- The user asked for direct access. What was built is direct to write and
  unchanged in what it can reach, and the reply said so: a program written
  by a model must not be able to change files because it says so.
- Not claimed: parallel steps, the last of the seven asked for, are not
  built. They need the workflow loop in `runtime._run_workflow` split so
  one step can run apart from the others, with the session and the budget
  made safe for that; it was left for its own slice rather than rushed.
  Methods cannot be static, there are no constructors beyond the record
  literal, and no inheritance or interfaces. Recursion through methods is
  bounded by the call-depth limit, not by a declared `recurse`. A `var`
  assigned inside a `try` body keeps the assignment even if the body then
  fails. There is no `append file`, `delete file` or `move file`, and
  `fetch` only reads. The author agent's guide does not teach any of this
  yet.

## Parallel steps

- `parallel { a = ...  b = ... }` in a workflow sets `Step.group` on its
  steps. When the workflow loop reaches the first step of a block it starts
  the slow call of every eligible member on its own thread
  (`runtime._Ahead`): a model step's whole asking (with `verify`, `retry`,
  `vote`, `escalate`), or a host tool whose access is `observe`. The loop
  then runs each step in written order as before and takes the answer that
  is ready or on its way instead of making the call. So events, budgets,
  the session and `otherwise` are untouched, and the trace is identical to
  a sequential run. This avoided splitting the workflow loop, which the
  earlier note said was needed.
- The checker refuses members that use each other's results, `match`
  steps, workflow calls, `ask`, memory and board steps, and `execute`
  tools.
- A scripted model or tool (`ordered = True`) is never asked early, so
  tests and replays of scripted runs stay deterministic. A step a resumed
  session already completed is not asked again.
- The model step's asking code moved into one local function,
  `model_answer`, used by both paths.
- 7 tests in `tests/test_parallel.py`: three 0.4-second reads finish in
  under a second with all three open at once; two different models
  overlap; the trace lists the steps in written order and replays; a
  failure in the block reaches that step's `otherwise`.
- Not claimed: no speed-up was measured on a real model, and two steps
  asking the same local model will not overlap, since Ollama on this
  machine answers one at a time. Function steps in a block run in order.
  A call begun early is not stopped if an earlier step of the block then
  fails; it finishes on its thread and its result is dropped, though a
  model call it made was still counted against the budget. Studio shows
  the steps of a block lighting up one after another, not together.

## Rewriting the planner with the new features, and record update

- The planner example was rewritten with record functions and `f"..."`
  templates, keeping all 8 of its tests unchanged and passing. It did not
  get shorter: 24,087 characters became 24,199. Reading it showed why:
  three small functions each copied all seven fields of a record to change
  one or two.
- Record update, `{..base, field: value}` (`RecordExpr.base`): a copy of a
  record with the named fields replaced. Its type is the base's, so it
  needs no annotation. With it the planner is 23,827 characters, about one
  percent smaller than where it started, and 637 lines against 617 because
  the record is now written over several lines.
- So the honest result is that these features make a program clearer and
  safer to change, not noticeably shorter. Most of the planner is its own
  logic and its wording to the user, which no syntax removes.
- The author agent's guide now teaches `f"..."` in place of telling the
  model there is no text formatting.
- 2 more tests in `tests/test_record_methods.py`.
- Not claimed: only one agent was rewritten. No comparison with an
  equivalent Python program was made. With the changed guide the author
  scored 4 of 6 on qwen2.5:7b again, with a different pair of tasks failing
  than last time (classify failed, long words passed), so one run per task
  cannot show whether the change helped.

### The researcher in Python, and the short methods it showed were missing

- `benchmarks/python_comparison/` holds the researcher agent written again
  in plain Python (standard library, local Ollama), its tests, a counter
  and a live measure. Its README is the result.
- First result: the two programs were the same size, 8,787 characters in
  Kosika against 8,723 in Python. Kosika needed none of Python's 110 lines
  of folder safety, model calling and measuring, and spent about 100 more
  lines on the agent's own logic. On qwen2.5:7b both were right 12 of 12.
- Six causes are listed in that README. The first, second and part of the
  fifth are fixed by six built-in methods: `take`, `drop`, `words`,
  `replace`, `trim_marks`, `is_empty` (Text and arrays; `words`, `replace`
  and `trim_marks` on Text only). The researcher is now 219 lines and 8,142
  characters.
- `tests/test_short_methods.py`.
- Not claimed: `is_empty` was not added to maps, because a map's method
  names are reserved from record methods.

### A value where a step expects a call, and a correction

- After `otherwise`, and as a branch of a step's `match`, a record value
  may be written directly. `kosika.sugar.lower_values` replaces it with a
  call of a generated function `WORKFLOW$STEP$otherwise` or `...$caseN`
  that takes nothing and returns it, typed from the call it stands beside.
  The story and Studio say "used the value written in the program".
- A correction to the comparison. Three of the six causes it listed were
  not gaps in the language: a step can already pass plain Text, a step
  call can already take literal and computed arguments, and
  `for x in xs where cond { x }` already filters. The researcher had
  simply been written the long way, with a record around every text.
- Rewritten with all of this, the researcher is 184 working lines and
  7,266 characters, from 243 and 8,787, against Python's 253 and 8,723.
  Its eight tests are unchanged and pass.
- `tests/test_value_steps.py`.
- Not claimed: the value cannot use a parameter or an earlier step. An
  error inside it names the generated function. One agent and one model
  is a small comparison.

### The other example agents in the short style

- The six other agents were given the short forms: `take`, `drop`,
  `is_empty`, `replace`, a value after `otherwise` or in a `case` where a
  function only returned one fixed record, and `kind_of` returning Text
  with no `Kind` record around it.
- Working lines, before to after: author 256 to 249, coach 278 to 265,
  doctor 257 to 250, planner 451 to 444, scout 55 to 52, workbench 350 to
  340. That is two to five percent. The researcher's quarter came from
  wrappers these agents never had: most of each is its own logic and its
  wording to the user.
- The author agent's guide now teaches `words`, `replace`, `take`, `drop`,
  `is_empty` and `for x in xs where cond { x }`.
- Every agent's own tests pass unchanged, apart from `kind_of(...)` in two
  tests no longer needing `.label`.
- Not claimed: the workflows of these agents still pass a `Prompt` record
  to each model step and wrap replies in small functions; that was left
  as it is. With the longer guide the author scored 3 of 6 on qwen2.5:7b in
  one run (977 s), against 4 of 6 in each of two earlier runs: the three
  plain-code tasks passed and the model, summary and memory tasks failed.
  One run cannot show whether the longer guide cost a task or whether it
  is the usual noise; it has not been run again.

### Search by meaning

- A files tool may have `operation: "find"`: the lines of a folder
  nearest in meaning to a question, in the same `path:line: text` form
  as `search`, so a program can swap one for the other and the
  researcher's quote check works unchanged.
- It needs an embedding model. `--embed-model NAME` on `run`, `chat` and
  `studio` grants one, asked at the Ollama address. Without it the tool
  is not bound. Studio's Permissions page says whether it is granted.
- `kosika/embeddings.py`: `ollama_embedder`, and `Meaning`, which ranks
  by cosine nearness and remembers every vector in a SQLite file in the
  user's home folder, keyed by model and text. The searched folder is
  never written to.
- The researcher asks for key-word lines and, when the question has key
  words at all, for lines near in meaning; it reads the best eight of
  the first, then up to six of the second, then the rest.
- `tests/test_find_by_meaning.py` (a stand-in embedder, no network), one
  more test and one more eval in the researcher.
- Tried with nomic-embed-text on a made-up folder of three notes files:
  four questions sharing no useful word with their answer (flat and rent,
  tooth doctor and dentist, away and on leave, rubbish and bins) each got
  the right line first; key words found one of the four. The first call
  took 1.8 s, later ones under 0.2 s. The researcher's evals on qwen2.5:7b:
  15 of 15, the new one 3 of 3.
- The same day, five setups on the researcher's four older evals, three
  runs each: gemma3:1b 5 of 12 in 74 s, qwen2.5:1.5b 8 in 52 s, qwen2.5:3b
  9 in 84 s, qwen2.5:7b 12 in 137 s, and 1.5b with 7b as `strong` 9 in
  60 s. The 1.5b model never refused the question the lines do not answer:
  it gave a real quote that proves nothing, which the quote check passes,
  so `escalate` is never reached. The check proves a quote is real, not
  that it answers the question.
- The author with the longer guide, three more runs on qwen2.5:7b: 9 of
  18, so 12 of 24 over all four runs against 8 of 12 for the two runs with
  the shorter guide. That points to the longer guide costing something,
  without settling it.
- Not claimed: the trial folder was sixteen lines; nothing was measured on
  a large folder, where only 400 new lines are embedded per call and a
  first search is therefore incomplete. Ranking is by one embedding model
  with no threshold, so `find` always returns its twelve nearest lines,
  near or not. The vectors store grows and is never trimmed. No hosted
  embedding service is supported.

### A second check: is the proving line about the question?

- A tool with `source system "meaning"` and `operation: "near"` takes two
  texts and gives how near they are in meaning. It is bound when an
  embedding model is granted and needs no folder. `kosika measure` takes
  `--embed-model`, so an eval can use the real comparison while its file
  tools stay scripted.
- The researcher compares the question with the line that proves the
  answer, and does not show an answer whose line scores under 0.61. With no
  embedding model the step falls to its `otherwise` and the answer stands
  on its quote alone, as before.
- Measured on the researcher's five evals, three runs each. Without the
  check: qwen2.5:1.5b 12 of 15, qwen2.5:3b 12, 1.5b with 7b as `strong` 13.
  With it: 1.5b 15 of 15, 3b 12, 1.5b with 7b 15, 7b 15. The gain is the
  question the lines do not answer, which 1.5b had got wrong 3 times of 3
  and now refuses 3 of 3; no right answer was withheld. 3b's three failures
  are a different fault (it quotes the wrong line among several), which
  this check does not address.
- The cost here is large: 1.5b took 72 s without the check and 172 s with
  it, and 7b 476 s against about 160. The comparison itself is quick; the
  time is most likely Ollama swapping the chat model and the embedding
  model in and out of memory on a 16 GB laptop. That was not confirmed.
- Not claimed: the threshold 0.61 was chosen from fourteen pairs, with a
  gap of 0.03 between the lowest answering pair and the highest
  non-answering one, and the wifi question in the eval is one of those
  fourteen, so the eval is not an independent test of the threshold. It
  holds for nomic-embed-text only. A line can be about the question and
  still not answer it.
- The author's guide went back to the shorter text. Three runs each on
  qwen2.5:7b: 13 of 18 with the shorter guide, 9 of 18 with the longer one
  that taught the new short methods; over all runs 21 of 30 against 12 of
  24. The longer guide cost most on the plain-code tasks. The agents'
  own code keeps the short methods; only the guide given to the model
  changed back.

### A map of an agent in Studio

- `kosika/project_map.py` builds a beginner's map of one program from
  the checked program and a person's own words: purpose, what goes in
  and out, what it only looks at, what it may change, what it reaches
  that Kosika cannot see inside, its models, its memories, and the main
  workflow's steps (at most ten, each opening one level into a workflow
  it runs). Each item carries its source (`checked`, `comment`, `person`)
  and its line. A purpose is never guessed.
- Studio has a Map tab, `/api/map`, and `/api/purpose`, which saves one
  sentence in `purpose.txt` beside the program.
- `tests/test_project_map.py`. Looked at in the real page for the
  researcher and the workbench.
- Not claimed: no one but the author has read a map, so whether it
  explains an agent to a newcomer is untested. It is the beginner view
  only; the developer and system-designer views of the blueprint are not
  built. It covers one Kosika program, not a project of several, and no
  code in other languages. Lines come from matching the source text, not
  from the parser. The older `mindmap` and `audience-map` commands are
  unchanged and are not what the tab shows.

### Sessions: an agent's own copy of the folder, behind a wall

- `kosika/sessions.py`: a `Session` copies the granted folder (up to
  20,000 files and 200 MB, without `node_modules`, `__pycache__` and
  virtual environments), runs one named program at a time behind
  bubblewrap (no network, read-only system, only the copy writable,
  environment cleared, stopped at the time limit), lists what differs
  from the real folder, and applies those differences only when asked.
  On Windows the command is run through `wsl.exe`.
- `bind_system_tools(..., session=)`: file tools work in the copy and
  shell tools run behind the wall.
- Studio: `--in-session` (needs `--allow-folder`, and refuses to start
  if the wall does not work). One session per agent, made on its first
  run. File and shell approvals inside it are given by the session and
  recorded as such; a Session tab lists the changes, with one button to
  bring them back and one to throw them away.
- `tests/test_sessions.py`: twelve tests, eleven with a stand-in for the
  wall and one against the real wall, skipped where there is none. On
  this laptop the real one runs: the program saw only the copy, had no
  network, and could not write to the system.
- Tried through Studio's server with the real wall: a run with one
  program start and one file write finished in 4.7 s without asking,
  the real folder was unchanged, both files were listed, and bringing
  them back wrote them.
- Not claimed: only Studio has the switch, not `kosika run` or `chat`.
  A session does not survive Studio stopping (its copy stays on disk
  and is not found again). Each change opens to show its lines (text files up to 200 KB),
  but changes are brought back all together from the page. If the real folder changes while a session is
  open, bringing back overwrites it. The Linux side at first had no
  `python` or `pytest`; both were installed later the same day, with the
  owner's yes, and `python`, `python -m pytest` and `git` were then run
  inside a session. The workbench agent was then run in one through
  Studio with qwen2.5:7b: `status`, `test`, `run report.py` and a
  calculation each finished in 2 to 3 seconds, none waited for a yes, and
  the session held no changes afterwards. No limit on memory or processor. No model was
  involved in the trial, and no one but the author has used it. MCP and
  web tools are outside the wall.

### A Project page in Studio

- `kosika/project_index.py` lists every agent, workflow, model, tool,
  memory, board, swarm and record under Studio's folder, each with its
  program, line and a short description. Studio serves it at
  `/api/project` and shows it on a Project page with a search box and a
  filter by kind.
- Nine suggestion rules, applied to the checked program: `does-not-check`,
  `no-tests`, `no-evals`, `model-step-no-fallback`, `no-budget`,
  `unused-tool`, `unused-model`, `unused-workflow`, `no-purpose`. Each
  suggestion shows its rule. On the seven example agents the only
  suggestions are five missing evals.
- `tests/test_project_index.py`. Looked at in the real page.
- Not claimed: the rules are a first set chosen by the author and have
  not been checked against what a newcomer finds useful. A suggestion
  cannot be dismissed. Functions are not listed. Search is by words in
  names and descriptions, not by meaning. The page lists one folder of
  programs and knows nothing of how they relate to each other.

### Help while writing in Studio

- `/api/assist` checks the text being edited without saving it and
  returns the first problem (message, code, line, column, length) and
  the words that can be completed: about fifty words of the language
  with a line on how each is used, every built-in method, and the names
  and fields found in the text.
- The checker places a problem at the function or step it is in.
  `_sharpen` moves that to the word the message quotes, when the word
  is found inside the same declaration.
- The Code tab shows a bar of up to eight completions for the word being
  typed (Tab takes the first; after a dot, fields and methods only) and
  the first problem under the text, 0.7 s after typing stops; clicking it
  selects the word at fault.
- `tests/test_writing_help.py`. Tried in the real page: a misspelt method
  was reported on its line and selected on click, and `hits.mat` then Tab
  gave `hits.matches`.
- Not claimed: this is a bar above a plain text box, not a list at the
  cursor, and errors are not underlined in the text. Only the first
  problem is shown. Completions do not know types: after a dot every
  field and method in the file is offered, whatever the value is. A
  name the program does not declare (a misspelt tool) is pointed at
  the declaration, not at the misspelling. It does not write code for
  you; that would need a model. The existing language server
  (`kosika/lsp.py`) and the VS Code folder were not touched or tried.

### An experiment: animation described in Kosika

- `kosika/motion.py`: `check_scene` says in words everything wrong with
  a scene record; `to_svg` and `to_html` turn a scene that checks into
  one SVG picture animated with SVG's own elements, with no script and
  nothing fetched. Each shape sits in nested groups so that moving,
  turning, growing and fading do not undo one another, and a later move
  of the same kind takes over from the earlier one.
- `kosika motion SOURCE --out FILE [--input JSON]` runs the program's
  workflow, checks the scene it returns, and writes the file.
- `Number` gains `sin()`, `cos()` (degrees) and `sqrt()`, rounded to nine
  places so that a run replays exactly on any computer.
- `examples/motion/logo.kos`: 16 shapes and 83 moves from about fifty
  lines, with three tests. Played in a browser and read back at several
  moments: the dots reached their places on the circle, the tile
  dropped and turned a quarter, the letter and the name faded in.
- `tests/test_motion.py`.
- Not claimed: no picture of it was captured; it was verified by reading
  positions, sizes and fades from the browser, not by looking. Four
  kinds of shape and five of move; no paths, images, colour changes,
  repeats, or reaction to the mouse. No agent writes scenes yet, and no
  model was involved. It is not shown in Studio. Whether this is worth
  growing is exactly what is not yet known.

### Animation: richer motion and a Scene tab

- Moves gain `color` (to the colour in the move's `color` field), the
  eases `bounce` and `back`, and `repeat` (a count, or 0 for without
  end). A move is now written as a list of values with their times, so
  a bounce is four values and there-and-back is three.
- A fault found and fixed: going across and going up and down shared one
  group, so when a shape did both at once the second replaced the first
  and the shape jumped sideways instead of gliding. Each now has its own
  group. The first version of the logo had this fault in flight; its
  resting positions, which were what had been read back, were right.
- Studio shows a Scene tab for a program whose `main` returns shapes and
  moves: play and pause, a slider through time, the inputs to change,
  and saving the web file. `/api/scene` runs only plain code.
- The logo example uses a bounce, a flash of colour round the dots, and a
  ring that turns without end.
- Tried in the real page: the tab appeared first, the slider held the
  scene at 1.6 s with the tile below its resting place (mid-bounce) and
  at 5 s with it settled.
- Not claimed: still no picture was captured and nobody has watched it
  but the owner, who saw the first version. No paths, lines, images or
  groups of shapes. A scene program that needs a model or a tool cannot
  be played in the Scene tab. No agent writes scenes yet.

### An agent that designs motion

- `examples/agents/animator`: the model fills in a `Plan` of seven fields
  (pattern, count, two colours, background, title, seconds); a `verify`
  written in Kosika sends a plan that breaks a rule back once; code then
  builds the scene. Four patterns: ring, grid, wave, burst. A grid of 35
  tiles is 213 moves, none of them written by the model.
- `draw:` asks the model for the whole scene instead, checked by a
  second `verify` written in Kosika, with two more tries.
- The Scene tab takes a description for a program that asks a model: an
  ordinary run, whose result is checked and drawn by `/api/scene`.
- Six tests in the agent with scripted answers. A Python test builds
  every pattern at several sizes through the real checker. Each pattern
  was read back from a browser at several moments: tiles hidden, then
  overshooting to 1.07 of their size and settling; the wave reaching its
  top with its flash of colour and returning, each dot later than the
  last; sparks flying out, fading, and starting again.
- On qwen2.5:7b, five requests asked three times over. In the first
  round the model chose a fitting pattern, count and title each time,
  in 16 to 29 s, but one scene would not play (the model asked for 3
  seconds and the ring needs a little more) and colour names were
  ignored in favour of the one colour the prompt showed. After two
  fixes (a scene is made long enough for its own moves; the prompt
  lists nine named colours) all ten scenes of the next two rounds
  played and used the colours asked for. `draw:` took 96 to 113 s and
  played each time; once it left out the word the request asked for.
- Not claimed: fifteen runs on one model is a trial, not a measurement,
  and there is no eval in the agent yet. Whether a design looks good
  was not judged by anyone. The patterns are four fixed ones; "complex"
  here means many well-timed moves, not new kinds of motion. `draw:`
  is slow and gives small scenes. Through Studio's Scene tab with
  qwen2.5:7b it was tried once: "a gold burst of sparks on black, titled
  Hello Aryan" was drawn 40 s after pressing Design it, gold on near
  black, with that title and the play controls.

### A benchmark of forty questions, and what it changed

- `benchmarks/researcher/`: eight files of made-up notes, forty
  questions of four kinds (key words shared, meaning only, several
  candidate lines, not answered by the notes), a runner that asks the
  real agent with nothing scripted and sorts each answer into right,
  false answer shown, declined or failed, and every run's answers kept
  in `results/`. Its README has the tables.
- First run: qwen2.5:7b 32 right and none false; with search by meaning
  36 and none false. qwen2.5:1.5b 24 right and 12 false; with search by
  meaning 27 and 9 false.
- Four faults were read out of the wrong answers and fixed. The word
  search matched fragments; it now matches where a word begins, and
  words of three letters or fewer whole. In the researcher, a heading
  is no longer proof; an answer must have words of its own in the
  quoted line; and the fixed score for "is the line about the
  question" is replaced by requiring the proving line among the five
  nearest in meaning. The `near` tool stays in the language; the
  researcher no longer uses it.
- Second run: 7b 31 and none false; with meaning 39 and none false.
  1.5b 26 and 6 false; with meaning 32 and 8 false.
- The earlier report of 15 of 15 for 1.5b with the second check is
  withdrawn in the benchmark's README: it rested on five questions.
- Not claimed: the fixes were made after reading these same questions'
  wrong answers, so the second run is not a test on unseen questions.
  One run per setup. The author wrote both the agent and the
  questions. The Python version of the researcher in
  `benchmarks/python_comparison` was not given these fixes, so the two
  no longer do the same thing and its size comparison describes the
  agent as it was.

### Motion in Studio itself

- `kosika/app_assets/brand.kos` describes Studio's mark as a scene, with
  one test. Studio runs it once (`/api/brand`), draws it without a
  background (`to_svg(..., backdrop=False)`), and shows it in the
  sidebar and beside the Agents and Project titles. If the program
  fails, the plain K stays.
- Each page's parts, the agent cards, and the panels of the Map, Scene
  and Session tabs ease in one after another; the chosen sidebar item
  gets a bar that grows in; panels answer the pointer.
- With the system's reduce-motion setting on, the page's own rule turns
  the eases off, and the mark is set to its finished moment and paused.
- Seen in a screenshot of the real page: the mark with its ring in both
  places. Read back from the page: the tile at rest in the centre at
  1.8 s, the letter visible, a dot swollen mid-wave at 3.5 s.
- Not claimed: whether this is good design is the owner's call and has
  not been made. The reduce-motion path was written, not tried on a
  computer set that way. Only a still picture was captured, so the
  motion itself was checked by reading values. The mark's colours are
  fixed and were looked at on the dark theme only.

### A run drawn as a moving diagram, and a richer look

- The Live view draws a diagram above the steps: you, the program, and
  up to seven parts on the right (each declared model, files, web,
  programs, outside tools, meaning, memory, board, plain code), made
  from what the agent declares. The run's own events drive it: a light
  travels to a part when it is called and back when it answers, the
  part lights up and counts, a pause for approval sends the light to
  you, and the end colours the program. Events are queued so lights
  follow one another.
- The look: a soft warm glow behind the page, deeper dark colours,
  shadows and rounder corners on cards and panels, a display face for
  headings, a graded button, a focus ring, and an agent's long
  description shown as three lines that open on a click.
- Tried with qwen2.5:7b on the researcher and the coach: the counts on
  the diagram matched the run's own tally (one model call and two file
  calls; two memory calls and three code steps), a light was caught in
  flight in a screenshot, and the program ended green.
- A fault found on the way: the diagram advanced only when the browser
  drew a frame, so in a window that was not being drawn it froze on
  the first light and never counted. It now advances on a timer and
  uses drawing only to move the light.
- Not claimed: the diagram is page code with no test of its behaviour,
  only a test that it is present; what it shows was checked by hand on
  two runs. Whether the look is better is the owner's judgment. Seen on
  the dark theme only. The diagram shows the kinds of part, not each
  tool, and does not replay a past run.

### Replaying a run, a theme switch, and a box that jumps anywhere

- Under Runs, a chosen run gets a Play button. The recorded story's
  steps, with the steps inside each workflow, are played on the same
  diagram as a live run, at 1, 2 or 4 times the speed, with a progress
  line and a caption for the step. Nothing is run.
- A theme switch at the foot of the sidebar: as the computer, light, or
  dark, kept in the browser. Each set of dark colours is now written
  twice, once for the computer's setting unless light was chosen and
  once for dark chosen outright.
- Ctrl+K opens a box over the six pages and everything the Project
  page lists; arrows move, Enter opens, Escape closes.
- Tried in the real page: a recorded researcher run replayed to the end
  with one model call and two file calls counted, as recorded; the
  light theme was looked at for the first time and reads cleanly;
  typing "memory" in the box left the coach's memory, and Enter opened
  the coach.
- Not claimed: the replay gives every step the same length of time, not
  the time it really took, and shows the kinds of part, not what was
  said. It reads the story, so a step the story does not name a kind
  for moves no light. The jump box opens the agent, not the line. All
  three are page code checked by hand once each; the tests only check
  that they are there.

### One command to start, and a guide for a newcomer

- `kosika start` (`kosika/quickstart.py`) takes no options. It makes
  `KosikaAgents` in the home folder with three starter agents if there
  is none, asks Ollama on this computer what is installed, picks a chat
  model (a preferred one, else the largest under 9 GB) and an embedding
  model if there is one, says in plain words what it found and that it
  grants nothing, and then is an ordinary `kosika studio`.
- The three starter agents carry comments that explain each part to
  someone reading Kosika for the first time. Their code is unchanged.
- `GETTING_STARTED.md`: fifteen minutes from install to having changed
  an agent, written for someone who does not program. The README
  points to it first.
- An installable file: `python -m pip wheel . --no-deps -w dist` makes
  one `.whl` of about 630 KB.
- `tests/test_quickstart.py`. Tried for real: the file was built and
  installed into a clean environment with nothing downloaded;
  `kosika start` there made the three agents, chose qwen2.5:7b and
  nomic-embed-text, and opened Studio with the moving mark and the
  Project page working; all three starter agents then ran on the model
  to the end (63 s, 86 s and about a minute).
- Not claimed: nobody but the author has followed the guide, which is
  the whole point of it and is still to do. The clean install was on
  the same laptop, without the two tree-sitter packages the project
  lists as needed; Studio and the starters work without them, the Rust
  source reader was not tried, and a normal install would download
  them. Only Ollama is looked for. Nothing was tried on another
  operating system. There is no uninstaller beyond `pip uninstall`.

### Why two models were slow together

- Measured on this laptop with qwen2.5:7b and nomic-embed-text: a chat
  call with the model already loaded took 1.7 s; the same call straight
  after an embedding call took 14 to 16 s, of which 11 to 15 s was loading.
  After each call only the model just used was in memory.
- The cause is a setting in the owner's environment,
  `OLLAMA_MAX_LOADED_MODELS=1`. Every question that uses search by meaning
  therefore reloads the chat model. This confirms what was guessed when the
  second check made runs slower, and it also explains the slow fast-then-
  strong runs measured earlier, where two chat models took turns.
- `kosika start` now says so when that setting is 1 and search by meaning
  is on, with what it costs and how to change it. `tests/test_quickstart.py`.
- Not claimed: Kosika can only see the setting when it is in its own
  environment, which is where the owner's is; an Ollama started some other
  way with the same limit is not noticed.
- After the owner agreed, the setting was changed to 2 for his user account
  and Ollama restarted. Measured again on the same laptop: a chat call
  straight after an embedding call took 1.6 to 1.8 s, with both models in
  memory. The researcher's own 15 tests with search by meaning on took
  170 s, where they had taken 476 s, still 15 of 15. The forty questions
  took 828 s, where they had taken 1663 s, with the same result: 39 right,
  1 missed, 0 false. The run is kept in
  `benchmarks/researcher/results/20261011-004343_qwen2.5-7b_meaning_twomodels.json`.
- Not claimed: one run of each, on one laptop. Keeping two models loaded
  uses about 6.3 GB of memory together, where one used 5.9 GB.

### An agent that plans before it changes anything

- Before this, an agent that chose its own tools could make one change per
  step, and each change was asked about on its own. A task that needs
  several changes could not be done in one step.
- `plan: first` on an agent. Its model looks with the reading tools, then
  asks for every change in one reply. That list is the plan. It is handed
  over whole before any of it happens: Studio shows each change in order
  with its exact details and one button, and `kosika run --approve` prints
  the same list. The approval is bound to exactly that list, so a yes for
  one plan does not count for another.
- After the yes the changes run in order and stop at the first failure. A
  change that is not the next one in the approved plan is refused by the
  runtime, whatever the model asks.
- A step begins again after a person answers. For a planning agent the
  model is not asked again: what it said before the question is kept in the
  run's journal and given back, so the plan carried out is the plan shown.
- If the model asks for more changes after its plan was carried out, they
  do not happen and it is told so; it can still give its answer.
- The checker refuses `plan: first` on an agent with no model, with nothing
  it can change, or with fewer than 2 turns.
- The beginner's map says of such a step that the agent "shows you its
  whole plan of changes and waits for one yes".
- A ninth example agent, `examples/agents/builder`: it lists a folder and
  makes the files asked for, after showing the plan.
- Measured on this laptop with qwen2.5:7b, in a scratch folder: 8 requests,
  each asked about once, plans of 1 to 3 files. 7 finished, in 27 to 78 s.
  The one that did not was the model asking for more changes after its
  plan, which at that time ended the step; it is now told no instead, and
  the same request then finished. `tests/test_plan_first.py`, 14 tests.
- Not claimed: the plan is a flat list fixed before anything happens, so a
  later change cannot depend on what an earlier one gave back. The reading
  tools run again when the step begins again, and the model's kept words
  are used even if a file changed in between. If a change fails midway the
  earlier ones stay done; nothing is undone. What the agent says it made is
  the model's own words: in one run it listed `.hello.py` for `hello.py`,
  and in another it was asked for readme.txt, which existed, and made
  hello.txt without saying why. Only qwen2.5:7b was tried; a smaller model
  may not put several changes in one reply. Inside a session the plan card
  was not tried with a real model.

### Planning without being told, and skills picked beforehand

- The owner asked that he should not have to write `plan: first`, and that
  agents choose the skills a request needs before they start.
- Planning is now the default. An agent with a model, a tool that changes
  something, and at least two turns plans first. `plan: off` goes back to
  one change per step, each asked about. `plan: first` still exists and is
  checked. The builder example no longer says it.
- Skills are notes in a `skills` folder beside the program: a title, a
  `When:` line of words or phrases, and the know-how. Before an `ask` step,
  the skills whose `When:` words are in the request are picked, at most
  three, without a model. They are added to what the model is told, written
  into the `agent_call` trace event with the words that chose each, and
  shown in Studio's live view. `kosika studio` and `kosika run` read the
  folder. A skill adds no tool and no permission.
- The builder has three skills: readme, python, lists.
- Measured once on this laptop with qwen2.5:7b, six requests each way, in
  scratch folders. Each file made was checked against its skill's rules by
  code. With skills: 6 of 9 files followed the rules (readmes 3 of 3, lists
  2 of 3, Python 1 of 3), all 6 requests finished, 396 s. Without: 0 of 8
  files followed them, 5 of 6 finished, 223 s. The right skills were picked
  for all six requests.
- What the measurement also showed: two of the three Python files made with
  the skill were not usable, one with the characters backslash-n written
  where line breaks belong and one empty. Without the skill one of two was
  usable. A skill changes the shape of the work; it does not make a small
  model write a longer file correctly.
- `tests/test_skill_notes.py`, 14 tests; `tests/test_plan_first.py` gained 4.
- Not claimed: one run each way and nine files is a small sample. Skills
  were picked by words only at this point; the next section adds the
  model's own choice. Nothing checks afterwards
  that the work followed the skill. `kosika test` and `kosika measure` do
  not read the skills folder. Skills are given to `ask` steps only, not to
  plain model steps.

### Skills chosen by the agent's own model

- Picking by words misses a request that means a skill without saying its
  words. Two ways to fix that were measured before building either.
- By meaning, with nomic-embed-text: no use. Requests that fit a skill
  scored 0.47 to 0.64 against it and unrelated requests 0.45 to 0.53, so no
  line separates them.
- By asking the model: shown the three skills and twelve requests, of which
  none used a trigger word in seven, qwen2.5:7b chose the right set for 11
  of 12 in about 3 s each once loaded. Its one mistake was choosing the
  Python skill for "make a.txt b.txt and c.txt". qwen2.5:1.5b got 9 of 12.
- Built: after the word pick, the agent's model is asked once which skills
  fit. It can name only skills that exist. A wrong or unreadable answer, or
  a model that cannot be reached, leaves the word pick as it was. The choice
  is kept in the run's journal, so a step that begins again after a plan is
  approved has the same skills and the model is not asked to choose twice.
  The extra call counts toward the budget; the builder's went from 6 to 7.
- Measured through the builder on qwen2.5:7b, five requests with no trigger
  word: the skill picked was right for 4 of 5, the same mistake as above.
- What that run also showed, and it matters more: with the right skill in
  hand the model followed it in 1 of 4 files. Asked for "something to tick
  off before a weekend trip", it wrote that sentence into the file. Asked
  for a small program, it made an empty file. Picking the skill is now the
  easy part; a 7b model given a vague request does not do the work well,
  with or without the skill.
- `tests/test_skill_notes.py`, now 20 tests.
- Not claimed: one run. The check this calls for is the next section.

### A plan is checked before the person sees it

- The last measurement showed the model often did not follow a skill it was
  given. Asking a small model to review itself was not tried: the checks
  here are done by code.
- A skill note can carry `Files:` and `Check:` lines. A check is
  `starts with`, `contains` or `every line starts with`. When a file name
  fits more than one picked skill, the skill whose pattern says most about
  the name applies, so readme.txt is checked as a readme, not as a list.
- With or without skills, a planned file must not be empty, must not only
  repeat the request, must not have its line breaks written as backslash-n,
  and must not be written twice in one plan.
- A plan that fails goes back to the model once and nothing is shown or
  done. If the second plan still fails, the person is shown it with the
  problems listed: Studio prints them above the button and `kosika run
  --approve` under the plan. The person decides.
- The builder's three skills now carry checks.
- Measured on qwen2.5:7b, the same eleven requests as before, one run.
  Six clear requests: 6 of 8 files followed their rules, where it had been
  6 of 9; no file was empty or had broken line breaks, where two had. Five
  vague requests: 2 of 4 files under a rightly picked skill followed it,
  where it had been 1 of 4. Across both, three files still broke a rule the
  checker knows, and all three plans carried the warning.
- One request failed in both runs of this measurement: the model planned
  the same file twice and the second write was refused. That is now one of
  the things the check sends back. Not measured again after adding it.
- `tests/test_plan_review.py`, 14 tests.
- Not claimed: the checks are about shape. A to-do list of nonsense that
  starts every line with a box passes. The model wrongly given the Python
  skill for plain text files is not caught, because no file name fitted.
  When the lists skill is picked, every other .txt file in the plan is
  held to its rule too. The measurement approved every plan, so files with
  warnings were made; a person reading the warning might have said no.

### Questions the researcher had never seen

- The forty questions used so far are the ones the researcher was improved
  against, so its score on them flatters it. A second set was written on
  new notes about other things (garden, car, school, computer, kitchen, two
  clubs) and committed before the agent was run on it:
  `benchmarks/researcher/unseen`, run with `run.py --set unseen`.
- qwen2.5:7b with search by meaning, one run: 35 right, 1 false answer
  shown, 4 missed, in 958 s. On the first set the same agent scored 39, 0
  and 1. Without search by meaning: 31 right, 0 false, 9 missed, in 439 s.
- By kind, with meaning: exact-word questions 12 of 12, questions the notes
  do not answer 8 of 8 declined, questions with several near lines 7 of 8,
  questions worded differently from the notes 8 of 12.
- The false answer: asked who to phone if the car stops on the road, it
  gave the garage and its number, with a true quote, where the notes also
  name a breakdown service. The four misses were declined, each showing the
  closest lines, and in three the right line was among them.
- Not claimed: the questions were written by the same author who built the
  agent, so they are unseen by the agent's tuning but not independent. One
  run. Nothing was changed in the agent after this run.

### Tests on a real model for the agents that had none

- Six agents had only scripted tests: coach, planner, animator, doctor,
  workbench, scout. Each now has `eval` blocks that ask a real model and
  check the answer by code. The builder has none, because an eval cannot
  answer its plan question.
- Run once with `kosika measure` on qwen2.5:7b: coach 3 of 3 (68 s),
  planner 2 of 2 (27 s), animator 2 of 2 (124 s), doctor 1 of 1 (12 s),
  workbench 1 of 1 (15 s), scout 1 of 1 (58 s). 10 of 10.
- Not claimed: one run each, and the checks are loose. They ask that an
  answer has the right shape and mentions the subject, not that it is
  correct. The doctor and workbench evals reach only their advice step, not
  the steps that start programs.

### Two rough edges in the language

- A value written where a call belongs, such as `otherwise {matches: []}`,
  could not use anything: `otherwise {text: msg.text}` was an error, and a
  function had to be written for it. It may now use the workflow's inputs
  and the steps before it. Each name it uses becomes a parameter of the
  function Kosika makes for it, with that name's type, so it is checked
  like any other code and a step cannot use itself or a later step.
- An error about such a value used to name the function Kosika made, as in
  "Step 'function main$hits$otherwise' array item expects ...". It now says
  "Step 'hits' in workflow 'main': the value after `otherwise` array item
  expects ...". The line and column reported also became the step's own,
  where before they pointed elsewhere.
- `tests/test_value_names.py`, 6 tests.
- Not claimed: only check errors are reworded. The generated name still
  appears in traces and in `kosika story`, which already say it in words.
  The third rough edge listed earlier, the written contract falling behind
  the code, was handled as each change above was made rather than as a
  review of the whole contract; that review has not been done.

### Sessions: bring back some files, and choose the system

- A session's changes could only be brought back all together or not at
  all. In Studio's Session tab each file now has a tick. With one or more
  unticked, only the ticked files are brought into the real folder and the
  rest go when the session ends. A file name that is not among the
  session's changes is refused, nothing is brought back, and the session
  stays open.
- `kosika studio ... --in-session --session-system NAME` runs sessions in
  the named WSL system. The name must be one `wsl.exe -l` lists; it is
  handed to wsl.exe as one argument. The Session tab says which system.
- Tried for real on this laptop in a scratch folder, in Ubuntu and in
  kali-linux: a Python program behind the wall reported the right system,
  could not reach the network, wrote one file to the copy, and the real
  folder was unchanged. 0.3 s each.
- `tests/test_sessions.py` gained 4 tests; `tests/test_session_system.py`,
  5 tests.
- Tried end to end in Studio on a scratch folder, with qwen2.5:7b and
  sessions in kali-linux: the builder picked two skills, its plan of two
  files was shown once with no warning and approved, both files appeared
  in the session and none in the real folder. In the browser one file was
  unticked, which did not open it; the button then read "Bring the 1
  ticked file(s) into the real folder, throw the other 1 away"; pressing it
  brought that one file and reported the other left behind. This is also
  the first time the plan card was used inside a session with a real model.
- Not claimed: one such run. Kali's own tools were not tried, and most of
  them need the
  network or raw devices, which the wall keeps away; a session is for
  working on files, not for scanning a network.

### Retiring what Studio replaced

- Measured by following imports: Studio, the runtime, `kosika test`,
  `kosika start`, `kosika motion` and sessions reached 49 of 106 modules,
  23,000 of 52,700 lines. The owner chose to retire four groups that only
  older commands reached. Each is its own commit, so each can be brought
  back with one `git revert`.
- The first desktop app: `kosika.workbench` and its helpers, the packaged
  desktop host, its assets, docs and tests, and the CI step that started
  it. The `workbench` example agent is a different thing and stays.
- The city pictures: the city, cityscape, metropolis, live city, world and
  design modules, the `city` and `design` commands, `kosika run --design`,
  their tests and generated pictures. `kosika run --event-trace` stays.
- The Python authoring loop: `kosika.authoring`, its evaluation, and the
  `author` and `author-eval` commands. The `author` example agent, written
  in Kosika, does that job. `repair`, `corpus` and `benchmark` stay,
  because `kosika benchmark` and the diagnostics tests use them.
- The older maps: `audience_maps`, `explain`, `unified_mindmap` and
  `assistant`, with the commands `audience-map`, `unified`, `explain` and
  `assistant`. Studio's Map tab does this for a beginner. The `mindmap`
  module and command stay: other code and many tests use them.
- The full suite was run after each: 1710 passed after the first, 1587
  after the second, 1517 after the third with the coder added, 1440
  after the fourth. It now takes about 8 minutes, where it took 13.
- Not claimed: the untracked build folders under `desktop/` were left on
  disk, because they are not in git and were not mine to delete. The
  module `synapse_bridge`, listed with the desktop app, stays: the event
  trace of `kosika run` uses it.

### A coder agent

- The owner asked for basic agents, a researcher and a coder, to grow into
  assistants. The researcher exists. `examples/agents/coder` is new.
- Given one small task in a Python project, it lists and reads files,
  plans every file it will write with its whole new text, and after one
  yes writes them. The workflow then runs `python -m pytest` itself, so
  the tests are run by code and not left to the model. The report says
  what was written and whether the tests passed. `fix` asks it again with
  what the tests printed.
- It has one writing tool, which replaces a whole file. A first draft had
  two, create and replace, and the model used create on a file that
  existed, which failed the step. One tool removed that mistake.
- When nothing was written, passing tests are not reported as success: it
  says "Nothing was changed, so the task is not done."
- Two skills: how a function is added, and how a test file is written,
  each with checks.
- Tried on qwen2.5:7b in a session, on a scratch project with two
  functions and two tests, asked to "add a function is_even to calc.py
  that says whether a number is even, with a test". Run 1, first draft:
  failed as described above. Run 2, same draft: the plan held both files
  but one used create on an existing file, and nothing was written. Run 3,
  after the change to one tool: the plan wrote calc.py and test_calc.py,
  kept the existing functions and tests, the tests passed in the session,
  and the real folder was untouched. 151 s.
- Not claimed: one success on one easy task. It replaces whole files, so
  a long file is beyond a 7b model. It has no eval, because an eval cannot
  answer the plan question. Only Python and pytest. The `fix` path was
  not reached in a real run, because the one good run passed first time.

### Granting from inside Studio

- The owner tried to start Studio with `--allow-folder Aryan_Private` and
  was told it was not a folder. Two things followed. The message now shows
  the full path that was tried and asks for a full path. And he asked to do
  this in the app instead of the command line.
- Studio's Permissions page now has "Choose a folder", which asks the
  server to open the computer's own folder window (Tk, run as a separate
  process). What is picked there is remembered on the server. The page can
  then confirm it, keep the current folder, or remove it; it cannot send a
  folder name of its own. Two switches set starting programs and sessions.
- Refused: a whole drive, the user folder or anything holding it, and
  system folders. Refused while an agent is working or a session is open.
  Turning sessions on checks first that the wall works, and says why if not.
- Found while trying it in a real Studio window: two changes pressed
  quickly overlapped, and the slower one put back the folder the faster one
  had removed. Changes are now made one at a time on the server, and the
  page disables its buttons while one is applied. A test holds the first
  change open and shows the second waits.
- Tried in the browser on a scratch folder: the page showed the granted
  folder, the programs switch took effect, the session switch said it was
  checking and then took effect, and "Stop sharing this folder" removed the
  folder and both switches. `kosika start` and the beginner's guide now
  point here instead of at a command. `tests/test_grants.py`, 19 tests.
- Not claimed: the folder window itself was not opened in this work. It
  appears on the owner's screen and needs a person to answer it, so the
  owner's first use is its first real test. Tests use a stand-in for it.
  The model, websites, MCP servers and search by meaning are still chosen
  when Studio starts. The grant lasts until Studio stops; it is not saved.

### Vectors, matrices, relations and graphs

- The owner is studying discrete mathematics and asked whether Kosika should
  have matrix operations and graphs. A first answer said yes for discrete
  maths but not for heavy numerical work. He disagreed: he wants Kosika
  usable by maths and science students for real calculation. He was right
  that it can be: Kosika runs on Python, and Python's own speed at this
  comes from NumPy, which was already installed.
- Before building, matrix multiplication was written in today's Kosika as a
  check. It worked and took about 20 lines of loops.
- `kosika/matrices.py` and 36 built-in methods on `Number[]`, `Number[][]`
  and `Number`. Whole numbers are exact at any size, by Python's integers
  or, for large products of small numbers, NumPy's 64-bit integers when a
  bound proves they cannot overflow. The determinant of an integer matrix
  is exact (Bareiss). Decimals go to NumPy when present and to plain Python
  otherwise, and are rounded to 9 places.
- Relations as 0/1 matrices: the four properties, the Boolean product, and
  Warshall's closure. Graphs: adjacency from edges, degrees, neighbours,
  shortest path breadth first, connected parts.
- Shapes are checked before any arithmetic and the message gives them.
- A vector and a matrix share method names such as `plus`. At run time the
  method was found by name alone among arrays, which would have picked the
  wrong one; it is now found from the value's own shape first.
- Measured on this laptop, while other work was running: a 500 by 500
  decimal product 0.26 s with NumPy; 200 by 200, 0.08 s with NumPy and
  1.1 s in plain Python; solving 300 equations 0.7 s.
- `tests/test_matrices.py`, 24 tests, among them the two engines compared on
  the same inputs, and `examples/discrete_maths.kos` with 6 tests.
- Not claimed: shapes are checked when the program runs, not when it is
  checked, because the type `Number[][]` does not carry a size. There are
  no eigenvalues, no complex numbers, no calculus and no statistics beyond
  the mean yet. A matrix is a list of lists, so very large ones use far
  more memory than NumPy's own arrays, and each call converts in and out.
  The limit is 4,000,000 numbers. The owner was told eigenvalues would be
  there; they are not, and are the next thing to add.

### One box that finds the right agent

- The owner wants to grow a researcher and a coder into assistants. A first
  step is not having to choose the agent: Studio's Assistant page has a box
  that takes any request and passes it on.
- Calling one Kosika program from another was looked at first. An import
  from outside the program's own folder is refused, and two agents declare
  the same names (`Msg`, `Said`, `main`), so it cannot be done in the
  language today. The choosing is done by Studio instead, and the chosen
  agent then runs as itself, with its own grants, plan and session.
- The agents' purposes are shown to the bound model, which names one. It
  can name only an agent that exists. With no model, or an answer that is
  not one of them, the agent whose description shares the most uncommon
  words with the request is used; failing that, the usual one. The page
  says which agent was chosen and why, and nothing is run by the choosing.
- First real try: 2 of 3. "What do my notes say about the rent?" went to
  the workbench. The model had been shown 320 characters of each program's
  top comment, most of it how to run the program. It is now shown only the
  first paragraph, which says what the agent is for.
- Measured after that, qwen2.5:7b, twelve requests across the ten example
  agents: 11 of 12 went to an agent that fits, about 7 s each once loaded.
  The miss: "explain photosynthesis and then test me" went to the
  researcher where the coach fits. `tests/test_agent_routing.py`, 13 tests.
- Not claimed: one run. A request that needs two agents goes to one. An
  agent with no comment at the top cannot be chosen well. Agents calling
  agents inside the language is still not possible.

### What an agent really changed, and the coder measured

- Measured before any change, qwen2.5:7b, six small tasks in sessions on
  scratch Python projects: 3 of 6 done, where done means the tests pass and
  the asked-for change is in the files. Two of the three failures were the
  model claiming work it had not done: in one its plan held only one of the
  two files it then said it wrote, and in the other it made no plan at all
  and answered "Created greet.py ... and test_greet.py". The third was a
  bug fix where it also rewrote the test file and dropped its import.
- An agent's answer record may now have a field `did: Text[]`. The runtime
  fills it from the transcript: each call of a tool that changes something
  whose result was not an error, as the tool and its path. The model's own
  value is thrown away. Replay works the list out again from the recorded
  calls and refuses a trace whose `did` differs.
- The coder and the builder report from `did`. With nothing written they
  say so, and show the model's sentence only as what it said. The coder
  asks its model once more when nothing was written, and is told to leave
  the tests alone when fixing a bug. `tests/test_agent_did.py`, 6 tests.
- Measured again after these changes, same six tasks, one run: 4 of 6
  done, where it had been 3. Three of the four needed the `fix` round to
  get there. No report named a file that was not written. The two that
  failed were real mistakes left standing after a fix: an import of a name
  that no longer existed after a rename, and a new test file that did not
  import the function it tested. The run took 3082 s where the first took
  1519 s, because the fix rounds and the second try cost model time, and
  for part of it other work was using the same model.
- Not claimed: one run of six easy tasks each time, so 3 and 4 are not far
  enough apart to be sure of. `did` says what was written, not whether it
  was right.

### Maths, saved for later

The owner asked for the rest of the maths work to be planned and kept for
later, and for the desktop app first. Planned, in this order, none of it
built:

1. Eigenvalues and eigenvectors of a square matrix. The difficulty is
   giving the same answer with and without NumPy, and saying plainly when
   they are complex numbers, which Kosika does not have.
2. Functions on whole vectors: `sqrt`, `exp`, `log`, `sin`, `cos`, powers,
   and statistics beyond the mean (median, variance, standard deviation).
3. Calculus helpers: a numerical derivative, an integral, and root finding,
   each with a stated error.
4. Libraries written in Kosika itself, on top of the built-in methods, for
   topics such as number theory, statistics and geometry. A library is a
   `.kos` file of functions that is imported. The owner's discrete maths
   course is the natural first one, and he may want to write it himself.
5. Drawing a graph or a mind map in Studio from its adjacency matrix, using
   the scene drawing that already exists.

The owner's own next step, if he wants it: write matrix multiplication by
hand in `my_examples`, then check an eigenvalue with `apply` and `scaled`.

### Kosika Studio as a desktop app

- The owner asked for Studio on his desktop "like a real functioning app".
- `kosika desktop [FOLDER]` writes an icon and makes a shortcut on the
  Desktop and in the Start menu. The shortcut runs `pythonw -m
  kosika.desktop_app`, which does what `kosika start` does with no console,
  opens Studio in a browser app window, and stops when that window closes.
  `kosika desktop --remove` deletes the two shortcuts.
- The window is given browser settings of its own. Without that the browser
  hands the page to one already running and exits at once, so there is
  nothing to wait for; with it, the process lasts as long as the window.
- The icon is drawn in code, a dark tile with an orange ring and a pale
  dot, at 16, 32, 48 and 256 pixels, and is also served as the window icon.
- With no console, what would be printed goes to a log file that is started
  afresh each time, and a failure to start shows a message box.
- The folder name is handed to PowerShell in the environment, never pasted
  into the command, and a name holding a quotation mark is refused.
- Done for the owner on this laptop: the two shortcuts were made, pointing
  at `examples/agents`. Tried for real: opened through the Desktop shortcut,
  the window appeared, the app ran under pythonw with no console, and
  closing the window ended the app. `tests/test_desktop_app.py`, 15 tests.
- Not claimed: Windows only. It is a shortcut to the Python already on the
  computer, not a packaged program: it needs Python and this folder to stay
  where they are, and Chrome or Edge for the window. Opening it twice
  starts two Studios. Closing was tested by ending the window's process,
  not by a person pressing the X. The log holds the address with its token,
  in a folder only this user can read.

### An assistant to talk to

- The owner asked for an assistant "with reference to Siri", made with
  Kosika, on his computer.
- `examples/agents/assistant`. By rule and in plain code: the time and the
  date from the clock, reminders that are kept, listed and finished, things
  it is told to remember, and forgetting it all. Anything else goes to the
  model with the date, the time, the reminders and what it remembers, and
  an instruction to answer in one to three sentences fit to be read aloud.
  It has a clock and its own memory and no other tool.
- Studio opens on it, because it is the folder named `assistant`, and its
  chat has a switch "Speak answers aloud". Only a voice the browser marks
  as living on this computer is used; one that would send the words to a
  service to be spoken is never picked. The switch is off until chosen.
- Tried on qwen2.5:7b: the time, a reminder and a thing to remember each
  answered in about a second with no model call; questions took 10 to 26 s
  while another job was using the model. It used what it had been told
  ("when is my exam?" gave "Your discrete maths exam is on Friday").
- The same try found the fault that matters most in an assistant. Asked to
  "open my web browser and search for cats", it answered "Sure, I'll open
  the web browser and search for cats". It can do no such thing. Two
  guards were added, both in code: a request that begins with a verb for
  acting on the computer (open, play, search, send, set and others) is
  answered with a fixed sentence saying it cannot, without asking the
  model; and any model answer that claims such an action is replaced by
  that sentence.
- In a real Studio window: the assistant was the page shown, nine voices
  were found of which six English ones are on this computer, a sentence
  was spoken at zero volume to its end, and "what time is it" typed into
  the chat was answered in half a second. 7 tests in the program, 3 evals,
  and `tests/test_assistant_agent.py` with 6 tests.
- Not claimed: it does not hear. The owner was asked to choose between
  local speech recognition (a download of about 150 MB), the browser's
  own (which sends the voice to Google), and typing; nothing that opens
  the microphone is in the page until he answers. There is no wake word.
  It cannot open programs or act on the computer, by design. Reminders
  have no times and nothing rings. The guard against false claims is a
  list of phrases, so a claim worded differently can still get through.
  Speaking aloud was heard by no one: it was run silent.

### Kosi: the assistant made to feel alive

- The owner chose typing over a microphone, asked what the assistant is
  called, and asked for Siri's animations and features. It had no name. It
  is now Kosi by default; `call yourself Nova` renames it and `call me
  Aryan` tells it who it is talking to. The look is Kosika's own, grown
  from the ring in the desktop icon, not a copy of Apple's.
- In the program, all in plain code with no model: a greeting by the hour
  with the person's name and how many reminders wait; sums worked out with
  multiplying and dividing before adding and taking away, from words or
  signs, with percentages; reminders that may carry a time written as
  "at 6pm", "at 18:30" or "in 10 minutes", turned into the moment they fall
  due; and a few things to say next, returned beside each answer in a
  `suggest` field.
- In Studio: an orb above the assistant's chat, still when idle, turning
  fast while a run is going, pulsing while an answer is spoken or shown,
  and ringing when a reminder is due. It is drawn with CSS only and stops
  for a person who asked for less motion. The assistant says hello when
  the page opens. Suggestions are shown as things to tap. Every 15 seconds
  the page reads the assistant's memory and shows, and says if speaking is
  on, any reminder whose time has come, once.
- Tried in a real Studio window on a scratch copy: the greeting was "You
  are up late." at 1:45 at night; "what is 17 times 23" gave 391 in 0.7 s
  with the orb turning and then pulsing; "call me Aryan" was answered and
  offered "hello"; "remind me in 1 minute to stretch" was answered "at
  1:46" and, a minute later, "Reminder: stretch." appeared by itself with
  the orb ringing. No errors in the page's console.
- 11 tests in the program, and `tests/test_assistant_agent.py` now has 11.
- Not claimed: a reminder is announced only while Studio is open on the
  assistant's page; nothing runs when the app is closed. The sums cover
  the four operations and percentages, not brackets or powers. A time such
  as "tomorrow at 9" or "on Friday" is not understood: the words are kept
  as part of the reminder with no time. The orb was looked at once in a
  screenshot; whether it feels right is the owner's to say.

### Kosi as a pill that stays on the screen

- The owner asked for "a live interface on screen always minimized", like
  the small translucent grey pill of a dictation app: hover to see what it
  is for and its key combination, and reach it easily.
- `kosika pill [FOLDER]` (module `kosika.pill`) shows a small borderless
  window against the right-hand edge of the screen, half way down, above
  other windows: at rest a small translucent grey pill standing on end
  with only its ring, the way a dictation pill lies along the bottom
  edge. Hovering, thinking or opening it widens it to the left. (It began
  at the bottom centre; the owner asked for the right-hand side, its
  middle, and a sharper look.)
- How it is drawn: the program tells Windows it draws at the screen's real
  resolution, so nothing is stretched on a scaled display. The pill is a
  picture made with Pillow, drawn four times too large and brought down,
  with a hair of lighter edge and a soft shadow, and handed to Windows
  with every pixel saying how solid it is (`UpdateLayeredWindow`), so the
  edge fades instead of ending in steps. The line to type on is a window
  of its own laid over the pill. The answer bubble asks Windows 11 for
  rounded corners. Without Pillow, or off Windows, it falls back to plain
  shapes. Hovering wakes it and shows "Ask Kosi" with the key
  combination. Clicking it, or pressing the combination from anywhere,
  opens a line to type in; Enter asks, Esc closes. The answer appears in a
  bubble above the pill with what Kosi offers next as chips, and stays up
  longer for a longer answer. Right-click: Ask, Open Kosika Studio, Quit.
- `kosika desktop --kosi` makes its Desktop and Start-menu shortcut, and
  `kosika desktop --kosi --remove` takes it away.
- It runs the same `assistant` agent through the same `App` as Studio, so
  the reminders and facts are the same ones, and a reminder that comes due
  is announced by the pill even with Studio closed.
- Only one pill runs at a time. Only one key combination is registered with
  Windows (`RegisterHotKey`); the first free of Ctrl+Alt+K,
  Ctrl+Shift+Space, Ctrl+Alt+J is taken, since another program may hold
  one. No other key is seen, and a test checks the module holds no keyboard
  hook, microphone or screen capture.
- With no model on the computer the pill still starts: sums, the time and
  reminders work, and an open question gets a polite "could not think of an
  answer".
- Verified on the owner's laptop, first by inspecting the window and
  then by capturing only that corner of the screen: the pill at rest, the
  opened line with typed words, and the greeting bubble with its chips
  were each seen. 22 tests in `tests/test_pill.py`.
- Every agent's own tests on the real model (qwen2.5:7b), as a baseline:
  researcher 5 of 5, coach 3 of 3, planner 2 of 2, animator 2 of 2, doctor
  1 of 1, workbench 1 of 1, scout 1 of 1, assistant 2 of 3.
- Not claimed: the hover state was seen only as a rendered picture, not
  live. The window code itself (hover, the bubble, the hand-over to
  Windows) has no automated test, only the logic and the picture under it. It does not start with Windows. Windows only.
  It answers nothing that needs a grant: if the assistant ever asked for
  one, the pill says no.

### Kosi understands more, in plain code

- The owner asked for a smarter Kosi. Everything here is worked out by the
  Kosika program itself, with no model, so it is exact and instant.
- Times: "tomorrow at 9", "tomorrow" (nine in the morning), "on Friday at
  5pm", "next Sunday", "today at 6pm", "in 2 days". A time that is not
  today says its day: "at 9:00 on Tuesday". A day of the week that is
  today with its time past means a week from now.
- "timer 25 minutes" and "alarm at 7am" are reminders with a time, so the
  pill and Studio announce them like any other.
- Sums: brackets (innermost first), powers ("^", "squared", "cubed", "to
  the power of") and square roots, before multiplying and adding.
- Units: length, weight, volume, time, speed and temperature, in "convert
  5 km to miles", "5 km in miles", "how many feet in a mile", "5km to m".
  Two different kinds of unit get a plain refusal.
- Dates: "days until 25 December", "how many days until Christmas", "what
  day is 1 January 2030". The count of days is integer arithmetic written
  in Kosika (`day_count`), checked against Python's own for five dates.
- 17 tests in the program, up from 11.
- Not claimed: an alarm or timer is announced only while the pill or
  Studio is open; nothing rings with both closed, and Kosi says so when
  an alarm is set. "tonight", "next week", "noon" and "half past" are not
  understood. Seconds are not: a timer is in minutes, hours or days.
  Gallons, cups and currencies are left out on purpose (they mean
  different things in different places, or change by the day). Open
  questions are no smarter than before: that needs a better model.

### Two typefaces of its own

- The owner asked for a fancier font for Kosi and Kosika and liked the
  ones on a dictation app's website. That site uses EB Garamond for
  headings and Figtree for the rest; both are under the SIL Open Font
  License, so Kosika carries them. He approved the download (under 1 MB).
- The files and their licence texts are in `kosika/app_assets/fonts/`.
  `kosika/fonts.py` says where they are and gives out only those two.
- Studio: EB Garamond for the name, headings and card titles; Figtree for
  everything else. The page asks this computer for them at `/fonts/...`
  and nowhere else, and the page's security policy now allows fonts from
  itself only.
- The pill: Figtree for its label, the typing line and the answer. The
  fonts are lent to the running program only (`AddFontResourceEx`, kept
  private); nothing is installed into Windows.
- Seen: Studio's page reported both families loaded and was looked at;
  the pill's bubble and label were captured with the new face. 6 tests in
  `tests/test_fonts.py`.
- Not claimed: the serif is not used on the pill, where small light text
  on dark is easier to read without it. Only Studio's first page was
  looked at with the new headings, not every page.

### Methods the assistant's own program wished for

- Writing Kosi's new skills in Kosika showed what the language lacked: each
  of these had to be written by hand inside the assistant first.
- `Number`: `abs()`, `power(exponent)` (a whole power of a whole number is
  exact, anything else is rounded to nine places, and what cannot be held
  fails the run in plain words), `is_whole()`.
- Any array and `Text`: `index_of(...)`, giving `Number?` so "not there"
  is absent rather than a made-up -1.
- `Text`: `chars()`, `pad_left(width, fill)`, `pad_right(width, fill)`.
- The assistant now uses them: its hand-written power loop, zero padding,
  day lookup and absolute value are gone, and its 17 tests pass unchanged.
- 12 tests in `tests/test_more_methods.py`; the methods are in the table
  in `KOSIKA.md`.
- Not claimed: there is still no `**` operator and no `+` between arrays
  (`power` and `concat` are the ways). Indexing a list written in place,
  such as `["a", "b"][0]`, was not tried. `chars()` splits by code point,
  so an emoji made of several is several parts.

### One agent handing a request to another

- The owner wants Kosi to grow into an assistant that gets work done
  through the other agents. Until now only the person could choose an
  agent; no program could.
- No new syntax. Another agent is a tool: `source system "agents"`,
  `operation: "ask"`, taking `{agent, text}` and giving `{ok, agent,
  text}`. A program that wants to hand work over declares it and calls it
  like any other tool.
- Handing over grants nothing, which is why the tool may be `observe`.
  The other agent runs as if the person had opened it: its own tools, the
  same grants, its own run kept in its own folder, and its own questions
  before anything changes. Studio shows such a question as "This was
  handed to coder, which asks: ...". A no stops that agent only; the
  asking agent is told it was refused and carries on.
- Limits: an agent cannot hand to itself; an agent that was handed a
  request is not given the tool, so there are no chains or loops; an
  empty name lets Studio's own router choose.
- `App._work`'s approval loop became `App._run_asking`, used both for a
  run and for a handed-over run, so the two cannot drift apart.
- Kosi uses it: "ask the coder to ...", "tell the researcher to ...",
  "@coder ...", and "hand over: ..." go to the other agent, and the
  answer comes back as "The coder agent says: ...". Its time budget went
  from 120 to 900 seconds, because a handed request takes as long as the
  other agent takes.
- Tried for real on qwen2.5:7b in a scratch folder: "ask the coach to
  explain what a matrix is in two sentences" came back in 33 seconds with
  the coach's answer; a made-up agent was named as missing with the list
  of real ones.
- 11 tests in `tests/test_hand_over.py`; 18 in the assistant, up from 17.
- Not claimed: from the pill, an agent that asks to change something is
  refused, because the pill has nowhere to show what is asked; that work
  is for Studio. The other agent's steps are not shown live in Studio,
  only "handing this to coder" and "coder has answered". The agents are
  not asked together: one hand-over at a time, and the asking run waits.
  `kosika run` on the command line does not bind the tool.

### The researcher works an answer out, and says so

- The owner asked whether the researcher works perfectly. Asked five real
  questions about a scratch folder on qwen2.5:7b, it answered what a line
  states and refused what no line says, but refused everything that needs
  a step: two marks added together, a time against a closing time, "a
  tree" against "a connected graph with no cycles".
- The cause was the program's own check, not the model: an answer had to
  use words found in the quoted line, so "40" was thrown away because the
  line says 15 and 25.
- Now, when nothing can be proven word for word, the model named
  `thinker` is asked to work the answer out from one line and to say the
  step. Code still checks that the quote is really in a found line, is not
  a heading, shares a key word with the question, and (with search by
  meaning) is among the nearest lines. The answer is shown marked "Worked
  out, not stated word for word", with its step, quote and line.
- A second look. On the first try the model wrote "6pm is after 5pm" and
  answered "Yes" to "is the library open at 6pm". So the model named
  `judge` reads the line, question, answer and step and says only
  "follows" or "wrong"; a "wrong" means no answer is shown.
- Measured on qwen2.5:7b, ten evals run twice: 19 of 20. The five old
  evals 10 of 10; adding, multiplying and using what a word means 6 of 6;
  refusing what no line is about 2 of 2; the closing-time question 1 of 2,
  and the miss was a refusal, not a wrong answer. Before the change the
  five new evals could not pass at all.
- 15 tests in the program, up from 11; its budget is 7 model calls and 600
  seconds, up from 3 and 240.
- Not claimed: a worked-out answer is checked by a model, not proved by
  code, so it can still be wrong; that is why it is marked and shows its
  step. A question that needs two lines together is not attempted. The
  slow path costs up to four more model calls, about a minute on this
  laptop. Twenty runs is a small sample.

### The pill asks before an agent changes anything

- Kosi could hand a request to the coder from the pill, but the coder
  could do nothing there: the pill granted no folder, and any request to
  change something was refused because there was nowhere to show it.
- A folder is now chosen from the pill's right-click menu, in the
  computer's own folder window, through the same grant Studio uses (so a
  folder that is too wide, such as a whole drive, is refused). It lasts
  until the pill is quit. Starting programs is not granted from the pill.
- When an agent wants to change something, the bubble above the pill says
  who asks and exactly what ("The coder agent wants to make these 2
  changes, in this order: 1. write the file calc.py ..."), with Allow and
  No. The bubble stays until one is picked, nothing else may cover it, and
  no answer in five minutes is a no. `ask_words` writes that text and is
  tested on its own.
- Seen: the question bubble was shown and captured on the owner's screen.
  27 tests in `tests/test_pill.py`, including a real hand-over to an agent
  that writes a file, allowed once and refused once.
- Not claimed: a plan of more than six changes shows the first six and
  points to Studio. The text of a file about to be written is not shown,
  only its name and length. No real coder run was approved through the
  pill by a person yet.

### The researcher puts two lines together

- A worked-out answer may now rest on two lines: "which homework is worth
  more" when each homework has its own line. The second quote is held to
  the same rules as the first (real, not a heading, about the question)
  and both lines are shown.
- The first version of this measured worse, 16 of 24, and the cause was
  the second look, not the answers. Asked only "follows or wrong", the
  small model called right answers wrong about half the time. Three
  changes, each from reading the model's own words:
  - The judge now works the answer out for itself, in writing, before it
    sees the answer to compare with.
  - Code decides whether two answers agree where it can: two answers with
    numbers agree when they share a number, two that say yes or no agree
    when they say the same. The model had written "15 + 25 = 40 marks"
    and then said "40" disagreed.
  - A small model asked for two quotes often writes both in the first.
    When the whole is in no line but two of its sentences each are, they
    are taken as the two quotes.
- Measured on qwen2.5:7b, twelve evals three times each: 34 of 36. Ten
  evals passed 3 of 3, including both two-line ones. The closing-time
  question passed 1 of 3: the model reworded its quote, which is refused,
  so both misses were refusals and not wrong answers.
- 18 tests in the program; its budget is 9 model calls and 900 seconds.
- Not claimed: where neither a number nor a yes or no is in the answer,
  the judge's own word is still what decides, and that is the weak case.
  Three lines together are not attempted. The slow path now takes up to a
  minute and a half on this laptop.

### The coder tested for real, and its plan checked in Kosika

- The owner asked for the coder to be tested, "because this agent will
  write the code to develop Kosika itself". Six small tasks in a scratch
  Python project on qwen2.5:7b, each really run with its tests: 3 of 6.
- Read from the files it left: it wrote a file again with only the new
  function in it, losing the others; it put tests in the source file; a
  test forgot its import; one file was not valid Python.
- None of that needs a model to see. A plan is now checked before a person
  is asked, and sent back to the model with what is wrong.
- The owner then asked why this was written in Python "when Kosika can do
  it itself", and said he wants Kosika gradually independent of Python.
  The first version of these checks was all in the engine. It was taken
  apart again:
  - A language change: an agent may name a function of its own program
    with `check: NAME`. It is given the request and every file the plan
    would write, with the file as it is now, and returns what is wrong.
  - `Text.lines()` was added, because `split` stops at 512 parts and a
    file has more lines than that.
  - The checks that only read lines are now in the coder's own program, in
    Kosika: a function the file has and the plan drops, tests in the wrong
    file, a task that asks for a test and writes none. 3 tests there.
  - Only what needs a Python parser stayed in the engine
    (`kosika/code_checks.py`, 187 lines): not valid Python, a name never
    defined, an import the other file does not have.
- A bug found by the tests of my own check: "add a function" was excusing
  the loss of the function named `add`, because the request named it. A
  function is now excused only when the request also speaks of renaming
  or removing.
- What is read for the check is journaled. Without that, a step resumed
  after approval would check its plan against the files it had just
  written, see nothing lost, and no longer match what was approved.
- Measured, and scored strictly. My first scoring counted a task done when
  its tests passed and the new function was there, which let through runs
  that had deleted the project's other functions and rewritten the tests
  to match. Counting only runs where everything the project had is still
  there: 2 of 6 before the checks (not the 3 of 6 first reported), 5 of 6
  with them. The sixth reached the approval step carrying three warnings.
- A plan found wrong now goes back to the model twice, not once
  (`PLAN_RETRIES`). On the task that failed, three more runs: 3 of 3.
- Asking an outside agent (`kosika/outside.py`, not yet reachable from a
  program). The owner has other command-line agents installed and said
  they may be given folders inside `Aryan_Private` only. The folder is
  copied, the agent is started in the copy allowed to edit files and
  nothing more, and what it changed comes back as a list; the real folder
  is not touched. A folder outside the one named root is refused before
  anything is started. Tried by hand on a scratch project: devin and omp
  each did the task the local coder failed, correctly, in 31 seconds; kilo
  changed nothing (it is set to an image model); freebuff cannot be run
  with one request. 12 tests, with a stand-in for the agent.
- Not claimed: six tasks and three reruns are a small sample, all of one
  kind (small Python functions). No program can ask an outside agent yet.
  The three engine checks are for Python only. The coder still writes
  whole files, which is slow: 4 to 13 minutes a task on this laptop.
- Stage 1 of five toward Kosika being independent of Python: hooks an
  author needs are written in Kosika. The others, not started: a standard
  library in `.kos`; Kosika's lexer in Kosika; its parser and checker in
  Kosika; an engine that is not Python.

### The coder asks an outside agent first

- Two more pieces of language, so that this could be written in the
  coder's own program and not in the engine: a `system "outside"` tool
  (`propose`), and a `system "files"` operation `write_all` that writes
  several files with one approval. A workflow cannot repeat a tool call
  over a list, which is why the second was needed.
- The coder's program, in Kosika: ask the outside agent; run the same
  `plan_check` on what it proposes; if something is wrong, ask it once
  more with what was wrong; if that fails too, or nobody can be asked, do
  the task with the local model as before. A proposal that passes is
  written with `write_all` after one approval, and the tests are run.
- The owner's limit is a setting, not a habit: `kosika outside --allow
  NAMES --within FOLDER`, kept in `outside.json` beside the app's other
  files. Off until set. The tool is bound only for allowed agents and only
  when the granted folder is inside that root. Studio and the pill read
  the setting when they start.
- Measured on the same six tasks, scored strictly, with devin: 6 of 6 in
  745 seconds (2 of 6 in 2044 s with the local model and no checks; 5 of 6
  in 3152 s with checks). Five tasks took 25 to 48 seconds.
- The sixth showed a fault in the check. On "rename double to twice" it
  refused devin's correct work twice, because the test `test_double` was
  renamed too and counted as lost; the coder fell back to the local model
  and finished in 568 s. The check now lets the test of a renamed or
  removed function go with it. Rerun: 30 seconds.
- 16 tests in `tests/test_outside.py`, four of them the coder end to end
  with a stand-in for the outside agent; 13 tests in the coder's program.
- Not claimed: what an outside agent proposes is not put through the
  engine's Python checks (valid Python, undefined names), only through
  the coder's own check and then the real tests. Six tasks is a small
  sample and all are toy-sized. The outside agents were told not to run
  programs but are trusted to obey; they are started with edit-only
  permission, which is their own setting, not Kosika's. No real project
  of the owner's has been given to one yet.

### An outside agent on Kosika itself

- The first job that was not a toy: devin, in a copy of this repository,
  asked to add `Text.capital()` with its test and its row in the reference.
- Kosika's folder was refused at first as too large (over 40 MB): caches
  and old builds. An outside agent is now shown what git tracks or would
  add, 515 files and 5.5 MB here, so what a project ignores stays private.
  Files named like secrets (`.env`, `*.pem`, `id_rsa`, ...) are never
  shown, in a git project or not.
- Result of the second run: 56 seconds, three files changed and nothing
  else, one line in each of the engine and the reference and one test.
  Applied to a second copy: the two relevant test files pass (43 tests),
  ruff is clean, the engine's own plan checks find nothing wrong. The
  change was not applied to the real repository; it was a trial.
- What the first run showed. It took 185 seconds because devin ran the
  tests, although it was told not to run programs and was started in the
  mode that approves edits only. It did so inside the copy. The caches it
  left came back as "changed files"; they are now dropped, as anything
  the project's git rules ignore is.
- Not claimed: an outside agent cannot be stopped from running programs
  on this computer. It is told not to and it works in a copy, but that is
  its good behaviour and its own setting, not a wall of Kosika's. The
  coder's flow through Studio was not used for this job, because it would
  run all of Kosika's tests (about 17 minutes); the proposal was tested by
  hand with the two test files that matter. One job is one job.

### The coder runs only the tests that matter

- Kosika's own tests take about 17 minutes, so the coder could not be
  used on Kosika. Written in the coder's program, in Kosika: after a
  change, pytest is asked for the tests whose names hold the name of a
  changed Python file (`-k "builtin_methods or more_methods"`). If that
  finds none, they are all run, so a change is never left untested. The
  report says when only some were run and how to run them all.
- The whole flow on a copy of Kosika, with devin: asked, checked, the
  three files written after approval, the narrowed tests run, 97 seconds
  from start to finish. The test run was 23 seconds of that.
- 15 tests in the coder's program.
- Not claimed: tests are chosen by file name only. A change to a file
  whose tests are named differently, and whose name appears in no test's
  name, falls back to running everything; a change that breaks a test of
  another name is not seen until all are run. devin again ran tests of
  its own in its copy.

### A second agent reads the change, and the coder writes Kosika

- A reviewer. With two outside agents allowed, the one that did not write
  a change reads it before the person is asked: `system "outside"`,
  operation `review`. The change is applied in a copy with the old files
  beside it, because an agent handed the new text in its request read the
  files on disk and reviewed those. The local model then says only whether
  the review reports a problem; if it does, the author is asked once more,
  told what was found. What the reviewer said is shown with the list of
  files (`note` on `write_all`), before the yes.
- Tried alone on four changes, one sound and three with a fault their
  tests did not catch (a wrong upper bound, a test that asserts nothing, a
  change nobody asked for): omp and devin each passed the sound one and
  found the fault in the other three.
- Two faults of mine found on the way, and one cost.
  - The request to an outside agent was cut at its first line break. It
    was started through the Windows shell, which ends an argument there.
    The agents had been given the task and never the rules after it, such
    as "do not run programs". They are now started directly. With the
    whole request, omp answered "No programs, tests, or installers run".
  - The measuring script looked for the words "The tests pass." and so
    counted every narrowed run, "The tests for calc pass", as a failure,
    then asked for a "fix" that damaged sound work. That run is discarded.
  - devin's free daily allowance was used up by these runs. An agent that
    ends in an error now counts as no answer, and when none was named the
    next allowed one is asked.
- Kosika. An outside agent has not been taught the language. Given six
  small Kosika tasks and nothing else, devin got 4 of 6: `words.length`
  for `words.length()`, and a `Point(...)` that Kosika does not have.
  - A guide, `kosika/app_assets/writing_kosika.md`, goes with any request
    about Kosika. A test checks that every example in it is real Kosika
    and every method it names exists.
  - Before anything is written, the engine checks each planned `.kos` file
    and runs the tests in it: `system "kosika"`, operation `check_files`,
    which the coder calls on every proposal. The same reading now covers
    what an outside agent proposes in Python, which it did not before.
  - The coder's own check, in Kosika, reads Kosika files too: what a file
    had must stay, and a task that asks for a test must add one.
  - After a change to Kosika only, that file's own tests are run with
    `kosika test`, not pytest.
- Measured: the whole coder with omp on the six Kosika tasks, scored
  strictly. Five were done in 23 to 58 seconds each. The sixth did not
  finish: the run stood for an hour and ended on its deadline, most likely
  the laptop sleeping. Run again alone it was done in 31 seconds.
- 19 tests in the coder's program; 32 in `tests/test_outside.py`.
- Not claimed: the six Python tasks have not been run again since the
  request was fixed, and never with a reviewer; devin was spent. Whether
  the guide helps was not measured, for the same reason: the only number
  without it is devin's 4 of 6 and the only number with it is omp's. Six
  tasks of one small kind are not a measure of writing real Kosika
  programs with workflows, agents and tools; the guide does not teach
  those yet.

### Kosika runs without Python: a native engine and a conformance suite

- The owner asked for Kosika to be "a strong and independent programming
  language, without the assistance of python". The first plan put to him
  was to write Kosika in Kosika: lexer, parser, then a compiler. He then
  shared the advice of two outside agents, kilocode and omp. Both said:
  a native runtime first, held to a conformance suite; self-hosting later,
  only if useful. They were right, and he was told so. Self-hosting
  reaches "no Python" at its last step; a native engine reaches it early.
- He asked for both Go and Rust. Agreed: Go first, because Go is on his
  machine and builds one program file; Rust after, against the finished
  suite, which is work an outside agent can be given with the suite as
  judge. Two engines passing one suite is also the proof that the suite,
  not an engine, says what Kosika means.
- `conformance/`: programs with what `test` must print for each. Five
  that pass (numbers, text, lists, choices, records and functions; 31
  tests) and one whose nine tests fail on purpose, so that what a failure
  says is held too. `python -m kosika.conformance write` lets the
  reference say what it prints; a person reads it before it becomes law.
- `native/`: a Go engine for the core of the language, which reaches
  nothing outside the program. Lexer, parser, runner and about 80 built-in
  methods, in some 3,100 lines. It builds to one 4 MB file.
- Measured. Its lexer gives the reference's tokens on 78 of 78 Kosika
  files, 67,316 tokens. It passes 31 of 31 conformance tests. On the
  failing program both engines print the same report, word for word.
  `tests/test_conformance.py` holds both engines to this, and skips the
  native half, saying so, where Go is not installed.
- Not claimed. Kosika is not independent of Python: the app, the agents,
  models, tools, workflows, traces and the checker are all still Python
  only. The native engine has no checker, and on its first day ran a
  program the reference rightly refused (a list of mixed kinds); the suite
  has no case that would catch that. Six programs are a beginning: a rule
  no case uses is held to nothing. Known differences not yet closed: the
  reference counts any Unicode digit as a digit and decodes `\N{name}` in
  a string; the native lexer does neither.
- A lexer written in Kosika itself was drafted the same day, before the
  plan changed. It needs two primitives the language lacks, a character's
  number and back. It is kept as a future conformance program, not as the
  road.
- Next: refusal cases in the suite and a checker in the native engine;
  then workflows and tools; then models.

### Kosi follows the talk, and may put a question to a stronger mind

- The owner said Kosi did not feel smart, and showed three answers: a
  thin one, then two that spoke of his stretch reminder when he had asked
  about quantum projects. Reading how each was made showed the fault was
  mostly Kosi's, not the model's. Every message went to the model alone,
  with no word of the talk before it; the reminders were in every prompt,
  so they were all the model could seize on; and answers were held to
  three sentences and 160 tokens.
- Now Kosi keeps the last eight lines said, in a memory of its own, and
  gives them to the model; the model is told what pointing words mean,
  told not to bring up reminders unasked, and allowed up to six sentences.
  The same three messages, the same model, a reminder set as his was:
  each was answered properly. A plain "thanks" is answered by code.
- He then agreed, asked plainly, that open questions may go to omp. A new
  outside operation, `answer`, puts a question to an outside agent in an
  empty folder: no file is shown, so no folder need be granted. Kosi asks
  omp by name for questions of five words or more, sending the question
  and the earlier questions and answers only. His name, reminders and
  remembered facts are kept out of it, and a test holds that. omp is told
  to say LOCAL when a question turns on something only Kosi knows; then,
  or when omp cannot be asked, Kosi's own model answers. An answer from
  omp ends "(answered by omp)".
- Tried on a scratch copy: three open questions answered by omp in 14 to
  22 seconds each, against 31 to 41 for the local model, and with plainly
  more substance. "What is the number of my locker" came back as LOCAL
  and was answered here, which cost that question two waits, 44 seconds.
- The first wording of the LOCAL rule was too wide and sent every
  question back, the quantum one among them; nothing showed it but the
  missing "(answered by omp)". It was narrowed and tried on four
  questions.
- Not claimed: that "five words" is the right line; it is a guess. That
  the answers are right; nothing checks them. A question the person
  words with a private fact in it carries that fact to omp, as any
  question they choose to ask does.
- A leak found while writing this down, and closed: the talk a later
  question took along held every earlier answer, among them one Kosi's
  own model had given from a remembered fact. Now only turns omp itself
  answered go with a later question.
- The owner asked whether Kosika can make a model smarter. Said to him:
  not its numbers, which only training changes; but several models
  arranged in Kosika can score above the one inside, as the researcher
  does. Agreed as next: one shared "mind" in Kosika that agents ask as
  they would a model, measured against the bare model on fixed questions,
  using the local model only.

### The coder in JavaScript and TypeScript, and a Skills section in the app

- The owner's order for the coder was Kosika first, then JavaScript and
  TypeScript. Node is on his machine and runs TypeScript itself, and its
  own test runner needs no library, so nothing was installed.
- Written in Kosika, in the coder, not in the engine: what a `.js` or
  `.ts` file defines is read from its lines (functions, classes,
  constants, types, interfaces, and each `test("...")`). A plan that drops
  what a file had is sent back; so is a test put in a source file, a
  task that asks for a test and writes no test file, an import of a name
  the other planned file does not export, and an import that calls a file
  by a name Node will not find (`./calc` for `calc.ts`).
- After a change to JavaScript or TypeScript only, `node --test` runs the
  test files that were written, or all of them if none was. Node calls
  "no tests found" a success; the coder says "No tests were found to run".
  The word "test" alone now looks at the folder to choose pytest or Node.
- Skills are picked by language: `script.md` and `script-tests.md` for
  JavaScript and TypeScript, and the two Python skills now only for
  Python. Before, a task in any language picked the Python advice.
- Measured strictly, with a test file added afterwards that the coder
  never saw. With omp: 6 of 6, about 40 seconds each. The local model
  alone: 1 of 6 in 50 minutes. Its misses are listed in CODER.md.
- The same day the coder was run alone on the portfolio task, at the
  owner's wish, for comparison with omp's 3.5 minutes: in 22 minutes it
  wrote no file, said it had, and Kosika reported that none was written.
- Studio has a Skills section, asked for by the owner: every skill by the
  folder whose agents it serves, with its words, its file patterns and
  how many rules; a box that shows which skills a request would pick and
  why, with no model asked; and an editor to change a skill or write a
  new one. Save is the person's own yes. A note Kosika cannot read is
  shown with the reason and is never saved, because one unreadable note
  stops every skill beside that agent.
- Not claimed: a skill cannot be deleted or renamed from the app. Found
  and not yet put right: when the local model writes nothing in a
  JavaScript project, the coder still runs pytest; a test file that
  defines its own copy of the function it tests passes the plan check;
  `node --check` does not catch a TypeScript syntax error, so only the
  test run does. The Python six have not been re-measured since skills
  are picked by language.

### Skills reach the outside agent, and a skill for motion

- The owner proposed a skill that explains motion on a web page, scroll
  and cursor effects, for the coder to use on a portfolio. A skill until
  now reached only the coder's own local model; the outside agents, which
  do the writing, never saw one.
- Now the skills that fit a task are added to what the outside agent is
  asked, and its work is held to their checks afterwards: `check_files`
  takes what was asked for, picks the skills, and reports a file that
  breaks a rule. Tested end to end with a stand-in: a stylesheet that
  ignored a skill's rule was sent back with the rule quoted, before any
  person was asked.
- Two draft skills beside the coder, `motion.md` for stylesheets and
  `motion-script.md` for scripts: what moves on scroll, what follows the
  cursor, what keeps it smooth, what keeps it kind. Their checks: both
  files must speak of `prefers-reduced-motion`, the stylesheet of
  `transition`, the script of `IntersectionObserver`. They are picked by
  words of motion, not by "website", so a task that asks for no motion is
  not held to them. The words are a draft for the owner to change.
- Tried in a scratch folder. First the coder was asked for a portfolio
  site with nothing there: omp wrote three files, about 400 lines, shown
  as one list, in 3.5 minutes. Then, with the motion skills picked, it was
  asked to add scroll and cursor motion: 6 minutes. Looked at afterwards:
  every section and its text still there, no error on load, 16 elements
  set to rise into view, a reading bar and a cursor glow, motion switched
  off for people who ask for less, cursor effects only for a real pointer.
- A fault found by the first of those runs: when an outside agent's
  scratch copy could not be deleted at once, the run failed and its work
  was lost. Deleting it is now allowed to fail.
- Said plainly to the owner, who had taken the page for Kosika's own work:
  omp wrote it. The coder ran the job: handed it over in a copy, took back
  the files, checked them, asked before writing.
- Not claimed: whether the motion is tasteful, which a picture cannot
  show and only the owner can judge. Kosika checks nothing about HTML, CSS
  or JavaScript beyond a skill's `contains` rules; a page that loads is
  not a page that is right. The phone layout looked cut off in a capture
  and was not settled.

### Other local models, measured

- The owner asked which models should be installed, and about DeepSeek
  and other open models. This laptop has 4 cores, 15 GB and no separate
  graphics card, so only models of about 7B and under are practical.
  Three were downloaded with his yes and put on the scoreboards.
- Researcher, twelve evals twice each: qwen2.5:7b 24 of 24; gemma3:4b 18
  of 24, and it answered 3 of the 4 questions whose right answer is "I
  will not guess"; deepseek-r1:7b 8 of 24, most often failing to give a
  quote that is in the lines.
- Coder, six tasks, local only: qwen2.5-coder:7b 0 of 6. It never asked
  for a tool, so no plan was made; the coder said "No file was written"
  each time, which is the honest report.
- Kept as before: qwen2.5:7b, qwen2.5:3b, nomic-embed-text. Removing the
  three new ones is the owner's to say.
- Ollama is now asked to keep a model loaded for 30 minutes after an
  answer (`keep_alive`), which saves the reload on the next question.
- Not claimed: the times of each model were not kept. Two runs of twelve
  questions is a small sample. The models were tried only as they come,
  with no prompt written for them.

## Standard coding-agent procedure

1. Inspect `git status`, the relevant source files, tests, and this plan.
2. State the smallest proposed slice and any assumptions.
3. Implement without discarding unrelated changes.
4. Add focused tests, including failure branches where relevant.
5. Run focused tests.
6. Run the complete quality suite.
7. Review the diff and generated artifacts.
8. Report changed files, quality results, unresolved limitations, and the next slice.

## Definition of done for a slice

A slice is complete only when its behavior is covered by focused tests, existing behavior remains green, deterministic output is verified, security/authority boundaries remain explicit, and the result is documented well enough for another coding agent to continue without reconstructing context.
