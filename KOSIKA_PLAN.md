# Kosika implementation plan

This document is the shared plan for human developers and coding agents working in the actual Kosika checkout:

`C:\Users\Aryan\Aryan_Private\projects\kosika`

## Mission

Kosika is a Python-first, AI-native programming language and runtime for typed programs, models, agents, MCP/tools, workflows, networks, memory, deterministic explanations, visual designs, and replayable execution.

The long-term principle is:

> Unlimited expression, bounded authority.

Kosika should become more expressive than a collection of Python/JavaScript scripts while keeping execution, capabilities, model use, external effects, resources, and approvals explicit and fail-closed.

## Non-negotiable engineering rules

- Preserve unrelated uncommitted work; never reset or clean the checkout.
- Treat this checkout as authoritative. Do not confuse it with prototype folders or the separate Synapse/ai_agent project.
- Do not modify protected `agent-lab` content.
- Do not commit unless explicitly requested.
- Keep the implementation dependency-light and Python-first.
- The checker, schema validation, approval system, and replay validator remain authoritative.
- Model output is a proposal, never execution permission.
- Unknown tools, invalid manifests, undeclared capabilities, invalid schemas, and unsafe imports fail closed.
- Mindmaps, explanations, design graphs, and replay summaries must be deterministic from checked source, manifests, and traces.
- Add focused tests for every new branch, then run the full suite, Ruff, and MyPy.

## Current baseline

The repository currently contains:

- Parser, typed AST, checker, records, tools, agents, models, and structured values.
- Model, agent, tool, and nested workflow calls.
- Bounded local module composition through `load_program(...)`.
- MCP stdio transport, pinned manifests, schema checks, and replayable traces.
- Approval-gated execution, model budgets, provider telemetry, and failure handling.
- Deterministic mindmaps, focused explanations, and a bounded assistant.
- Extension-manifest validation and extension-aware mindmaps.
- Declarative network manifests and network indexing.
- Deterministic workflow/network/code-mindmap vector designs rendered as SVG, Mermaid, JSON, and text.
- A separate Synapse/ai_agent prototype integration; do not merge its source boundary into Kosika casually.

The last verified baseline before each change should be recorded by running:

```powershell
python -m pytest -q
ruff check kosika tests
python -m mypy kosika
```

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

### Phase 5 — Mathematical and expressive programming

- Add functions, nested functions, closures, recursion, higher-order functions, conditions, bounded loops, generators, async behavior, vectors, matrices, sequences, and signals.
- Define resource limits and deterministic semantics before adding unrestricted execution.
- Connect mathematical values to safe animation and visualization primitives.

### Phase 6 — Migration, optimization, and ecosystem

- Add grounded translation and modernization from Python/JavaScript into Kosika.
- Preserve unsupported behavior behind typed adapters and mark it in mindmaps and migration reports.
- Add packages, extensions, language tooling, editor support, benchmarks, and deployment adapters.
- Claim improvements only when measured for correctness, latency, memory, token use, source size, network use, or cost.

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
- True mid-run publication from inside `runtime.py` remains out of scope: the runtime collects events internally and hands them to the caller at completion, and that file is under active concurrent revision by another session (7 unrelated test failures persist there). This slice delivers live visibility end to end within the workbench boundary.

## Next active slice

The next bounded target is deep publication and runtime hardening (pick up only after the concurrent runtime/model edits in the working tree settle and their 7 test failures are resolved by that session):

1. Publish run events mid-execution from the runtime via an incremental event callback, retaining offline JSONL replay as the source of truth.
2. Wire the workbench timeline action to the already-published stream so the browser need not repost events.
3. Keep remote access disabled by default and require explicit approval for execute links or mutations.
4. Evaluate WebSocket transport only if bidirectional control is actually required.

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
