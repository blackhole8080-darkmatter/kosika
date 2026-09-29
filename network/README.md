# Kosika network directory

This directory stores declarative Kosika network manifests. A manifest describes a network without
connecting to or mutating any target.

## Manifest sections

- `network`: identity and default access policy.
- `developers`: owners and collaborators.
- `modules`: runtime, model gateway, memory, telemetry, connector, and virtual-space modules.
- `nodes`: Kosika runtimes, models, devices, memory graphs, services, and virtual spaces.
- `tabs`: named views for topology, timelines, mindmaps, workspaces, and memory graphs.
- `links`: typed connections with `observe`, `propose`, or `execute` access.
- `memoryGraphs`: bounded nodes and edges for project knowledge or system state.

Use external secret references in future versions. Credentials, tokens, passwords, and API keys are
rejected from manifests by design.

## Commands

From the project root:

```text
python -m kosika.cli network init network/my-network.json \
  --id my.network \
  --name "My Kosika Network"
python -m kosika.cli network validate network/example-network.json
python -m kosika.cli network show network/example-network.json --format text
python -m kosika.cli network show network/example-network.json --format mermaid
python -m kosika.cli network index network --format text
```

`network index` is deterministic and fails closed when a JSON manifest is invalid or when two files
reuse a network id. Use `--recursive` for nested network directories.

The current slice is descriptive only. It does not discover devices, open sockets, invoke models,
read memory stores, or connect Kosika systems. Those operations will require declared adapters,
pinned capabilities, explicit approvals, and replayable telemetry in later runtime slices.
