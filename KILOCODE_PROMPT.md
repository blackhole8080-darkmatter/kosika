# Kilocode prompt: continue Kosika

You are continuing work in the Kosika repository. Work from the repository root and read `DEVELOPMENT.md`, `README.md`, `examples/PROGRAM_SEQUENCE.md`, and the repository agent instructions before editing.

## Current state

Kosika is a typed, Python-first AI workflow language/runtime with:

- records, typed functions, workflows, models, agents, tools, swarms, and local modules;
- deterministic fixture-backed model execution and JSONL trace replay;
- durable execution journals and approval pauses for `execute` tools;
- pinned MCP stdio manifests with schema validation and drift checks;
- deterministic source maps, mind maps, and audience-specific explanations;
- Python, JavaScript, and TypeScript structural analysis;
- a completed agent-factory example at `examples/agent_factory.kos`.

The agent factory accepts a typed `BuildRequest`, asks the declared `architect` model for an `AgentSpec`, and sends that spec to the approval-protected `source_writer` agent. The pinned local MCP fixture renders reviewable Kosika source and explicitly does not write files or execute shell commands. Its fixtures are:

- `examples/agent_factory_input.json`
- `examples/agent_factory_models.json`
- `examples/agent_factory_manifest.json`
- `examples/agent_factory_approval.json`
- `examples/agent_factory_mcp_server.py`

Do not broaden this into a graphics, browser, provider, package-manager, or general multi-language rewrite.

## Next milestone

Implement the next row in `examples/PROGRAM_SEQUENCE.md`: `examples/approval_mutation.kos`.

Required behavior:

1. Declare a typed record input and typed result.
2. Declare one external `execute` tool representing a mutation.
3. Require approval at the workflow boundary.
4. Use the existing durable journal/session contract; do not invent a second approval mechanism.
5. First execution must pause before the mutation and expose the deterministic plan hash, exact action hash, tool name, arguments, and workflow step.
6. A matching approval must resume the same journal and execute exactly once.
7. A mismatched plan/action approval must be rejected without invoking the tool.
8. The completed trace must replay offline with the same result.
9. Keep the host effect deterministic and local. Never grant arbitrary shell, filesystem, credential, or network authority from Kosika source.

Reuse the existing approval examples, `ExecutionSession`, `ApprovalRecord`, `ApprovalLedger`, CLI flags, and trace/replay code. Inspect their actual APIs before editing; do not guess names or add compatibility shims.

## Verification gate

Run the focused example checks and the relevant existing tests. At minimum, prove:

```text
python -m kosika.cli check examples/approval_mutation.kos
python -m kosika.cli run ...                 # deterministic first run pauses
python -m kosika.cli run ... --approval-record ...  # matching approval resumes
python -m kosika.cli replay ...
python -m pytest <focused approval/runtime tests> -q
python -m ruff check kosika tests
```

Use throwaway journals/traces for the proof and remove them afterward. Add permanent tests only for consumer-visible approval behavior: pause-before-effect, exact action binding, durable resume, single execution, rejection, and replay. Update `README.md` and `examples/PROGRAM_SEQUENCE.md` only after the runtime proof passes.

Stop when the approval-mutation milestone is complete and verified. Report exact files changed, commands run, observed outputs, and any unrelated pre-existing failures. Do not silently continue to package systems, evaluation pipelines, or world rendering.
