A dependency-free first vertical slice of the Kosika AI systems language. Synapse was the earlier working codename.


## Language and runtime expansion

Kosika now includes local imports with explicit exports, typed workflow calls,
nested replay, model budgets/telemetry, durable checkpoints and approval pauses,
a bounded read-only DAG API, host-granted JSON/HTTP/SQLite adapters, development
commands, editor snippets, and evaluation fixtures.

See [DEVELOPMENT.md](DEVELOPMENT.md) for runnable examples, APIs, security boundaries,
and current limitations. These are initial implementations: live inference on the
installed local model timed out, parallelism is host-defined, and editor support
does not yet include a language server.

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
expressions and workflow arguments; the checker rejects recursive or over-deep call
graphs.

Function locals use `let name: Type = expression`. Initializers run in order;
later locals and the return expression can refer to earlier bindings. Names cannot
be rebound or shadow parameters. See `examples/local_bindings.kos` for a runnable
example (`python -m kosika.cli run examples/local_bindings.kos`).

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
responses, not live inference. To explicitly bind a configured local model, replace
`--model-fixture ...` with `--model-binding writer --model YOUR_MODEL --model-url
http://localhost:11434/v1`. The provider uses `KOSIKA_MODEL_API_KEY` if supplied.
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
- Unit and integration tests using Python's standard library

## Run

```text
python -m kosika.cli check examples/test_host.kos
python -m kosika.cli run examples/test_host.kos --trace run_trace.jsonl
python -m kosika.cli replay run_trace.jsonl
python -m unittest discover -s tests -v
```

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

The current structured-expression slice supports arrays, nested record literals, field access, and
integer indexing. It intentionally does not yet include arithmetic, boolean operators, loops, or
conditional control flow. See `examples/structured_values.kos` for a complete example.

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
