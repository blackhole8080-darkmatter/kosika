# Kosika design layer

The design layer turns checked Kosika structure into an inspectable visual model.

## Node kinds

- `workflow`: an agent workflow entry point.
- `stage`: a bounded workflow step.
- `agent`: a declared reasoning component.
- `tool`: an explicit capability boundary.
- `record`: typed data exchanged by a tool or workflow.
- `module`: a bounded source module with content identity and declaration ownership.
- `approval`: a required authority checkpoint.
- `network`: a declared Kosika network context.
- `network_node`: a runtime, model, device, memory graph, or virtual space in that network.

## Edge kinds

Edges describe structure and authority rather than model guesses:

- `contains`, `calls`, `invokes`, `outputs`, `uses`
- `imports` for direct module dependencies; module-to-declaration `contains` edges express ownership.
- `requires_approval`
- `network_context`, `connects`

## Vector direction

Every node has an explicit position and size. SVG rendering uses these values directly. A later
layout engine may propose positions, but the checked design manifest remains authoritative and
replayable. This lets developers rearrange a network visually without changing program behavior.

## Human-oriented views

The same graph supports three visual views:

- **Workflow** — left-to-right stages, agents, tools, records, approvals, and network context.
- **Network** — runtimes, models, devices, memory graphs, and virtual spaces.
- **Mindmap** — a deterministic layered expansion from a workflow or network root.

SVG uses curved Bezier edges with arrowheads, edge labels, category colors, soft panels, shadows,
and a legend. These are presentation details only; node IDs and typed edges remain authoritative.

## Workflow as code and code as workflow

Kosika should support both directions:

1. Developers write `.kos` source and Kosika generates the visual workflow.
2. Developers design a validated graph and Kosika can later generate a safe `.kos` proposal.

The second direction must remain proposal-only until the generated source passes parsing, static
capability checks, schema validation, and explicit approval. This gives Kosika a visual programming
surface without making diagrams an unverified execution authority.

Future design modules can add animation timelines, vector fields, matrix transforms, Venn diagrams,
spatial scenes, and memory-graph layouts while preserving the same typed node/edge model.
