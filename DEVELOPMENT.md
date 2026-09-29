# Kosika development guide

Kosika (formerly Synapse Language) is a Python-first experimental language/runtime
for typed AI workflows. The additions below are working first slices, not a claim
of production readiness. Run commands from the repository root.

## 1. Modules and workflow composition

```kosika
import "types.syn"
import "research.syn"

workflow main(question: Question) -> Answer {
  step investigate {
    let answer: Answer = call research(question: question)
    return answer
  }
}
```

Modules explicitly publish their local declarations with `export [Question, Answer]`.
Only exports of directly imported files are visible. Dependencies are loaded once.
Names must not conflict across modules; namespaces and aliases are not implemented.
Imports stay within the host-granted module root (the entry file's directory by
default), reject cycles and final-component symlinks, and are bounded to 64 files,
16 import levels, and 256 KiB per file. These checks are not an OS sandbox.
Every loaded file's content hash contributes to the program/approval identity.
Imports grant declarations, not providers, tools, network access, or credentials.

`parse(text)` remains the in-memory parser and refuses unresolved imports when
checked. Use `load_program(path, root=optional_project_root)` for file programs.
CLI check/run/agent now load modules. Import parse errors include their filename;
parser errors retain line/column. Static errors still mostly identify declarations
and steps rather than exact source ranges.

Workflow calls match named record parameters and output types. The checker rejects
unknown calls, recursion, type mismatches, and missing approval declarations through
the call graph. Runtime preflight checks model grants in nested calls before work.
Nested execution and replay retain child traces. No generic loops were added.

Try the three-file example:

```text
python -m kosika.cli check examples/composed/main.syn
python -m kosika.cli run examples/composed/main.syn --input examples/composed/inputs.json --model-fixture examples/composed/responses.json --trace composed.jsonl
python -m kosika.cli replay composed.jsonl
```

This uses scripted responses, not live model inference.

## 2. Errors, cancellation, retries, and model controls

`ExecutionError` exposes `code`, `retryable`, and `to_dict()`. The host can distinguish
budget exhaustion, cancellation, deadlines, provider failures, approval pauses,
plan mismatch, and uncertain outcomes. Existing parser/checker/schema exceptions
remain compatible. Language-level `try`/`catch` and fallback syntax are not added.

```python
from threading import Event
from kosika.controls import ControlledProvider, ModelLimits
from kosika.model import OpenAICompatibleProvider

cancel = Event()
provider = ControlledProvider(
    OpenAICompatibleProvider("http://127.0.0.1:11434/v1", "YOUR_MODEL", timeout=20),
    ModelLimits(max_requests=8, max_output_tokens=256, max_tokens=4000),
    cancel=cancel,
)
# Bind explicitly: run(..., model_bindings={"reader": provider})
```

Limits and telemetry belong to each provider wrapper. Requests count attempts,
including failures. Token accounting requires valid usage when a token budget is
set and stops further requests when usage is unavailable. Usage arrives after the
request: this is not a hard guarantee against exceeding a remote bill. Output caps
are sent as `max_tokens`; the server must support/enforce that parameter.
Provider calls are serialized by a wrapper to keep its counters coherent.

Telemetry contains latency, reported token usage, errors, and optional estimated
cost using host-supplied per-token rates. Missing cost is unknown, not zero.
Wrapper counters are in memory; they are not a durable quota across processes.

Cancellation and deadline checks occur between requests and after responses;
in-flight HTTP relies on its transport timeout. Cancelling a coroutine does not kill
an underlying worker thread or undo a request already received by a server.

Retries default to one attempt. Only an explicit `TransientFailure` from a trusted
adapter is eligible for up to three configured attempts. Malformed output,
permission errors, and generic provider errors are not automatically retried.
`ReadOnlyRetry` similarly refuses tools unless their access is exactly `observe`.
No mutation is automatically retried.

CLI run supports `--max-requests`, `--max-tokens`, `--max-output-tokens`, and
`--telemetry PATH`. Limits apply per bound model. Live use still requires
`--model-binding`, `--model`, and `--model-url`; credentials stay in the environment.

## 3. Durable runs and approval pauses

```text
python -m kosika.cli run examples/composed/main.syn --input examples/composed/inputs.json --model-fixture examples/composed/responses.json --trace composed.jsonl --journal composed.sqlite --binding-id scripted-reader-v1
```

Repeat that command with the same program, inputs, and binding identity to rebuild
the trace from completed checkpoints. Model/tool calls for completed steps are not
repeated. Nested workflows checkpoint their leaf steps. A journal represents one
run; use a new journal for fresh execution.

The journal binds the source AST/hashes, selected workflow, inputs, MCP manifest,
commands, and host `binding_id`. The host MUST change `binding_id` when provider,
tool implementation, retry policy, or other relevant binding configuration changes.
Python API: `run(..., session=ExecutionSession(path, binding_id="..."))`.

An `execute` step requires `require approval` and a durable session. Execution
pauses before the effect with an `ApprovalPending` request. CLI exit code is 3;
the request contains plan hash, exact action hash, tool, arguments, and step path.
After a human reviews it, supply a JSON `--approval-record` containing:

```json
{"plan_hash":"REVIEWED_PLAN_HASH","action_hash":"REVIEWED_ACTION_HASH","approved_by":"reviewer","reason":"Reviewed the exact requested change"}
```

The Python API accepts a list of existing `ApprovalRecord` values in the session.
Approval actor is a label, not authenticated identity. An action claim is committed
before invocation. Completed results are checkpointed only after validation. If a
process stops or an exception occurs after a step starts, resume rejects its
uncertain outcome. Reconcile it explicitly; do not delete the journal to bypass
this guard. There is no automatic rollback or exactly-once external-effect claim.

A separate SQLite lock prevents two processes from owning one journal, and the
session rejects cross-thread reuse. Keep the journal, `.lock.sqlite`, and
`.approvals.sqlite` files together. Journals/traces are private trusted local state;
they are not cryptographically authenticated against local rewriting.
`ExecutionSession.inspect()` reads existing checkpoint status without executing work.

`ask` agent steps remain read-only. Durable mutations are explicit tool steps.
Failed runs retain journal state and now emit terminal diagnostic trace events;
they are not replayable as successful results. Approval pauses preserve completed work; interrupted agent loops are not
resumed midway through a model/tool conversation.

## 4. Parallel read-only workflows

```python
from kosika.dag import DagNode, run_dag, replay_dag

nodes = [
    DagNode("first", "research", inputs={"question": {"text": "Summarize"}}),
    # Dependency record types must match the receiving workflow's parameters.
]
results = await run_dag(program, nodes, "new-run-directory", concurrency=4,
                        models={"first": {"reader": provider}})
cached = replay_dag("new-run-directory")
```

Dependencies map input parameter names to predecessor node names. The scheduler
validates types, cycles, capabilities and required bindings before creating output.
It executes independent nodes concurrently, waits for each dependency layer, and
does not start downstream work after a failed layer. Traces are separate per node;
the completed DAG manifest records declared dependencies and results. Offline DAG
replay checks trace/result/input consistency and does not execute models or tools.

Limits: 64 nodes, concurrency 1..16. Only `observe` tools may run in a DAG; mutations
are refused, including inside nested workflows or agents. Supply independent model
fixtures/adapters per node, or thread-safe shared wrappers. Node finish order is
nondeterministic; replay reuses recorded outcomes, not the original wall-clock order.
This is a host Python API, not new parallel language syntax or a remote scheduler.

## 5. Small host adapter library

`kosika.stdlib` supplies:

- `JsonFiles({key: exact_path})`: bounded JSON reads by granted key, no model paths.
- `SQLiteReader(path, {name: query})`: named host SQL, bound parameters, read-only
  connection and authorizer, row and execution-work limits. Arbitrary query text,
  write operations and SQL functions are not accepted from the agent.
- `HTTPReader({name: exact_url})`: GET-only JSON, HTTPS or explicit loopback HTTP,
  no redirects or inherited proxies, timeout and 1 MiB response bound.
- `ReadOnlyRetry(executor, attempts=...)`: explicit safe transient retries.

The existing `LocalDocuments` adapter provides bounded document snapshots/search.
Use small host callables to map declared tools to adapter methods and bind them
through `tool_bindings`. Runtime validates declared input/output schemas on both
sides. A declaration cannot instantiate an adapter or grant its resources.
These adapters are not filesystem/network sandboxes; deploy untrusted host code
inside a real sandbox. No package installer, cloud execution, or kosipack registry
has been introduced.

## 6. Developer tools and evaluations

```text
python -m kosika.devtools format examples/composed/main.syn --check
python -m kosika.devtools complete examples/composed/main.syn --prefix re
python -m kosika.devtools inspect composed.jsonl
python -m kosika.devtools eval examples/composed/main.syn examples/composed/evaluations.json --output new-evaluation-directory
```

The formatter normalizes indentation while preserving comments and line structure;
it parses before and after and rejects semantic changes. Without `--write` it prints
to stdout. Completion lists declarations/keywords for a valid program. The trace
inspector validates replay first and shows nested steps without printing prompts or
outputs. These are foundations, not a full language server or IDE debugger.

`editors/vscode` contains a local VS Code extension skeleton with `.syn` highlighting
and snippets. It is not installed or published automatically. Open that folder in
VS Code and use an Extension Development Host to try it. Dynamic editor diagnostics
and semantic autocomplete remain future LSP work.

Evaluation fixtures declare named inputs, exact expected records and/or required
text in output fields. Assertions are data, never Python/code expressions. Each
case executes afresh with a new provider supplied by the host, writes its own trace,
and contributes to a JSON report. CLI fixtures use scripted responses and return
0 on pass, 1 on assertion failure, 2 on invalid setup. Python `evaluate(...)` also
accepts live-provider factories. Fresh evaluation and exact replay are separate.

## 7. Static code mindmaps and assistant context

### Language adapter registry

`kosika.language_adapters.REGISTRY` is the single dispatch table for read-only
source analysis. Each `LanguageAdapter` declares its language name, file suffixes,
per-file source and symbol limits, source analyzer, and file analyzer. Callers use
`adapter_for_suffix()` or `adapter_for_language()` instead of branching on
language names. The built-in registry currently provides:

- Python: `.py`, `.pyi`
- JavaScript: `.js`, `.mjs`, `.cjs`, `.jsx`
- TypeScript: `.ts`, `.mts`, `.cts`, `.tsx`

Adapters never import or execute analyzed code. To add a language, implement its
bounded analyzer, create a `LanguageAdapter`, and add it to `REGISTRY`; project
analysis, source maps, CLI analysis, and assistant source grounding then share the
same dispatch contract. Unsupported source types are reported explicitly by the
CLI and remain inventory-only in project analysis.

### Read-only source analysis

`kosika.python_adapter.analyze_python_file(path)` and
`analyze_python_source(source)` use Python's standard-library AST parser. The
JavaScript and TypeScript adapter uses a bounded syntax scanner. All adapters
return deterministic `kosika.source-analysis/v1` reports containing source
identity, declarations, imports, source spans, direct syntactic calls, and
clearly-labeled effect observations.

```text
python -m kosika.cli analyze path/to/module.py --format text
python -m kosika.cli analyze path/to/module.py --format json
python -m kosika.cli analyze path/to/module.ts --format json
python -m kosika.cli assistant examples/test_host.syn --task "Explain this helper" --source-file path/to/module.py
```

This is structural indexing, not complete semantic analysis: it does not resolve
calls, infer purpose, model dynamic dispatch/reflection/generated code, or prove
safety or purity. Missing effect observations do not prove that code has no
effects. Source limits are enforced by the selected adapter; oversized, invalid
UTF-8, or invalid syntax input fails closed. Assistant grounding is opt-in
through repeatable `--source-file` arguments; each report is bounded context and
source-derived text is explicitly treated as untrusted data.

### Bounded project map

`kosika.project_analysis.analyze_python_project(root)` indexes up to 40 files and
4 MiB of source through the registry without importing or executing code. It
includes syntax-backed file symbols and imports, candidate local Python import
edges, test-file paths, counts of known but unsupported-language files, and
bounded root-level README/build/dependency metadata as explicitly unverified
context evidence. Hidden directories, common build/virtualenv/vendor directories,
symlinks, and non-regular files are excluded. The report is limited to 180 KiB
and fails safely instead of silently truncating semantic data.

```text
python -m kosika.cli project ./my-project --output project-map.json
python -m kosika.cli assistant examples/test_host.syn --task "What is this codebase for?" --project ./my-project
```

Repeat `--project` to compare multiple project roots; each report is included in
bounded assistant context and returned in `project_analyses`. Local import edges
currently link Python files only; JavaScript/TypeScript imports are reported per
file without cross-file linking. Unresolved import candidates are not called
external dependencies, and duplicate module names are reported as ambiguous
rather than linked. README/config text is only evidence, not verified purpose or
runtime behavior. Tests are inventoried by path and are never run by analysis.

`kosika.mindmap.build_mindmap(program)` checks the program and returns a deterministic JSON-compatible
contract. Nodes cover module boundaries, records and fields, tools and access levels, models, agents,
workflows, steps, bindings, and call relationships. Edges are sorted by endpoint and relation; node
IDs are stable for the same declaration names. Imported programs retain module paths, SHA-256 content
hashes, declaration ownership, and direct import edges in `Program.module_hashes`,
`Program.module_declarations`, and `Program.module_imports`.

```text
python -m kosika.cli mindmap examples/composed/main.syn --format json --output code-map.json
python -m kosika.cli mindmap examples/composed/main.syn --format mermaid --output code-map.mmd
```

The map is created after `load_program` and `check`; it never executes a workflow, opens a provider,
or interprets model output. A forbidden capability that is named but not declared as a tool appears as
a `capability` node with `status: forbidden`. The map has a 512-node bound and fails closed on invalid
relationship endpoints.

`kosika.explain.build_explanation(...)` provides a focused deterministic view over that map. Pass
`workflows=(...)` and/or `records=(...)` to select roots; the result walks connected semantic edges,
then retains module containers and import boundaries. Pass diagnostic strings to keep compiler or host
observations beside the static structure. `trace_path=` reads a JSONL trace, runs the existing strict
`replay_events` validator, and returns event counts, nested step paths, the recorded result, and a
program-hash comparison. It never re-executes the workflow. CLI usage is:

```text
python -m kosika.cli explain examples/composed/main.syn \
  --workflow main --record Answer \
  --diagnostic "Review the model boundary" \
  --trace composed.jsonl --format json
```

JSON contains the selected mindmap and trace summary; text adds a compact explanation; Mermaid emits
the selected graph. `--format outsider` renders the same selected graph as plain language: workflow
story, step actions, agent permissions, human diagnostics, and offline trace counts, with type names
hidden. It adds no edges and infers no purpose; business intent still comes from human diagnostics.
Unknown selections, malformed traces, reordered events, outcome mismatches, and
invalid endpoints fail closed. A valid trace from another loaded program is not silently treated as
matching: JSON exposes `program_hash_match: false` and text reports a warning.

`kosika.assistant.KosikaAssistant` receives only the checked mindmap and host-supplied diagnostic text.
It supports a deterministic static mode, `ScriptedModelProvider` fixture mode, and an optional
OpenAI-compatible local provider. Assistant requests always have an empty tool list. Tool calls,
truncated responses, empty output, oversized context, and invalid tasks are rejected; assistant text
never authorizes a capability or mutates a resource. Use `kosika.cli assistant --json` when a caller
needs the answer, diagnostics, and exact static mindmap in one structured result.

## Validation and next limits

### Pure expression core

Top-level `fn` declarations support typed, authority-free computation with a
sequence of immutable typed local bindings and a final return expression. Expressions include function composition, numeric
`+`, `-`, `*`, and `/`, numeric comparisons, strict same-type equality,
Boolean `and`, `or`, and `not`, unary numeric negation, grouping, and typed
`if condition {value} else {value}` selection. Boolean operators and
conditionals short-circuit, so unselected expressions are not evaluated.

The checker rejects operand mismatches, non-Boolean conditions, differing branch
types, unannotated recursive function graphs, private function references, and call depth
beyond 32. Functions cannot invoke models, agents, tools, workflows, host code,
or external services. Nested declarations, closures, scalar matching and bounded
recursion are described below. Mutation and general loops remain unsupported.

Local declarations require `let name: Type = expression`. Each initializer is
checked before its name enters the scope, so forward references and self-reference
fail. Duplicate locals and parameter shadowing also fail. Runtime validates every
initializer against its declared schema in a fresh per-call environment.
Initializer calls are included in module privacy checks and deterministic call
graphs. Trace version 5 remains unchanged: calls made by initializers appear in
the function body's ordered events; local values themselves are not separately
recorded. Offline replay validates recorded outcomes without evaluating locals.

### Language-core stabilization

Syntax matching distinguishes quoted text from keywords and punctuation. Module
visibility checks traverse function calls inside workflow arguments, including
conditional branches. The 32-level function and workflow call-graph limits are
independent of declaration order. Execution and replay count function frames
separately from workflow nesting; arguments execute before the callee frame.

Replay validates recorded event structure, schemas, and outcome consistency. It
does not re-evaluate function bodies, authenticate the trace, or prove that its
recorded results were computed by the original source. Consistent edits to results
and schemas are not necessarily detectable. Source hashes are identification
metadata, not cryptographic signatures of execution.

### Nested pure functions and lexical capture

Functions may declare nested `fn` helpers after their `let` bindings and before
their final `return`. Helpers see enclosing parameters and locals, sibling
helpers, and module-visible top-level functions. A helper's parameters and locals
may shadow enclosing values without changing what sibling helpers capture.
Helpers are not visible to preceding local initializers or outside their enclosing
function. Duplicate sibling declarations and unannotated recursive call graphs are rejected.

```syn
fn calculate(base: Number) -> Number {
  let adjustment: Number = 2
  fn adjusted() -> Number { return base + adjustment }
  fn scale(factor: Number) -> Number { return adjusted() * factor }
  return scale(factor: 3)
}
```

The parser lowers helpers into private qualified names such as `calculate/scale`
with explicit capture arguments. Source identifiers cannot spell these names.
Existing checking, schemas, call-depth limits, graph views, execution and version-5
replay then operate on the lowered program. Captures conservatively include all
enclosing values, including shadowed bindings; these values appear as internal
`$capture:` arguments in traces. Source nesting is bounded to 32 declarations.
Host callers constructing nested ASTs must call `lower_nested_functions` before
checking or running them. Existing flat ASTs need no extra step.

Run `python -m kosika.cli run examples/nested_functions.syn` for a result of
`{"value": 36}`. Function bodies remain pure and immutable.

### Typed function values and escaping closures

Use `fn(value: Number) -> Number` as a parameter, local, return or workflow-binding
type. Argument names are part of the signature; there is no implicit conversion
between signatures. `ref name` creates a reference to a visible pure function.
For nested functions, it snapshots the lexical captures so the reference can be
returned and called after its creating invocation has finished.

```syn
fn make_adder(base: Number) -> fn(value: Number) -> Number {
  fn add(value: Number) -> Number { return base + value }
  return ref add
}
```

Invoke a function-valued binding using the same named call syntax as declarations.
`examples/closures.syn` passes an escaped adder to a higher-order function and
returns `{"value": 15}`. Function-valued parameters and locals shadow declarations
for calls; explicit `ref name` always refers to a declaration, not a variable.

Closures contain a qualified function name, canonical signature and copied captures;
they never contain Python callables or host capabilities. Captured values are
visible in version-5 traces. Replay checks their recorded signature/shape but does
not authenticate closure targets or reconstruct execution. Runtime invocation checks
the actual target signature and capture names. Static dependencies conservatively
include function references; indirect invocations retain the runtime depth limit.
Graph views show static references and workflow binding producers, not a complete
dynamic call graph.

This slice deliberately excludes function fields in external records, arrays of
functions, anonymous lambdas, mutable captures, recursive types and effectful
functions. Workflow inputs and final outputs remain records, so host/model/tool
payloads cannot supply executable function values.

### Bounded recursion and scalar pattern matching

Pure functions opt into recursion with `recurse N` after their return type, where
N is an integer from 1 to 32. N counts simultaneous invocations of that declaration,
including the first invocation, regardless of which closure captures are used.
Every declaration participating in a statically visible cycle must opt in; this
includes mutual recursion. Workflow recursion remains forbidden.

```syn
fn factorial(n: Number) -> Number recurse 16 {
  return match n {
    case 0 { 1 }
    case _ { n * factorial(n: n - 1) }
  }
}
```

The runtime enforces both the declared per-function bound and the existing maximum
of 32 active function frames across all declarations. A default 10,000-call ceiling
bounds branching pure computation. It is now configurable and shared across the
root run and all nested workflow invocations (see execution controls below).
These are execution bounds, not a termination proof. Cached steps do not consume
function calls they do not execute. Limit violations raise RuntimeError or its
ExecutionError subclass.

`match subject { case literal { expression } case _ { expression } }` evaluates
its subject once and only evaluates the selected arm. Supported patterns are Text,
Number (including negative numbers), Bool and Null literals plus `_`. Literal types
must match the subject. Bool is exhaustive with true/false, Null with null; Number
and Text require a final wildcard. Duplicates, unreachable arms, non-exhaustive
matches and mismatched result types fail checking. Record/array destructuring,
pattern-bound variables, guards and tagged unions are not implemented.

Run `examples/recursion_match.syn` to obtain
`{"factorial": 120, "parity": "odd", "classification": "zero"}`. Successful recursive
runs use existing version-5 call traces and replay validation. Replay enforces its
global nesting limit but does not independently prove source-level recursion bounds
or the execution call budget.

### Whole-run controls and failure telemetry

`run(..., limits=ExecutionLimits(...), cancel=threading.Event())` supports shared
admission counters for function calls, workflow steps, model-provider calls and
tool calls. Defaults are 10,000 / 10,000 / 64 / 256, respectively. Each integer
limit must be between 1 and 1,000,000. `deadline_seconds` optionally supplies a
finite positive elapsed-time limit. A fresh root run gets fresh counters; nested
workflows share them. DAG nodes remain separate root runs. Existing per-provider
token/request limits still apply independently.

```python
import threading
from kosika.execution import ExecutionLimits
from kosika.runtime import run

cancel = threading.Event()  # another thread may call cancel.set()
result = run(program, "main", "trace.jsonl", cancel=cancel,
             limits=ExecutionLimits(max_function_calls=500,
                                    max_steps=20, deadline_seconds=5))
```

CLI equivalents: `--max-function-calls`, `--max-workflow-steps`,
`--max-model-calls`, `--max-tool-calls`, and `--deadline-seconds`. Ctrl+C during
execution is reported as cancellation with exit code 130. Budget/deadline failures
return 2; approval pauses retain exit code 3. Example:

```text
python -m kosika.cli run examples/recursion_match.syn --max-function-calls 100 --deadline-seconds 5 --trace bounded.jsonl
```

For an offline comparison of success, budget exhaustion and cancellation, run
`python -m examples.execution_controls`. It prints the result or terminal status
and counters for each case without calling a model or tool.

Cancellation/deadline checkpoints occur in expression evaluation, at step and
provider boundaries, and before tool admission. They do not kill an in-flight
host/transport call or undo its effects. A returned tool outcome is validated and
journaled before the next cancellation check, so durable resume can reuse it.
Tool admission is reserved before approval/session start; a paused action may
therefore consume an admission without dispatching a tool. Cached steps consume
step admissions, but not skipped tool/model/function calls. A model-call admission
counts a provider invocation; retries inside an adapter are governed by that
adapter's controls and transport timeouts, not individually by this counter.

Successful terminal events include `execution` counters, limits and elapsed time.
Failures retain available completed events and end with `run_failed`,
`run_cancelled` or `run_paused`; child failure events retain their nested trace.
Terminal telemetry includes the safe error code/type, workflow/step scope and
available function stack, but omits exception messages. Existing trace inputs,
outputs and captures are still sensitive and are not redacted. Traces are replaced
atomically; inability to write a failure trace must not replace the original error.

This is diagnostic failure telemetry, not failed-run replay or a full interrupted
agent/function transcript. Successful replay deliberately rejects terminal failure
events. The optional normalized event/timeline export remains a successful-run
path; failure diagnostics are in the runtime `--trace` file. CLI setup errors before
`run()` begins do not create runtime traces. Hard preemption, full agent-loop resume,
global DAG budgets and configurable token/cost accounting across providers remain
outside this slice.

Tests cover modules, private-name access, recursion, nested replay, budgets,
approval pauses, one-use claims, uncertain outcomes, journal locking, actual
loopback HTTP transport, redirect denial, concurrent DAGs, and developer commands.
Existing behavior continues through the full suite and the 80% coverage gate.

The installed `gemma2:27b` was discovered at the local Ollama endpoint during this
development run. One 32-output-token inference attempt timed out after 20 seconds.
No live inference success is claimed. A deterministic loopback HTTP fixture passes.
Choose a runnable local model or an explicitly configured provider before relying
on live-model evaluations; no model download or cloud resource was created.
