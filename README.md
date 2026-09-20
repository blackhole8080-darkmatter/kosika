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

Try the complete example offline from this repository:

```text
python -m kosika.cli check examples/model_answer.syn
python -m kosika.cli run examples/model_answer.syn --workflow answer --input examples/question.json --model-fixture examples/model_answers.json --trace answer.jsonl
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
python -m kosika.cli check examples/document_assistant.syn
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

- Small `.syn` lexer and parser
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
python -m kosika.cli check examples/test_host.syn
python -m kosika.cli run examples/test_host.syn --trace run_trace.jsonl
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
python -m kosika.cli run examples/test_host_live.syn `
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
python -m kosika.cli agent examples/test_host.syn `
  --agent monitor `
  --task "Check the disk and summarize the result."
```

The command prints the deterministic plan hash before execution. For a live MCP source, a pinned
manifest and server are required:

```powershell
python -m kosika.cli agent examples/test_host_live.syn `
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
compatibility. See `examples/typed_input.syn` for a complete runnable example.

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
conditional control flow. See `examples/structured_values.syn` for a complete example.
