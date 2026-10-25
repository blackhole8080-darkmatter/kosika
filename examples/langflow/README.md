# Langflow flows in Kosika

A flow drawn in Langflow can answer a Kosika model step, or stand behind a
tool an agent calls. Both are bridges; neither changes the language.

## A flow as a Kosika model

Kosika declares the model step; Langflow draws what answers it. The route is a
command, so nothing in the language or the runtime changes:

```text
kosika run examples/langflow/main.kos --input examples/langflow/input.json \
  --models examples/langflow/models.json
```

`models.json` maps the program's `writer` to `kosika-langflow`, the bridge in
[`kosika/langflow.py`](../../kosika/langflow.py) (installed as a command by
`pip install .`). The bridge reads Kosika's model request on standard input,
runs the flow, and prints the flow's answer.

Kosika starts the command in an empty temporary folder, so from a source
checkout that is not installed, use
`["python", "-m", "kosika.langflow", "kosika-model"]` and have `PYTHONPATH`
name the checkout.

## The flow

[`kosika-model.flow.json`](kosika-model.flow.json) is a three-node flow: **Chat
Input → Ollama (qwen2.5:3b) → Chat Output**. It needs no Kosika-specific
component because Kosika writes the JSON Schema it wants into the prompt.

- Import it in Langflow (**Projects → Import**) or build the same three nodes.
- The flow's endpoint name is `kosika-model`; a flow id works as well.
- Any flow whose last node is Chat Output works. A prompt or an agent belongs
  between the input and the output, not after it.

## A flow as an agent's model

An agent can be answered by the same bridge. `agent.kos` declares a general
model (`model flow`: text in, text out), an agent that names it, and nothing
else:

```text
export LANGFLOW_API_KEY=...

kosika agent examples/langflow/agent.kos --agent narrator \
  --task "explain Langflow" \
  --input examples/langflow/agent.input.json \
  --models examples/langflow/agent.models.json \
  --trace agent-trace.jsonl

kosika replay agent-trace.jsonl
```

[`agent.models.json`](agent.models.json) points `flow` at
`["kosika-langflow", "kosika-model"]`, so every turn of the agent is one run of
the flow. The flow receives the whole conversation as a single prompt and is
told the shape to answer in by the agent's instructions, because a command
route carries no schema and no tools; `agent.kos` asks for the `Reply` record
as JSON and Kosika validates it. An answer in prose fails the run, like any
other bad model answer.

`--models` is the agent's counterpart to `kosika run --models`: the file names
the route for each declared model, and the agent's own model must be in it. It
is used on its own, without `--model`, `--model-fixture` or `--virtual-model`.

A command-line model is never given tools, so an agent with a command route
cannot declare any: `CommandModelProvider` refuses a request that carries
tools. An agent that calls tools needs an HTTP model, while the flow itself may
call whatever it likes. The trace is a complete record of the model turns, so
`kosika replay` reproduces the answer with Langflow switched off.

## Telling the bridge where Langflow is

| Variable | Meaning | Default |
| --- | --- | --- |
| `LANGFLOW_URL` | Where Langflow listens | `http://127.0.0.1:7860` |
| `LANGFLOW_API_KEY` | Sent as `x-api-key` | unset |
| `LANGFLOW_TIMEOUT` | Seconds to wait for the flow | `280` |

Since Langflow 1.5 a flow run needs an API key. Create one in Langflow under
**Settings → Langflow API Keys**, then set it in the shell that runs Kosika:

```text
export LANGFLOW_API_KEY=...
```

Without a key the bridge asks `/api/v1/auto_login` for a session, which only a
server with `LANGFLOW_SKIP_AUTH_AUTO_LOGIN=true` accepts for a flow run.

## A flow as an agent's tool

A project's flows can also be tools: Langflow offers every MCP-enabled flow
over Streamable HTTP, and [`kosika/langflow_mcp.py`](../../kosika/langflow_mcp.py)
is the MCP server on standard input and output that relays to it, which is what
Kosika starts for a declared tool.

```text
tool brief_flow {
  source mcp "flows"
  operation: "kosika_brief"
  access: execute
  input: BriefIn
  output: BriefOut
}
```

[`brief.kos`](brief.kos) is the whole example: the tool, a model, an agent that
must call it, and a workflow. [`flows.manifest.json`](flows.manifest.json) is
its pinned manifest, generated from a project holding two flows.

### In Langflow

1. Keep the flows you want to offer in one project (**Projects → New Project**,
then import [`kosika-brief.flow.json`](kosika-brief.flow.json) or drag flows in).
2. Switch on **MCP** for each flow you want to offer, in the flow's own MCP
settings. Only those appear, and a flow's tool name is its MCP name
(`kosika_brief` here, from `action_name`).
3. Copy the project id: **Projects → the project → Settings**, or from its URL.

### The bridge

| Variable | Meaning | Default |
| --- | --- | --- |
| `LANGFLOW_MCP_PROJECT` | The project whose flows become tools | required |
| `LANGFLOW_MCP_URL` | The whole Streamable HTTP endpoint, instead of the project | unset |
| `LANGFLOW_URL` | Where Langflow listens | `http://127.0.0.1:7860` |
| `LANGFLOW_API_KEY` | Sent as `x-api-key`; without one, auto-login is tried | unset |
| `LANGFLOW_MCP_TIMEOUT` | Seconds to wait for one flow | `55` |
| `LANGFLOW_MCP_SCHEMAS` | `kosika` or `langflow` tool schemas | `kosika` |

Kosika starts an MCP server with no arguments of its own, so the project goes
in the environment. Run by hand, `kosika-langflow-mcp --project ID` takes the
same three as flags.

### Pin it, then call it

```text
export LANGFLOW_MCP_PROJECT=<project id>
export LANGFLOW_API_KEY=<key>

kosika manifest generate --mcp-server kosika/langflow_mcp.py \
  --source flows --output flows.manifest.json

kosika agent examples/langflow/brief.kos --agent briefer \
  --task "brief the note" --input examples/langflow/brief.input.json \
  --model qwen2.5:3b \
  --mcp-server kosika/langflow_mcp.py \
  --mcp-manifest flows.manifest.json --mcp-source flows \
  --approve --approval-ledger approvals.sqlite
```

`manifest generate` starts the bridge and records what the project offers
without running any flow; `manifest check` fails when a flow is added, removed
or gains a differently shaped input. `kosika run` takes the same two flags as
`kosika agent` when a workflow asks an agent that has the tool.

Every MCP tool Langflow offers is `access: execute`: Langflow marks no flow
read-only, so each call waits for your yes. `--approve` asks at the terminal
and records the answer in the ledger; unattended, a first run is refused with a
plan hash and an action hash, and passing both (`--approval-plan-hash`,
`--approval-action-hash`, plus `--approved-by`) approves that one call.

### Why the schemas are reshaped

Langflow describes a flow's tool as `input_value` and optional `session_id`,
with prose on both, no `additionalProperties`, and no output schema. Kosika
pins a tool's schema and then refuses any declaration whose record is not that
schema exactly, and a Kosika record always closes its fields and requires all
of them. Nothing Langflow advertises can equal that, so by default the bridge
offers each flow as `{ input_value: Text }` in and `{ text: Text }` out, which
is what `flows.manifest.json` pins and what `BriefIn`/`BriefOut` declare.

`--schemas langflow` relays Langflow's own schemas for other MCP clients; a
Kosika declaration cannot be pinned against those.

### What the tool path cannot do

- The answer must be text. A flow whose output is a file or an image has
  nothing to put in `text`.
- One call is one flow run, with Langflow's default session, so a Kosika tool
  does not carry a conversation across calls. A flow that needs a `session_id`
  needs a different bridge.
- `kosika agent` waits ten seconds for each MCP request and `kosika run` sixty,
  and a flow that runs a model on a laptop is close to that; `kosika_brief`
  answers in five to eight seconds. A slower flow belongs in a workflow.

## What this cannot do

- A command-route model is not given tools, so an agent that calls tools still
  needs an HTTP model. The flow itself may call tools; Kosika sees one answer.
- One Kosika model call is one flow run. Budgets, retries, `otherwise`, traces,
  and offline replay are Kosika's and work unchanged.
- A flow that rambles or answers outside the schema fails the step like any
  other bad model answer; `retry` and `otherwise` are the remedy.
