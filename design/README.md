# Kosika design directory

The `design/` directory contains deterministic visual design manifests for Kosika programs and
networks. Design manifests are separate from executable source and network manifests:

- `.kos` source declares records, tools, agents, and workflows.
- `network/` manifests declare runtimes, models, devices, memory graphs, tabs, and virtual spaces.
- `design/` manifests declare visual nodes, typed edges, canvas positions, views, and styles.

The design layer is intentionally vector-first. The same design manifest can render as structured
JSON, text, Mermaid, or SVG without asking a model to infer the program structure.

## Commands

Generate a design from a Kosika program:

```text
python -m kosika.cli design generate examples/test_host_live.kos \
  --id test-host.design \
  --name "Test Host Agent Workflow" \
  --network network/example-network.json \
  --output design/test-host.json
```

Validate and render it:

```text
python -m kosika.cli design validate design/test-host.json
python -m kosika.cli design show design/test-host.json --format text
python -m kosika.cli design show design/test-host.json --format mermaid
python -m kosika.cli design show design/test-host.json --format svg --view workflow --output design/test-host.svg
python -m kosika.cli design show design/test-host.json --format svg --view network --output design/test-host-network.svg
python -m kosika.cli design show design/test-host.json --format svg --view mindmap --output design/test-host-mindmap.svg
```

The workflow view emphasizes clear execution order and approvals. The network view emphasizes
systems and connections. The mindmap view uses the same graph but lays it out from a deterministic
root so humans can understand code structure and dependencies.

SVG output is a portable vector artifact with colorful node categories, curved Bezier arrows,
arrowheads, readable edge labels, swimlane-style panels, and a legend. It can be opened by
browsers, design tools, and later Kosika virtual-space renderers.
