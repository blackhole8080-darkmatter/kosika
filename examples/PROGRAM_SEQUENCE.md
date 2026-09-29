# Kosika program sequence

This is the staged example path for Kosika's vision: typed, local-first, capability-secure, auditable AI workflows.

## Runnable programs

| Stage | Program | File | Status | Demonstrates |
|---|---|---|---|---|
| 1 | Hello World | `hello_world.kos` | Done | A typed record, pure function, workflow, run, and replay |
| 2 | Typed Input/Output | `typed_input_output.kos` | Done | A caller-provided record, validation, pure transformation, and typed result |
| 3 | Research Assistant | `research_assistant.kos` | Done | A read-only tool, declared model, citations, fixture-backed execution, and trace replay |
| 4 | Agent Factory | `agent_factory.kos` | Done | A model-proposed agent declaration, approval pause, pinned MCP source writer, durable resume, and replay |
| 5 | Approval-Protected Mutation | `approval_mutation.kos` | Done | A typed external effect, durable journal, exact action review, single execution, and replay |
| 6 | Evaluated Research Assistant | `research_assistant.kos` plus evaluation fixtures | Done | Fresh evaluation, expected records, safety assertions, per-case traces, and regression reports |

## Supporting artifacts

| Stage | Artifact | File or directory | Purpose |
|---|---|---|---|
| 2 | Typed input | `typed_input.json` | Deterministic `EvaluationInput` supplied to `main` |
| 3 | Research input | `research_input.json` | Deterministic workflow input |
| 3 | Model fixture | `research_model_answers.json` | Offline model responses; no provider call |
| 3 | Read-only tool fixture | `research_documents/` | Bounded source material for the research workflow |
| 4 | Factory input | `agent_factory_input.json` | Typed request for the proposed agent |
| 4 | Model fixture | `agent_factory_models.json` | Deterministic `AgentSpec` returned by the architect |
| 4 | MCP manifest | `agent_factory_manifest.json` | Pinned source-writer tool contract |
| 4 | Approval record | `agent_factory_approval.json` | Explicit reviewer decision for one exact source-writing action |
| 5 | Mutation input | `approval_mutation_input.json` | Typed change request supplied to the mutation workflow |
| 5 | MCP server | `approval_mutation_mcp_server.py` | Deterministic local effect writing one constrained JSON sink |
| 5 | MCP manifest | `approval_mutation_manifest.json` | Pinned mutation tool contract |
| 5 | Approval record | `approval_mutation_approval.json` | Explicit reviewer decision for one exact mutation action |
| 6 | Evaluation cases | `research_evaluations.json` | Expected outputs and safety checks |
| 6 | MCP manifest | `research_documents_manifest.json` | Pinned read-only search contract for evaluation runs |
| 7 | World view | `worlds/forest_knowledge_reserve.json` | Live visualization of real workflow and agent execution |

## Execution contract

Each program should support this progression before the next stage starts:

```text
check → run with deterministic inputs → inspect trace → replay → evaluate
```

Programs must not hide provider access, tool authority, mutation, retries, or approval requirements. Supporting fixtures must remain deterministic and local.

The next slice should define reusable local Kosika packages without adding live registry dependencies.
