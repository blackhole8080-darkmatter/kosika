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
