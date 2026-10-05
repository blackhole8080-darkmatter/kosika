A dependency-light first vertical slice of the Kosika AI systems language. Synapse was the earlier working codename.

## Status: prototype, not a released language

Read this before evaluating anything below.

Kosika is an **early prototype**. It is published so the design can be
reviewed and argued with, not because it is ready to depend on.

- **What works today.** A real parser, type checker, and runtime; typed
  records, functions, bounded loops and recursion, modules, and workflows;
  declared models, bounded agents, tools, and capabilities; approval-gated
  execution; deterministic mindmaps; stable structured diagnostics across
  local module graphs; a versioned machine-readable development interface; an
  LSP/editor backend; installable `kosika` and `kosika-lsp` commands; a local
  `.kos` workbench whose fixture-backed first-use path is browser-exercised;
  offline validation/replay of recorded traces; and a pinned local
  cross-language component contract.
- **What remains incomplete.** A demonstrated live-model setup, published
  releases and remote delivery, swarm messaging and consensus, component
  sandboxing, expression-precise checker spans, and the full AI
  author/check/repair/run loop. Several features in the tree are declared,
  checked, rendered, or exposed as host experiments but are not integrated
  into supported end-to-end behavior.
- **[KOSIKA_VISION.md](KOSIKA_VISION.md) is a design target, not a
  description of this codebase.** It describes a much larger language —
  tensors, memory bubbles, personality policies, an extension ecosystem, and
  multi-agent consensus. Most of those targets remain incomplete. The vision is
  included to show intent and to argue about, not to claim delivery.
- **The version is `0.1.0` and the API is unstable.** Interfaces will change.

If you are evaluating Kosika for real use today, the honest answer is that it
is not there yet. If you are evaluating the *idea* — that an AI system's
authority, evidence, and replayability should be enforced by the language
rather than by convention — the rest of this file is the substance.

See [KOSIKA.md](KOSIKA.md) for the versioned `kosika-language/0.1.0`
grammar, typing, execution, capability, replay, and support-status contract.

See [KOSIKA_PLAN.md](KOSIKA_PLAN.md) for the reconciled capability matrix,
verification record, and prioritized K01–K12 backlog.

## Repository layout

| Path | What it holds |
| --- | --- |
| `kosika/` | The language: lexer, parser, checker, runtime, and tools |
| `kosika_lsp/`, `editors/` | Editor support (language server, VS Code extension) |
| `examples/` | Example `.kos` programs and their fixtures |
| `benchmarks/` | The write-from-spec benchmark and its results |
| `tests/` | The test suite |
| `design/`, `network/`, `ui/`, `hive/`, `desktop/` | Diagram output, network manifests, UI styles, agent notes, and the desktop app scaffold |
| `archive/` | Historical documents, not used by the code |
| `TUTORIAL.md`, `AGENT_GUIDE.md`, `KOSIKA.md`, `KOSIKA_PLAN.md` | Tutorial, guide for AI authors, language contract, and plan |
| `DEVELOPMENT.md`, `WORKBENCH.md`, `KOSIKA_VISION.md` | Developer guide, browser workbench, and long-term vision |

## Start here

New to Kosika? [TUTORIAL.md](TUTORIAL.md) goes from nothing to a running,
replayable model workflow in about ten minutes:

```text
python -m pip install .
kosika new hello
cd hello
kosika run main.kos --input input.json --model-fixture responses.json
kosika run main.kos --input input.json --model YOUR_LOCAL_MODEL
```

## Can a model write Kosika?

[AGENT_GUIDE.md](AGENT_GUIDE.md) is the language guide written for AI authors.
`spec-eval` measures whether a model can write whole programs from it: 20
plain-language specs in `benchmarks/spec`, each judged by Kosika itself (the
checker, required declarations, and an exact result from a fixture-backed run).

```text
kosika spec-eval                                   # stored reference programs
kosika spec-eval --solver model --base-url URL --model NAME
kosika spec-eval --solver command --solver-command gemini -p ""
```

`--solver command` drives any command-line model that reads a prompt on standard
input. A failed attempt is shown what Kosika reported and may try again
(`--attempts`). Candidates run only with the task's fixtures, in a temporary
folder. Measured results are recorded in [KOSIKA_PLAN.md](KOSIKA_PLAN.md).

## Thinking steps

Four model tasks are single workflow steps with built-in, typed answers:

```kosika
model assistant

workflow main(ticket: Ticket) -> Routing {
  label   = classify ticket.text into ["billing", "bug", "other"]
  invoice = extract Invoice from ticket.text
  brief   = summarize ticket.text
  refund  = decide "Is this refund within policy?" given ticket.text
  routing = route(label, refund, brief, invoice)
}
```

`classify` always returns one of the listed labels with a confidence; `extract`
fills a record you declared; `decide` returns yes or no, a confidence, and its
reason. An answer that breaks one of those guarantees fails the run. Each step
is an ordinary typed model call underneath, so it is budgeted, traced, and
replayable, and it still needs the model granted at run time with `--model` or
`--model-fixture`. See [KOSIKA.md](KOSIKA.md) for the exact rules.

## How Kosika compares with Python

`kosika compare-python` runs six tasks written three ways: Kosika, short
"plain" Python, and "checked" Python that also rejects wrongly shaped data.
All three return the same answers. Measured on these six tasks:

| | Kosika | Python, plain | Python, checked |
| --- | --- | --- | --- |
| Lines of code, all six tasks | 103 | 81 | 108, plus 35 shared helper lines |
| Lines, four data tasks | 69 | 38 | 47 |
| Lines, two model tasks | 34 | 43 | 61 |
| Bad-data cases refused, of 20 | 20 | 9 | 20 |

Kosika is longer than plain Python for ordinary data handling, and shorter
once a model is involved. Its advantage is not size: it refuses every bad case
without extra code, where plain Python silently accepted 11 of 20, such as a
label the model invented or `true` where a number belongs. The bad cases were
chosen by the author of the comparison, so read that column as "what Kosika
checks for free", not as an unbiased error rate. Details and limits are in
[KOSIKA_PLAN.md](KOSIKA_PLAN.md).

## Shell and file access, approved every time

A program can run a command or touch files only through a declared `system`
tool, and only when you grant it and approve each call:

```kosika
tool save_note {
  source system "files"
  operation: "write"
  access: execute
  input: FileWrite
  output: FileWritten
}
```

```text
kosika run main.kos --input input.json --allow-folder ./workspace --approve
```

`--allow-folder` grants one folder to the program's files tools; add
`--allow-shell` to grant its shell tools too. `--approve` stops before every
such call, shows the exact tool and arguments, and asks `Approve this one
action? [y/N]`. Anything but yes stops the run with nothing done for that call.
With no terminal to ask, the run pauses instead of proceeding. See
[KOSIKA.md](KOSIKA.md) for the limits that hold even after approval.

## Use Kosika from an AI assistant (MCP server)

`kosika-mcp` is a Model Context Protocol server. Connected to an assistant such
as Claude Code or the Gemini CLI, it lets the assistant read the language
guide, then write, check, run, replay, and map Kosika programs by itself.

| Tool | What it does |
| --- | --- |
| `kosika_guide` | Returns `AGENT_GUIDE.md` |
| `kosika_check` | Checks a program; returns codes, messages, and lines |
| `kosika_run` | Runs a workflow with the inputs and scripted answers supplied |
| `kosika_replay` | Validates a trace from `kosika_run` and returns its result |
| `kosika_map` | Describes what a checked program declares |

Connect it by adding a stdio server whose command is `kosika-mcp` (or
`python -m kosika.mcp_server`). For Claude Code:

```text
claude mcp add kosika -- kosika-mcp
```

The server has no power of its own. Programs arrive as text and are checked in
a temporary folder; no tool takes a file path, address, or command.
`kosika_run` never calls a real model or tool: answers come only from the
`model_answers` and `tool_answers` the assistant supplies, and a program that
needs one it was not given fails. Runs are bounded to 16 model calls, 16 tool
calls, and 10 seconds.

## Python SDK

`kosika.sdk` builds a Kosika program from Python instead of a `.kos` file:

```python
from kosika.sdk import App, ref

app = App()
question = app.record("Question", text="Text")
answer = app.record("Answer", text="Text")
assistant = app.model("assistant", input=question, output=answer)
main = app.workflow("main", output=answer, question=question)
main.use_model("respond", assistant, text=ref("question.text"))

program = app.build()              # parsed and checked like any .kos file
app.save("main.kos")               # the same program as source
app.run(inputs={...}, model_bindings={"assistant": provider})
```

It is not a second language. The builder renders ordinary `.kos` text and
`build()` sends it through the same parser and checker, so the result is the
same checked program, with the same limits, approvals, and replayable traces.
`App` also has `tool`, `agent`, `swarm`, and `source` (for hand-written `fn`
declarations); a workflow has `use_model`, `ask`, `execute`, `call`, and
`function` steps. The SDK grants nothing: `run` takes explicit bindings, as
`kosika.runtime.run` does. Run `python examples/sdk_hello.py` for a complete
offline example.

## Language and runtime expansion

Kosika includes local imports with explicit exports, typed workflow calls, pure
functions, closures, higher-order values, bounded loops and recursion, nested
replay, model budgets/telemetry, durable checkpoints and approval pauses, a
bounded read-only DAG API, host-granted JSON/HTTP/SQLite adapters, development
commands, an LSP/editor backend, and evaluation fixtures.

See [DEVELOPMENT.md](DEVELOPMENT.md) for runnable examples, APIs, security
boundaries, and current limitations. A working local model endpoint has been
exercised end to end, but model quality and hosted availability remain
environment-dependent and are not release guarantees. Parallel DAG execution is a
host API rather than language swarm execution: a `swarm` declaration is checked,
mapped, and required to state `require approval`. `run` does not accept one; it
runs only through `kosika swarm` under one approval (see
[Approved swarm runs](#approved-swarm-runs)). Editor/LSP integration is implemented
but not equivalent to a packaged, production-tested IDE experience.

### Hello World

The smallest complete Kosika program returns a typed greeting:

```kosika
record Greeting {
  text: Text
}

fn greeting() -> Greeting {
  return {text: "Hello, world!"}
}

workflow main() -> Greeting {
  step greet {
    let result: Greeting = greeting()
    return result
  }
}
```

Run and replay it locally:

```text
python -m kosika.cli check examples/hello_world.kos
python -m kosika.cli run examples/hello_world.kos --trace hello_world.jsonl
python -m kosika.cli replay hello_world.jsonl
```

The run prints `{'text': 'Hello, world!'}` and records a replayable trace.

### Typed input/output

`examples/typed_input_output.kos` accepts a typed `EvaluationInput`, applies a
pure scoring function, and returns a validated `EvaluationResult`:

```text
python -m kosika.cli check examples/typed_input_output.kos
python -m kosika.cli run examples/typed_input_output.kos --input examples/typed_input.json --trace typed_input.jsonl
python -m kosika.cli replay typed_input.jsonl
```

The example returns `{'score': 0.87, 'accepted': True}`. Invalid input is rejected
before workflow execution.

## Pure typed functions

Kosika functions provide deterministic computation without model, tool, network,
or host authority. The function slice supports top-level declarations with typed
parameters, immutable typed local bindings, and a final return expression:

```kosika
fn person_name(person: Person) -> Text {
  return if person.name == "" {"Anonymous"} else {person.name}
}

fn make_greeting(person: Person) -> Greeting {
  return {text: person_name(person: person)}
}
```

Workflows invoke them with ordinary function-call syntax. Function arguments and
results are checked statically, validated at runtime, and recorded in version 5
traces for offline replay. Pure functions may call other pure functions in return
expressions and workflow arguments. Unannotated recursion and over-deep call graphs
are rejected; explicitly bounded recursion uses `recurse N`.

Function locals use `let name = expression` or `let name: Type = expression`. The
annotation is optional: when it is omitted the checker infers the type from the
initializer and then checks every later use exactly as it would an explicit one.
Initializers run in order; later locals and the return expression can refer to
earlier bindings. Names cannot be rebound or shadowed.

Use `var` for a binding that is assigned later. A `let` binding is immutable, and
the checker rejects an assignment to it, to a parameter, or to an unknown name —
and rejects an assignment whose value type does not match the binding's type.
Assignment is a statement, so it may not appear in `return` position. A loop body
may assign to a `var` declared outside it, which is how an accumulator is written:

```kosika
fn total(box: Box) -> Number {
  var sum = 0
  for value in box.values {
    sum = sum + value
  }
  return sum
}
```

A function body may also contain loops, conditionals, and assignments as
statements. The body runs in the order it is written, so a `let` placed after
a loop sees the loop's result.
A braced body with no trailing expression evaluates to `Null`, so
`repeat 3 times { sum = sum + 1 }` needs no filler value.

See `examples/local_bindings.kos` for a runnable example
(`python -m kosika.cli run examples/local_bindings.kos`).

Pure expressions support precedence-aware numeric arithmetic, numeric comparisons,
strict same-type equality, Boolean `and`/`or`/`not`, unary negation, parentheses,
and typed `if condition {value} else {value}` expressions. Boolean operators and
conditionals evaluate only the selected branch.

```text
python -m kosika.cli check examples/pure_function.kos
python -m kosika.cli run examples/pure_function.kos --input examples/person.json
```

### Function values and escaping closures

Functions are first-class values. A function type is written `fn(parameter: Type, ...) -> Output`,
and a function can return another function, accept one as an argument, or be bound
to a local with an explicit function type. A nested function that captures an outer
binding is returned with `ref function_name`; the capture is lexical and immutable.

```kosika
fn make_adder(base: Number) -> fn(value: Number) -> Number {
  fn add(value: Number) -> Number { return base + value }
  return ref add
}

fn apply(operation: fn(value: Number) -> Number, value: Number) -> Result {
  return {value: operation(value: value)}
}

workflow main() -> Result {
  step create {
    let add_ten: fn(value: Number) -> Number = make_adder(base: 10)
    return add_ten
  }
  step use {
    let result = apply(operation: add_ten, value: 5)
    return result
  }
}
```

Top-level functions can be passed directly with `ref function_name`. Bound function
values can be invoked directly as expressions or as workflow step calls. The checker
rejects signature mismatches, records that try to hold function values, and references
to functions that are not visible at the call site. Runtime invocation checks the
closure signature and capture set, and replay rejects tampered closure payloads.

Try the complete example offline from this repository:

```text
python -m kosika.cli check examples/closures.kos
python -m kosika.cli run examples/closures.kos --workflow main --trace closures.jsonl
python -m kosika.cli replay closures.jsonl
python -m kosika.cli mindmap examples/closures.kos --format json
```

Nested declarations, recursion, mutation, and loops remain separate language-core
slices.

## Typed model workflows

`model` declares a typed interface. `use_model` makes one model request and accepts
only a complete JSON record matching its output type. Providers and credentials are
configured by the host; declaring a model does not grant network access.

```kosika
record Question { text: Text }
record Answer { text: Text confidence: Number }
model writer { input: Question output: Answer }

workflow answer(question: Question) -> Answer {
  step respond {
    let result: Answer = use_model writer(text: question.text)
    return result
  }
}
```

Model deployments and request parameters can be declared without putting provider
credentials in source. `temperature` is bounded to `0..2`, `top_p` to `0..1`,
and both penalties to `-2..2`; output tokens are bounded to `1..1000000`.
`stop` accepts up to four unique non-empty strings, each at most 256 characters.

```kosika
model research_writer {
  input: Question
  output: Answer
  deployment: "local-research-model"
  parameters: {
    temperature: 0.2
    max_output_tokens: 2048
    top_p: 0.9
    frequency_penalty: -0.1
    presence_penalty: 0.1
    stop: ["END"]
  }
}
```

Try the complete example offline from this repository:

```text
python -m kosika.cli check examples/model_answer.kos
python -m kosika.cli run examples/model_answer.kos --workflow answer --input examples/question.json --model-fixture examples/model_answers.json --trace answer.jsonl
python -m kosika.cli replay answer.jsonl
```

The input file maps workflow parameter names to records. The fixture maps declared
model names to ordered lists of response records. These are deterministic test
responses, not live inference. To use a local model, replace `--model-fixture ...`
with `--model YOUR_MODEL`. That one flag grants the model to every model the
program declares, and Kosika prints the grant. The endpoint is `--model-url`, then
`KOSIKA_MODEL_URL`, then `http://localhost:11434/v1`; `--model-protocol ollama`
selects Ollama's native API. To grant a single declared model instead, use
`--model-binding writer --model YOUR_MODEL --model-url URL`. The provider uses
`KOSIKA_MODEL_API_KEY` if supplied.
The Python API accepts `run(..., inputs={...}, model_bindings={"writer": provider})`.

Missing bindings or invalid inputs are rejected before any workflow call. Model
steps expose no tools, reject tool-call responses, and fail on malformed, truncated,
or schema-invalid output. There are no automatic model retries in this slice.

Successful runs record inputs, model response text, validated outcomes, token usage
when provided, and an AST hash in a versioned JSONL trace. Treat trace files as
private application data. Offline replay validates event order and outcome
consistency and returns cached results without invoking tools or providers. It also
accepts complete legacy tool traces. Validation detects inconsistent records; it
does not authenticate a trace against malicious rewriting. Failed runs do not yet
produce a complete replayable failure trace. Optional durable sessions retain
checkpoint state and support explicit workflow mutation approval; see the development guide.

## Deterministic evaluations

`kosika.evaluation` runs fresh workflow cases with fixture-backed providers,
writes one replayable JSONL trace per case, and produces a machine-readable
`report.json`. Assertions are data: cases support exact output matching or
required text fragments, never executable test code.

The research assistant evaluation uses the existing local MCP document fixture:

```powershell
$env:KOSIKA_DOCUMENTS_DIR = "examples/research_documents"
python -m kosika.devtools eval examples/research_assistant.kos `
  examples/research_evaluations.json `
  --workflow answer `
  --mcp-server examples/documents_mcp_server.py `
  --mcp-manifest examples/research_documents_manifest.json `
  --mcp-source research_documents `
  --output research-evaluation
python -m kosika.cli replay research-evaluation/auditable_answer.jsonl
```

The command returns exit code `0` only when every case passes, `1` when an
assertion fails, and `2` for invalid fixture or host setup. The output directory
must be new so previous traces cannot be mistaken for a fresh evaluation.


## Local document assistant

An `ask researcher(text: question.text)` step runs a bounded agent and returns its
declared model output record. The agent declares `model: reader` and `max_steps: 4`.
This first workflow integration permits only read-only tools. The host must grant
both the model and tool bindings before execution.

Try the included sample from this repository:

```text
python -m kosika.cli check examples/document_assistant.kos
python -m examples.document_assistant approval --trace document_trace.jsonl
python -m kosika.cli replay document_trace.jsonl
```

The default model is a deterministic fixture, not AI inference. Its search tool
reads real files from `examples/documents`. The host binds the declared tool
directly in process; this demonstration does not start an MCP server. Use
`--docs-root PATH` to grant a different folder. Only direct UTF-8 `.md` and `.txt`
files are read, with limits of 32 files and 64 KiB per file; symlinks are rejected.
The adapter snapshots file contents before the agent runs and returns at most five
matching line excerpts per search. It is not an operating-system sandbox.

For an explicitly configured model, add `--model YOUR_MODEL --model-url URL`.
That provider receives the question and retrieved excerpts. No live provider is
needed for the fixture demo. Answers contain typed `text` and `sources` fields;
cited filenames must have been retrieved. This checks provenance, not whether the
answer is factually supported by those documents.

Successful agent traces include the conversation and validated tool outcomes.
Offline replay validates the transcript and returns cached results without reading
documents or calling a model. The same trace privacy, authenticity, and failed-run
limitations described above apply.

### Research assistant example

`examples/research_assistant.kos` adds a bounded, read-only document search to the
agent workflow. The host grants only the declared `search_docs` tool, and the
deterministic model fixture must cite files returned by that search.

```text
python -m kosika.cli check examples/research_assistant.kos
python -m examples.research_assistant --trace research_trace.jsonl
python -m kosika.devtools inspect research_trace.jsonl
python -m kosika.cli replay research_trace.jsonl
```

The example uses `examples/research_input.json`,
`examples/research_model_answers.json`, and the bounded documents in
`examples/research_documents`.

## Included

- Small `.kos` lexer and parser
- AST dataclasses
- Static capability checker
- Read-only mock MCP-style tool boundary
- Dependency-free local MCP stdio adapter
- Pinned MCP server/tool manifests
- Input and output JSON-schema validation at the live tool boundary
- JSONL event trace
- Exact replay from recorded tool output
- Unit and integration tests using pytest

## Install and run

From a source checkout, build and install Kosika into the active environment:

```text
python -m pip install .
kosika check examples/test_host.kos
kosika run examples/test_host.kos --trace run_trace.jsonl
kosika replay run_trace.jsonl
python -m pytest -v
```

`python -m kosika.cli` remains available for source-checkout development.

## Manifest generation and validation

Kosika can discover a live MCP stdio server and write a deterministic pinned manifest without
invoking any tool. It can also validate an existing manifest against a server without executing a
tool:

```text
python -m kosika.cli manifest generate `
  --mcp-server examples/mcp_host_server.py `
  --source local_stdio `
  --output examples/mcp_manifest.generated.json
python -m kosika.cli manifest validate examples/mcp_manifest.json `
  --mcp-server examples/mcp_host_server.py
```

`manifest generate` records the negotiated protocol, server identity, exact discovered tool set,
input/output schemas, and canonical schema digests. The generated JSON is stable for the same
server discovery response.

To review drift without invoking any MCP tool:

```text
python -m kosika.cli manifest diff examples/mcp_manifest.json `
  --mcp-server examples/mcp_host_server.py
python -m kosika.cli manifest diff examples/mcp_manifest.json `
  --mcp-server examples/mcp_host_server.py `
  --json
```

The human-readable form summarizes changed server identity, added or removed tools, and schema
changes. `--json` emits a structured diff for CI or later approval workflows. The review command
returns success even when drift is found. Use `manifest check` when drift must fail automation:

```text
python -m kosika.cli manifest check examples/mcp_manifest.json `
  --mcp-server examples/mcp_host_server.py
```

`manifest check` returns exit code `0` when the live server matches and exit code `1` when drift is
detected. It also supports `--json`.

A ready-to-copy GitHub Actions example is included at
`.github/workflows/kosika-manifest-check.yml`. It runs on pushes and pull requests that touch the
Kosika runtime, tests, examples, source files, or manifest-check workflow. The workflow uses the
local deterministic MCP fixture, installs pinned Ruff and MyPy for Python quality checks, fails
the job when the pinned manifest drifts, and runs the complete unittest suite with resource warnings treated as errors.
The workflow reports branch coverage in the GitHub Actions job summary, uploads `coverage.xml` as
the `kosika-coverage` artifact, and fails when total branch-aware coverage falls below 80%.

## Real local MCP stdio adapter

Kosika v0.1 can launch a local MCP-compatible server, perform the initialize handshake, discover
its advertised tools, validate the declared operation, call the tool, and record the transport in
the trace. Live execution requires a pinned manifest. The manifest pins the source, negotiated
protocol version, server name/version, exact advertised tool set, input schema, output schema, and
a canonical SHA-256 schema digest.

```text
python -m kosika.cli run examples/test_host_live.kos `
  --mcp-server examples/mcp_host_server.py `
  --mcp-manifest examples/mcp_manifest.json `
  --trace live_trace.jsonl
python -m kosika.cli replay live_trace.jsonl
```

Validation happens in three places:

1. Manifest loading rejects a missing or tampered schema digest.
2. Startup discovery rejects server identity, protocol, tool-set, or advertised schema drift.
3. Invocation validates arguments before sending `tools/call` and validates structured results before
   the workflow receives them.

The runtime also derives the expected output schema from the Kosika record returned by the tool and
requires that the pinned manifest agree with that record. The language does not grant shell, filesystem, credential, or network access by
itself. Hosts explicitly supply adapters and provider bindings; mutations require
matching approval through a durable session. The local test
server is protocol-compatible but deterministic. This is the first language/runtime slice, not a
production executor.

## Approval-protected agent factory

`examples/agent_factory.kos` demonstrates the first builder workflow. A typed request
goes to a declared architect model, the resulting `AgentSpec` is passed to an
`execute` source-writing tool, and the durable session pauses before the tool call.
The local MCP server only renders reviewable Kosika text; it never writes a file.

Generate and validate the pinned tool manifest:

```powershell
python -m kosika.cli manifest generate `
  --mcp-server examples/agent_factory_mcp_server.py `
  --source agent_factory `
  --output examples/agent_factory_manifest.json
python -m kosika.cli check examples/agent_factory.kos
```

Run with deterministic model and MCP fixtures. The first run prints the exact
`plan_hash` and `action_hash` and exits with approval required:

```powershell
python -m kosika.cli run examples/agent_factory.kos `
  --workflow build_agent `
  --input examples/agent_factory_input.json `
  --model-fixture examples/agent_factory_models.json `
  --mcp-server examples/agent_factory_mcp_server.py `
  --mcp-manifest examples/agent_factory_manifest.json `
  --mcp-source agent_factory `
  --journal agent_factory.sqlite `
  --binding-id agent-factory-v1 `
  --trace agent_factory.jsonl
```

Review those hashes and the proposed source, then rerun with
`--approval-record examples/agent_factory_approval.json`. The approved result is
recorded in the trace and can be replayed offline:

```powershell
python -m kosika.cli replay agent_factory.jsonl
```

The example intentionally stops at a proposed declaration. It does not silently
write generated source, grant capabilities, or execute arbitrary shell commands.

## Approval-protected mutation

`examples/approval_mutation.kos` is the staged mutation workflow. It declares one
typed `execute` tool and requires approval before the mutation. The local MCP
fixture writes one deterministic JSON result to the sink named by
`KOSIKA_MUTATION_SINK`; it accepts only a relative `.json` filename.

Generate and validate its pinned manifest:

```powershell
python -m kosika.cli manifest generate `
  --mcp-server examples/approval_mutation_mcp_server.py `
  --source approval_mutation `
  --output examples/approval_mutation_manifest.json
python -m kosika.cli manifest check `
  examples/approval_mutation_manifest.json `
  --mcp-server examples/approval_mutation_mcp_server.py
python -m kosika.cli check examples/approval_mutation.kos
```

Run once with a durable journal. The command pauses before the effect and prints
the exact plan and action hashes:

```powershell
$env:KOSIKA_MUTATION_SINK = ".tmp-approval-mutation-sink.json"
python -m kosika.cli run examples/approval_mutation.kos `
  --workflow apply_change `
  --input examples/approval_mutation_input.json `
  --mcp-server examples/approval_mutation_mcp_server.py `
  --mcp-manifest examples/approval_mutation_manifest.json `
  --mcp-source approval_mutation `
  --journal approval_mutation.sqlite `
  --binding-id approval-mutation-v1 `
  --trace approval_mutation.jsonl
```

Review the pending change, then resume with
`--approval-record examples/approval_mutation_approval.json`. The sink is written
only after the exact action is claimed. Re-running the completed journal
replays the checkpoint without invoking the mutation again:

```powershell
python -m kosika.cli run examples/approval_mutation.kos `
  --workflow apply_change `
  --input examples/approval_mutation_input.json `
  --mcp-server examples/approval_mutation_mcp_server.py `
  --mcp-manifest examples/approval_mutation_manifest.json `
  --mcp-source approval_mutation `
  --journal approval_mutation.sqlite `
  --binding-id approval-mutation-v1 `
  --approval-record examples/approval_mutation_approval.json `
  --trace approval_mutation.jsonl
python -m kosika.cli replay approval_mutation.jsonl
```

The approval record is bound to this source, input, binding identity, workflow
step, tool, and arguments. A changed action hash remains pending and cannot
invoke the host effect.


## Approved swarm runs

A `swarm` names agents, one workflow, a round cap, and `require approval`.
`kosika swarm` runs that workflow once per round. It stops when two consecutive
rounds return the same result (`fixed_point`) or at `max_rounds`. One approval
authorizes one run: it names the plan hash, which covers the swarm and the whole
checked program, and it is claimed in the ledger before any member acts.

Review the plan, then approve exactly that hash:

```powershell
python -m kosika.cli swarm examples/swarm_review.kos --swarm EditorialBoard --plan
python -m kosika.cli swarm examples/swarm_review.kos `
  --swarm EditorialBoard `
  --approved-by "your-name" `
  --plan-hash PLAN_HASH_FROM_ABOVE `
  --approval-ledger swarm-approvals.sqlite `
  --model-fixture examples/swarm_review_models.json `
  --tool-fixture examples/swarm_review_tools.json
```

The fixtures are deterministic stand-ins for the model and the tool. Use
`--mcp-server` with `--mcp-manifest` for a pinned MCP tool source instead of
`--tool-fixture`. All rounds share one execution budget. A failed round still
spends the approval, because members may already have acted, and the ledger
records the failure. Rerunning with the same ledger is refused.

This is not consensus. Members exchange no messages and share no state between
rounds, and the approver name is a label, not an authenticated identity.

## Local model provider and agent loop

The first v0.4-oriented agent seam is available as a Python API in `kosika.model` and `kosika.agent`.
It is intentionally provider-neutral and keeps model output separate from tool execution:

Agent declarations bind the model, instructions, and bounded execution directly in `.kos`:

```kosika
agent researcher {
  model: research_writer
  instructions: "Use only retrieved evidence and keep the answer concise."
  can: [search_documents]
  cannot: [shell]
  max_steps: 8
  max_tool_calls: 12
  max_model_calls: 8
  deadline_seconds: 60
}
```

Declared instructions are added as system context before host-provided system
prompts. Instructions and execution limits are included in the approval plan hash.
The declared deployment is used when `--model` is omitted. CLI `--model`,
`--max-steps`, and `--deadline-seconds` remain explicit overrides. Deadlines and
cancellation are cooperative; they do not interrupt an in-flight provider call.

```python
from kosika.agent import AgentLoop
from kosika.model import OpenAICompatibleProvider

provider = OpenAICompatibleProvider(
    "http://localhost:11434/v1",
    "your-local-model",
)
loop = AgentLoop(program, provider, execute_tool)
result = loop.run("monitor", "Check the disk and summarize the result.")
```

The loop exposes only tools declared in the selected Kosika agent, validates model arguments and tool
results against schemas, re-runs the static capability checker, limits model steps, and rejects
`execute` tools unless an approval record matches both the plan and concrete action hashes. `ScriptedModelProvider`
is included for deterministic tests and future exact-replay fixtures. The OpenAI-compatible adapter
only performs model HTTP requests; it never executes model-provided commands or tools itself.

The same loop is available from the terminal through `kosika agent`. Configure the local model without
putting credentials in source code:

```powershell
$env:KOSIKA_MODEL_URL = "http://localhost:11434/v1"
$env:KOSIKA_MODEL = "your-local-model"
$env:KOSIKA_MODEL_API_KEY = "optional-local-key"
python -m kosika.cli agent examples/test_host.kos `
  --agent monitor `
  --task "Check the disk and summarize the result."
```

For an agent whose declared model has typed input and output records, use
structured mode:

```json
{"prompt": "Summarize the retrieved evidence"}
```

```powershell
python -m kosika.cli agent research.kos `
  --agent researcher `
  --input request.json
```

The input JSON must match the model input record. The final model response must
be a JSON object matching the model output record; otherwise the run fails closed.
The Python API uses `loop.run("researcher", input_value={"prompt": "..."})`.
The repository includes a complete typed example in
`examples/research_agent.kos`, including a read-only `search_notes` tool,
bounded model settings, and structured `ResearchAnswer` output. Validate its
declarations with:

```powershell
python -m kosika.cli check examples/research_agent.kos
```
Run the complete typed agent offline with deterministic model and tool fixtures:

```powershell
python -m kosika.cli agent examples/research_agent.kos `
  --agent researcher `
  --input examples/research_agent_input.json `
  --model-fixture examples/research_agent_models.json `
  --tool-fixture examples/research_agent_tools.json
```

Model fixtures map the declared deployment to response objects. Tool fixtures map
the declared tool name or operation to one output record, or to a sequence of
output records for repeated calls.



Completed direct-agent runs can be recorded and replayed offline:

```powershell
python -m kosika.cli agent research.kos `
  --agent researcher `
  --input request.json `
  --trace researcher.jsonl
python -m kosika.cli replay researcher.jsonl
```

The trace records model requests/responses, tool calls/results, schemas,
configuration, structured input/output, and the plan hash. Replay validates the
recorded interaction without invoking the model or tools.
Direct agents can also use a durable SQLite journal:

```powershell
python -m kosika.cli agent examples/research_agent.kos `
  --agent researcher `
  --input examples/research_agent_input.json `
  --journal research-agent.sqlite `
  --binding-id local-research-v1 `
  --trace research-agent.jsonl

python -m kosika.cli agent examples/research_agent.kos `
  --agent researcher `
  --input examples/research_agent_input.json `
  --journal research-agent.sqlite `
  --binding-id local-research-v1 `
  --resume
```

The binding identity covers the host model, tools, and policy configuration.
Completed runs return from the journal without another model call. An
interrupted in-flight model or tool call is marked uncertain and is never
automatically repeated. `deadline_seconds` applies to each process invocation;
resuming a journal starts a new cooperative deadline window.
Inspect a journal without exposing prompts, tool arguments, or model output:

```powershell
python -m kosika.cli journal research-agent.sqlite
python -m kosika.cli journal research-agent.sqlite --format json
```



### Automatic skills

Kosika can select multiple relevant local skills when a task does not name them
explicitly. Store each skill in a directory containing `skill.json` and
`SKILL.md`, then inspect the deterministic selection:

```powershell
python -m kosika.cli skills `
  --skill-dir .kosika/skills `
  --task "Review this Rust workflow for capability security" `
  --format text
```

Use the same directory with an agent run:

```powershell
python -m kosika.cli agent examples/test_host.kos `
  --agent monitor `
  --task "Review this workflow for capability security" `
  --skill-dir .kosika/skills `
  --skill-policy hybrid
```

Selected skills are advisory context only. They are included in the plan hash
for reproducibility but cannot grant tools, capabilities, or approvals.


The command prints the deterministic plan hash before execution. For a live MCP source, a pinned
manifest and server are required:

```powershell
python -m kosika.cli agent examples/test_host_live.kos `
  --agent monitor `
  --task "Check the disk and summarize the result." `
  --mcp-server examples/mcp_host_server.py `
  --mcp-manifest examples/mcp_manifest.json
```

The CLI does not accept an API key argument, so secrets do not enter shell history through this
command. Observe-only tools can run without approval. Execute-capability tools require the matching
plan hash through `--approval-plan-hash`, the exact tool-call hash through
`--approval-action-hash`, and the approval actor with `--approved-by`. An unapproved
execute call prints the tool, arguments, and both hashes. Review those arguments
before rerunning; a changed model call will produce a different action hash and be refused.
Pass `--approval-ledger PATH` for execute calls. Kosika commits the claim to
that SQLite file before invoking the tool, then records its outcome hash or failure type.
A completed, failed, or interrupted claim cannot be reused with the same ledger.
Keep the ledger at a stable path; removing or replacing it removes this local replay guard.
The actor name is a label, not authenticated identity.
One record authorizes one execute call per run.

### Local VS Code commands

The repository includes a local extension backed by a long-running Kosika LSP
server. It provides syntax highlighting, snippets, debounced live diagnostics,
live workspace indexing for `.kos` files, document symbols, hover,
go-to-definition, references, completion, and commands for checking the active
file, explaining it, rendering Kosika or unified Mermaid mindmaps, and
inspecting selected skills:

```powershell
code --extensionDevelopmentPath=editors/vscode
```

Set `kosika.pythonPath` if the extension should use a specific Python
interpreter. Set `kosika.skillDirectories` to one or more local skill roots.
Set `kosika.modelName` to enable a local OpenAI-compatible model for the Explain
command; leave it empty for deterministic static explanations.

Diagnostics use the versioned `kosika.diagnostics/v1` JSON contract. Run
`python -m kosika.cli check PATH --format json` for a module-aware report. Every
diagnostic has a stable code and originating path; parser spans cover the
offending token, while checker spans identify the unique owning declaration or
step. If no truthful owner is available, JSON reports `range: null` and
`data.anchor_missing: true` instead of inventing a source position. Imported
file failures are published against the imported file URI by the LSP.

### Machine-readable development interface

`kosika.development/v1` exposes checked project inspection, K04 diagnostics,
bounded execution, validated trace inspection, and deterministic maps without a
second parser or runtime. Python hosts can call `inspect_project`,
`check_project`, `run_project`, `inspect_trace`, and `map_project` from
`kosika.development`.

The CLI accepts one strict JSON request from a file or `-` for standard input:

```json
{
  "schema": "kosika.development/v1",
  "operation": "inspect",
  "source": "examples/composed/main.kos",
  "root": "examples/composed"
}
```

```powershell
python -m kosika.cli development request.json
```

Operations are `inspect`, `check`, `run`, `trace`, and `map`. Every response
uses the same schema and includes `operation`, `ok`, `project`, `result`,
`diagnostics`, and `error`. JSON requests cannot grant model or tool authority;
an embedding host must inject explicit bindings into the Python API. Therefore
CLI `run` requests support workflows requiring no external binding and otherwise
fail closed.

### Cross-language components

`kosika.component/v1` binds one declared Kosika tool to one pinned local process
artifact. The contract versions the tool input/output schemas, required access,
effects and capabilities, declared errors, termination deadline, operation, and
artifact SHA-256. The host must grant every required effect and capability
explicitly before the process starts.

The included JavaScript example uses the language-neutral `json-stdio/v1`
request/response protocol:

```text
python -m kosika.cli component check examples/components/uppercase.component.json --json
python -m kosika.cli run examples/component_uppercase.kos \
  --input examples/component_uppercase_input.json \
  --component-manifest examples/components/uppercase.component.json \
  --component-runtime node \
  --allow-component-effect cpu:compute \
  --allow-component-capability text:transform
```

The host chooses the runtime executable; Kosika appends and re-hashes the pinned
artifact before every call, invokes no shell, validates both JSON boundaries,
accepts only declared component errors, bounds messages to 1 MiB, and kills the
process at the contract deadline. This is a process contract, not an OS sandbox:
the digest identifies bytes but does not prove their behavior.

### Authoring evaluation

`author-eval` measures the bounded `check -> propose -> repair -> run -> inspect`
loop over the generated repair corpus. The report includes repair and first-pass
rates, behavioral comparisons where execution is possible, authority violations,
per-attempt model calls, prompt/completion tokens and provider latency, plus
whole-case wall time.

```text
python -m kosika.cli author-eval --solver identity --limit 40 --no-behavior
python -m kosika.cli author-eval --solver reference --limit 60 --no-behavior
python -m kosika.cli author-eval --solver model --base-url URL --model NAME --limit 10 --format json
```

`identity` is the no-provider floor. `reference` is the stored-patch ceiling over
repairs expressible as a unique search/replace block. A live model is never
enabled implicitly. Evaluation works in temporary projects, never writes model
proposals into the repository, and treats `agent.cannot` as denial rather than
granted authority. See [DEVELOPMENT.md](DEVELOPMENT.md#authoring-evaluations)
for the report and authority-audit contracts.



## Typed tool inputs

Tools may declare an input record. The record becomes the model-facing JSON schema and is also used to
check literal workflow arguments before any tool runs:

```kosika
record DiskRequest {
  mount: Text
}

tool check_disk {
  source mcp "local_mock"
  input: DiskRequest
  operation: "get_disk_info"
  access: observe
  output: DiskStatus
}

workflow main() -> DiskStatus {
  step inspect {
    let status: DiskStatus = execute monitor.check_disk(mount: "/")
    return status
  }
  step recheck {
    let final: DiskStatus = execute monitor.check_disk(mount: status.mount)
    return final
  }
}
```

Each step binds its validated record result with `let`. Later steps may reference that record or one
of its fields, and the checker resolves references in declaration order before runtime execution.
Arguments currently support named string, number, boolean, `null`, variable, and field-access
expressions. Existing tools without an `input` declaration remain zero-argument tools for backward
compatibility. See `examples/typed_input.kos` for a complete runnable example.

Structured record fields may contain nested records or arrays. Workflow arguments can construct these
values directly and later steps can access fields or index arrays:

```kosika
record Metadata {
  name: Text
}

record Request {
  tags: Text[]
  metadata: Metadata
}

// Inside a workflow call:
inspect(tags: ["disk", "local"], metadata: {name: "root-disk"})
// Later, after binding the result:
inspect(tags: status.tags, metadata: status.metadata)
```

Structured expressions support arrays, nested record literals, field access,
integer indexing, arithmetic, Boolean operators, conditionals, pattern matching,
assignment, and bounded loops. See `examples/structured_values.kos`,
`examples/local_bindings.kos`, and `examples/recursion_match.kos`.

## Code mindmaps and bounded assistant

Kosika can explain a checked program without asking a model to infer its structure. The `mindmap`
command derives deterministic module, record, field, tool, model, agent, workflow, step, and call-graph
nodes from the parser/AST. It can emit structured JSON for programs and tooling, Mermaid text for a
diagram renderer, or a compact terminal summary:

```text
python -m kosika.cli mindmap examples/composed/main.kos --format json --output code-map.json
python -m kosika.cli mindmap examples/composed/main.kos --format mermaid --output code-map.mmd
python -m kosika.cli mindmap examples/composed/main.kos --format text
```

Imported modules retain their relative paths, content hashes, declarations, and direct import edges.
The map is read-only and is generated only after static checking succeeds. It is an explanation of
Kosika's checked structure, not an execution trace or a model-generated claim about intent.

## Audience-adaptive maps

`mindmap` answers "what does the checked program declare". `audience-map` answers it for a
particular reader, and labels where every fact came from:

```text
python -m kosika.cli audience-map examples/research_agent.kos --audience reviewer --format text
python -m kosika.cli audience-map examples/component_uppercase.kos --audience maintainer \
    --component-contract examples/components/uppercase.component.json --format json
python -m kosika.cli audience-map examples/composed/main.kos --audience operator \
    --trace run.jsonl --format mermaid
```

Five audiences are available: `outsider`, `overview`, `operator`, `reviewer`, and `maintainer`.
Each profile states the question it answers, so a new audience cannot silently inherit another
audience's detail level.

Four evidence classes are kept apart, even where several support the same entry:

| Class | Source | What it does not mean |
|---|---|---|
| `checked` | the type-checked AST | nothing about runtime behavior |
| `declared` | a versioned `kosika.component/v1` contract | the artifact's hash identifies bytes, not what they do |
| `observed` | a completed trace whose recorded program hash matches this project | nothing about runs that were not recorded |
| `human` | explicit `--purpose` notes | not independently verified |

There is deliberately no `inferred` class. A fact that cannot be sourced from checked code, a
contract, a matching trace, or an explicit note does not appear, and `coverage` reports how many
checked entries the audience did not show so a narrow view cannot read as a smaller program.

Two consequences are worth stating plainly. Executing a component does not make its contract
`observed` — declared authority is a claim by the component, not a record of it being used. And a
trace recorded against a different program contributes nothing at all, recorded as
`trace-program-hash-mismatch`, because attaching one program's behavior to another is exactly the
error this format exists to prevent. The command verifies evidence separation and content digest
before printing, and exits non-zero rather than emit a map that fails its own checks.

Purpose notes are the one input that is a human claim:

```text
python -m kosika.cli audience-map examples/hello_world.kos --audience outsider \
    --purpose "Greets the caller on the public endpoint." --format text
```

See [KOSIKA_PLAN.md](KOSIKA_PLAN.md) for the K09 completion record.

Use `explain` when you want a focused explanation rather than the whole map. Select one or more
workflows or records, attach diagnostics, and optionally validate a completed JSONL trace offline:

```text
python -m kosika.cli run examples/composed/main.kos `
  --input examples/composed/inputs.json `
  --model-fixture examples/composed/responses.json `
  --trace composed.jsonl
python -m kosika.cli explain examples/composed/main.kos `
  --workflow main `
  --record Answer `
  --diagnostic "Review the model boundary" `
  --trace composed.jsonl `
  --format text
python -m kosika.cli explain examples/composed/main.kos --workflow main --format json
```

Selection follows the deterministic mindmap relationships, so the result includes the selected
workflow or record plus its connected steps, contracts, calls, permissions, and module boundaries.
Trace summaries use the existing strict replay validator and never execute the workflow, tool, or model.
A trace built from a different loaded program is reported through `program_hash_match: false` in JSON
and as a warning in text output.

The `assistant` command supplies that checked mindmap and optional diagnostics to a bounded assistant.
Offline use needs no provider and returns a deterministic static summary. Fixture responses are useful
for repeatable tests and demos:

```text
["The workflow enters research.kos, calls reader, and returns Answer."]
python -m kosika.cli assistant examples/composed/main.kos `
  --task "Explain the code structure" `
  --fixture assistant-responses.json `
  --json
```

For optional local inference, omit `--fixture` and configure `KOSIKA_MODEL` (and optionally
`KOSIKA_MODEL_URL`); the adapter is OpenAI-compatible and uses `KOSIKA_MODEL_API_KEY` only from the
environment. The assistant receives no tools, cannot execute workflows or shell commands, and rejects
tool-call or truncated responses. The checker, schema validation, replay, and approval system remain
authoritative; assistant text is an explanation, not permission to mutate anything.

## Live AI worlds

Kosika can render the same checked agent system as a live, event-backed world.
The world view is not a simulation: a resident only wakes, walks, or lights up
when the runtime publishes an event for that real agent or tool call.

Choose a deterministic environment preset for the live isometric view:

```powershell
python -m kosika.cli city examples `
  --format live `
  --environment forest `
  --serve `
  --port 8787
```

Available environments are `metropolitan`, `urban`, `suburban`, `rural`,
`desert`, `forest`, and `water`. The Kosika declarations remain authoritative;
the environment changes terrain, atmosphere, roads, and presentation without
changing capabilities or execution behavior.

World manifests customize the presentation without changing Kosika execution
contracts. The reference Forest Knowledge Reserve maps real knowledge domains
and residents from `examples/city` to named institutions:

```powershell
python -m kosika.cli city examples/city `
  --format live `
  --world examples/worlds/forest_knowledge_reserve.json `
  --output forest-knowledge-reserve.html
```

The same manifest can be served live with `--serve`. Its buildings are labels
over the checked domains; the event stream and replay traces remain generated
by real Kosika executions.

The reference manifest uses the `isometric_lowpoly` render style: an original
Three.js game-world view with procedural landmark buildings, layered roofs,
flags, windows, a plaza fountain, rocks, water tiles, terrain, trees, shadows,
and animated resident figures. Materials use generated hand-painted-style
textures, while the page retains the deterministic SVG fallback if WebGL or
the optional glTF loader is unavailable. Runtime event identity remains
authoritative and drives the 3D activity state.

## Network and vector design

Kosika also includes a declarative network directory and deterministic vector design layer for agent
workflows, model boundaries, MCP nodes, devices, memory graphs, and virtual spaces. Network manifests
are validated before rendering and can be indexed without connecting to external systems:

```text
python -m kosika.cli network validate network/example-network.json
python -m kosika.cli network show network/example-network.json --format mermaid
python -m kosika.cli network index network --recursive --format text
```

Generate a design manifest from checked Kosika source, then render a modern workflow, network, or code
mindmap view as SVG, Mermaid, JSON, or terminal text:

```text
python -m kosika.cli design generate examples/test_host_live.kos `
  --id test-host.design `
  --name "Test Host Agent Workflow" `
  --network network/example-network.json `
  --output design/test-host.json
python -m kosika.cli design show design/test-host.json `
  --format svg --view workflow --output design/test-host.svg
python -m kosika.cli design show design/test-host.json `
  --format svg --view network --output design/test-host-network.svg
python -m kosika.cli design show design/test-host.json `
  --format svg --view mindmap --output design/test-host-mindmap.svg
```

The design graph is derived from the checked AST, bounded module graph, and declared network manifest.
For composed programs it includes module containers, declaration-ownership edges, and direct import edges.
It uses curved edges, arrowheads, category colors, legends, and bounded deterministic layout; it does
not ask a model to infer program structure.

## Browser workbench

Use [Kosika Workbench](WORKBENCH.md) to edit, check, run, and replay programs locally with deterministic model fixtures. Start with `python -B -m kosika.workbench --port 8765`, then open http://127.0.0.1:8765.
