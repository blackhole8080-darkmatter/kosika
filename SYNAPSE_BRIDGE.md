# Kosika-to-Synapse bridge

`kosika.synapse_bridge` is a small, fail-closed adapter for showing Kosika activity in a Synapse event bus or neural-field UI.

## Contract

Every normalized event is a JSON object with:

- `schema`: `synapse.kosika.event/v1`
- `bridge_version`: integer contract version
- `event_id`: deterministic within one emitted sequence
- `sequence`: ordered integer starting at one for an execution
- `timestamp`: UTC ISO-8601 timestamp from Kosika when available
- `bus`: `synapse`
- `source` and `region`: both `kosika`
- `type`: a stable name such as `kosika_check`, `kosika_workflow_start`, `kosika_model_call`, `kosika_tool_call`, `kosika_workflow_complete`, `kosika_replay`, or `kosika_error`
- `status`: `started`, `completed`, `observed`, `pending`, `granted`, `denied`, `passed`, `failed`, `cancelled`, or `error`
- `replay`: whether the event came from offline replay
- optional safe identity fields: workflow, step, agent, model, tool, operation, transport, access, and binding
- optional `payload`: metadata summaries only

Payload values are represented by type, size, and SHA-256 digest. Raw prompts, model content, tool arguments, tool results, and schemas are never forwarded by the bridge. Error messages are truncated and normalized for display.

## Usage

```python
from kosika.events import read_event_jsonl, write_event_jsonl
from kosika.synapse_bridge import demo_run, execute, replay_bridge

# Check and execute a local-only fixture.
run_result = demo_run("artifacts/kosika-demo.jsonl")

# Or execute a parsed Program with explicitly authorized bindings.
run_result = execute(program, "main", "artifacts/run.jsonl", inputs={})

# Validate the trace without invoking a model or tool.
replayed = replay_bridge("artifacts/kosika-demo.jsonl")
```

`BridgeResult.ok` is false for checker, runtime, or replay failures. A failure emits a corresponding `kosika_error` event rather than a false completion event.

`kosika.events` is the formal boundary for normalized streams. `validate_event` checks one envelope; `validate_events` checks contiguous sequence numbers and unique IDs; `read_event_jsonl` rejects duplicate JSON keys, non-finite numbers, blank/malformed lines, and invalid envelopes; and `write_event_jsonl` validates before atomically replacing the output file. Raw runtime traces remain a separate replay format and are not rewritten into this envelope.

The first prototype uses `demo_run`, which only exercises Kosika's allowlisted `local_mock` observation tool. It does not open network connections, invoke a model, execute shell commands, or mutate the desktop.
