"""Kosika brains: a small skill orchestrator built on top of the existing
Kosika skills, memory, and network subsystems.

Maths is treated as a skill that sits *on top of* the foundational
kosika-maths substrate: the brain decides when and how to use maths
(graphs, memory, paths) to solve programs, render designs, and reason.
Networks provide cross-device manifests the brain can attach to.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class BrainSkill:
    name: str
    description: str
    run: Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class Brain:
    skills: dict[str, BrainSkill] = field(default_factory=dict)
    memory: dict[str, Any] = field(default_factory=dict)

    def register(self, skill: BrainSkill) -> None:
        self.skills[skill.name] = skill

    def remember(self, key: str, value: Any) -> None:
        self.memory[key] = value

    def recall(self, key: str) -> Any:
        return self.memory.get(key)

    def invoke(self, skill_name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if skill_name not in self.skills:
            raise KeyError(f"unknown skill: {skill_name}")
        return self.skills[skill_name].run(payload or {})


def make_maths_skill() -> BrainSkill:
    def run(payload: dict[str, Any]) -> dict[str, Any]:
        from kosika.checker import check
        from kosika.parser import parse
        from kosika.runtime import run as kos_run

        src = payload.get("source")
        if not src:
            src = '''
record Node { id: Text label: Text kind: Text }
record Edge { from: Text to: Text rel: Text }
record Graph { nodes: Node[] edges: Edge[] }
fn graph() -> Graph { return { nodes: [], edges: [] } }
fn add_node(g: Graph, id: Text, kind: Text, label: Text) -> Graph {
  for n in g.nodes { if n.id == id { return g } }
  return { nodes: g.nodes.concat([{id: id, label: label, kind: kind}]), edges: g.edges }
}
fn add_edge(g: Graph, from: Text, to: Text, rel: Text) -> Graph {
  return { nodes: g.nodes, edges: g.edges.concat([{from: from, to: to, rel: rel}]) }
}
fn node_count(g: Graph) -> Number { return g.nodes.length() }
fn path(g: Graph, start: Text, goal: Text) -> Text[] {
  if start == goal { return [start] }
  var visited: Text[] = [start]
  var frontier: Text[] = [start]
  var parent: Text{} = {}
  var found = false
  while frontier.length() > 0 and not found {
    let cur: Text = frontier[0]
    frontier = frontier.slice(1, frontier.length())
    if cur == goal { found = true }
    else {
      for e in g.edges {
        if e.from == cur {
          if not visited.contains(e.to) {
            visited = visited.append(e.to)
            parent = parent.set(e.to, cur)
            frontier = frontier.append(e.to)
          }
        }
      }
    }
  }
  if not visited.contains(goal) { return [] }
  var out: Text[] = [goal]
  var cur: Text = goal
  while cur != start {
    cur = parent.get(cur).or(start)
    out = [cur].concat(out)
  }
  return out
}
record In { }
record Out { n: Number p: Text[] }
fn build() -> Out {
  let g = add_edge(add_node(add_node(graph(), "a", "concept", "a"), "b", "concept", "b"), "a", "b", "r")
  return { n: node_count(g), p: path(g, "a", "b") }
}
workflow main(input: In) -> Out { out = build() }
'''
        program = parse(src)
        check(program)
        inputs = payload.get("inputs", {"input": {}})
        result = kos_run(program, "main", None, inputs=inputs)
        return {"maths_result": result}

    return BrainSkill("maths", "Foundational maths (graphs, memory, paths) as a skill", run)


def make_network_skill() -> BrainSkill:
    def run(payload: dict[str, Any]) -> dict[str, Any]:
        from kosika.network import network_to_dict, new_network_manifest, render_network

        net_id = payload.get("network_id", "kosika-brain-net")
        name = payload.get("name", "Kosika Brain Network")
        manifest = new_network_manifest(net_id, name)
        return {
            "network": network_to_dict(manifest),
            "rendered": render_network(manifest, "text"),
        }

    return BrainSkill("networks", "Cross-device network manifest skill", run)


def build_default_brain() -> Brain:
    brain = Brain()
    brain.register(make_maths_skill())
    brain.register(make_network_skill())
    return brain
