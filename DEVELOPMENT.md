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
Failed runs retain journal state but do not yet emit complete replayable failure
traces. Approval pauses preserve completed work; interrupted agent loops are not
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

## Validation and next limits

Tests cover modules, private-name access, recursion, nested replay, budgets,
approval pauses, one-use claims, uncertain outcomes, journal locking, actual
loopback HTTP transport, redirect denial, concurrent DAGs, and developer commands.
Existing behavior continues through the full suite and the 80% coverage gate.

The installed `gemma2:27b` was discovered at the local Ollama endpoint during this
development run. One 32-output-token inference attempt timed out after 20 seconds.
No live inference success is claimed. A deterministic loopback HTTP fixture passes.
Choose a runnable local model or an explicitly configured provider before relying
on live-model evaluations; no model download or cloud resource was created.
