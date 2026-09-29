# Kosika Language v0.1

Kosika is a local-first, capability-secure language and runtime for AI applications, infrastructure, networks, devices, databases, and replayable workflows.

## Naming

- Kosika Language: source syntax and semantics, including typed tool inputs, variables, structured values, field access, and step-to-step data flow
- Kosika Core: interpreter/runtime
- Kosika Bridge: MCP and external capability adapters; v0.1 includes a local stdio client with pinned manifests and schema validation
- Kosika Agent Loop: provider-neutral model requests, declared-tool exposure, schema checks, step limits, and plan-hash approvals
- kosika: command-line interface, including `kosika agent` for local model execution
- Kosipack: future package manager
- Kosika Design: deterministic network, workflow, and code-mindmap views rendered from checked source and manifests

The first implementation is Python-first. Rust or Go may be evaluated later for a native runtime.

Kosika's network and design layer keeps declarative network topology, agent workflows, model/MCP boundaries,
memory graphs, and visual explanations separate from execution authority. `kosika network` validates and
indexes network manifests; `kosika design` generates and renders bounded SVG, Mermaid, JSON, or text views.
The renderer is structural and deterministic: assistant or model output cannot invent code relationships.
