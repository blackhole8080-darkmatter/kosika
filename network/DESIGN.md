# Kosika network design direction

The network layer is intended to become the control plane for AI systems rather than a bag of
connection scripts.

## Planned layers

1. **Directory** — deterministic manifests, identity, ownership, topology, and policy.
2. **Adapters** — explicit local-device, model-provider, memory-store, virtual-space, and
   Kosika-to-Kosika transports.
3. **Approval** — capability checks, human approval for proposals or execution, and bounded scopes.
4. **Telemetry** — normalized events shared with the existing Kosika/Synapse bridge and SSE stream.
5. **Replay** — offline network traces that reproduce observations and decisions without reconnecting.
6. **Views** — network topology, code mindmaps, memory graphs, timelines, and virtual-space layouts.

## Safety invariants

- A manifest describes capabilities; it does not grant them by itself.
- Remote access is disabled by default.
- Execute links require an explicit approval marker and later runtime approval evidence.
- Secrets never live in manifests.
- Unknown nodes, modules, tabs, links, or memory references fail validation.
- Discovery and health checks must remain read-only until a separate execution contract exists.
