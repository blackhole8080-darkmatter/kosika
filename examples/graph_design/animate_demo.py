"""Animated graph demo: Kosika design + animation + motion graphics.

Shows three capabilities:
1. Design: deterministic graph layout (layered/circular/grid/force)
2. Animation: keyed curves for staggered node entry and pulse
3. Motion graphics: a token flowing along edges via animateMotion

Run:  python examples/graph_design/animate_demo.py
Out:  examples/graph_design/out/animated.html
"""

from __future__ import annotations

import json
from pathlib import Path

from kosika.layout import (
    DEMO_GRAPH,
    compute_layout,
    KIND_COLORS,
    LINE as LINK_COLOR,
    NODE_R,
    route_points,
    STRATEGIES,
)
from kosika.motion import check_scene, to_html

WIDTH, HEIGHT = 960, 620
MARGIN = 78


def entry_moves(node_id: str, delay: float) -> list[dict]:
    """A node fades in and scales up, staggered by delay."""

    return [
        {"shape": node_id, "what": "fade", "to": 0, "start": 0, "seconds": 0,
         "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": []},
        {"shape": node_id, "what": "fade", "keys": [{"at": 0, "to": 0}, {"at": 1, "to": 1}],
         "start": delay, "seconds": 0.6, "ease": "both", "repeat": 1, "color": "", "path": ""},
        {"shape": node_id, "what": "size", "keys": [{"at": 0, "to": 0}, {"at": 1, "to": NODE_R * 2}],
         "start": delay, "seconds": 0.6, "ease": "back", "repeat": 1, "color": "", "path": ""},
    ]


def label_entry_moves(node_id: str, delay: float) -> list[dict]:
    """The label fades in slightly after the node."""

    return [
        {"shape": f"{node_id}__label", "what": "fade", "to": 0, "start": 0, "seconds": 0,
         "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": []},
        {"shape": f"{node_id}__label", "what": "fade",
         "keys": [{"at": 0, "to": 0}, {"at": 1, "to": 1}],
         "start": delay + 0.3, "seconds": 0.5, "ease": "both",
         "repeat": 1, "color": "", "path": ""},
    ]


def pulse_moves(node_id: str, start: float) -> list[dict]:
    """A node pulses (scale 1 -> 1.2 -> 1) to mark it as active."""

    base = NODE_R * 2
    return [
        {"shape": node_id, "what": "size", "keys": [
            {"at": 0, "to": base}, {"at": 0.5, "to": base * 1.25}, {"at": 1, "to": base}],
         "start": start, "seconds": 1.0, "ease": "both", "repeat": 0,
         "color": "", "path": ""},
    ]


def token_moves(token_id: str, link_id: str, start: float, seconds: float) -> list[dict]:
    """A token flows along a link's path."""

    return [
        {"shape": token_id, "what": "fade", "to": 0, "start": 0, "seconds": 0,
         "ease": "linear", "repeat": 1, "color": "", "path": ""},
        {"shape": token_id, "what": "fade", "to": 1,
         "start": start, "seconds": 0.2, "ease": "out", "repeat": 1,
         "color": "", "path": ""},
        {"shape": token_id, "what": "path", "path": link_id,
         "start": start + 0.2, "seconds": seconds, "ease": "linear", "repeat": 1,
         "color": "", "to": 0, "keys": []},
        {"shape": token_id, "what": "fade", "to": 0,
         "start": start + 0.2 + seconds, "seconds": 0.2, "ease": "in",
         "repeat": 1, "color": "", "path": ""},
    ]


def build_animated_scene(layout, graph, strategy: str) -> dict:
    """Build a motion scene that draws the graph, then animates it."""

    nodes, edges = graph
    shapes: list[dict] = []
    moves: list[dict] = []

    # Nodes (start hidden, fade in staggered)
    for index, (node_id, label, kind) in enumerate(nodes):
        x, y = layout.positions[node_id]
        colour = KIND_COLORS.get(kind, "#26332b")
        delay = index * 0.15
        shapes.append({
            "id": node_id, "kind": "circle", "x": x, "y": y,
            "size": NODE_R * 2, "color": colour, "text": label,
            "source": "", "target": "", "points": [], "style": "straight",
        })
        shapes.append({
            "id": f"{node_id}__label", "kind": "text", "x": x, "y": y + NODE_R + 10,
            "size": 11, "color": "#26332b", "text": label,
            "source": "", "target": "", "points": [], "style": "straight",
        })
        moves.extend(entry_moves(node_id, delay))
        moves.extend(label_entry_moves(node_id, delay))

    # Links (drawn from the start, faded in)
    for index, (source, target, _kind) in enumerate(edges):
        link_id = f"link{index}_{source}_{target}".replace(" ", "_")
        points = route_points(layout.positions[source], layout.positions[target], "straight")
        shapes.append({
            "id": link_id, "kind": "link", "x": 0, "y": 0, "size": 1.6,
            "color": LINK_COLOR, "text": "", "source": source, "target": target,
            "points": [list(p) for p in points], "style": "straight",
        })
        moves.append({
            "shape": link_id, "what": "fade", "keys": [{"at": 0, "to": 0}, {"at": 1, "to": 1}],
            "start": len(nodes) * 0.15 + index * 0.08, "seconds": 0.5,
            "ease": "both", "repeat": 1, "color": "", "path": "",
        })

    # The travelling token
    shapes.append({
        "id": "token", "kind": "circle", "x": 0, "y": 0,
        "size": 10, "color": "#286b5d", "text": "",
        "source": "", "target": "", "points": [], "style": "straight",
    })

    # Token travels along each edge in sequence
    token_start = len(nodes) * 0.15 + len(edges) * 0.08 + 0.5
    for index, (source, target, _kind) in enumerate(edges):
        link_id = f"link{index}_{source}_{target}".replace(" ", "_")
        moves.extend(token_moves("token", link_id, token_start + index * 1.2, 0.9))

    # Pulse the final node
    last_node = nodes[-1][0]
    moves.extend(pulse_moves(last_node, token_start + len(edges) * 1.2 + 0.5))

    scene = {
        "width": WIDTH, "height": HEIGHT, "seconds": 20.0,
        "background": "#f6f7f3",
        "shapes": shapes,
        "moves": moves,
    }
    problems = check_scene(scene)
    if problems:
        raise ValueError(f"Animated scene cannot play: {problems[0]}")
    return scene


def main() -> None:
    out = Path(__file__).resolve().parent / "out"
    out.mkdir(parents=True, exist_ok=True)

    graph = DEMO_GRAPH
    for strategy_name in STRATEGIES:
        layout = compute_layout(graph, strategy=strategy_name)
        scene = build_animated_scene(layout, graph, strategy_name)
        html = to_html(scene, title=f"Kosika animated graph — {strategy_name}")
        (out / f"animated-{strategy_name}.html").write_text(html, encoding="utf-8")
        print(f"animated-{strategy_name}.html: {len(scene['moves'])} moves, {len(scene['shapes'])} shapes")

    # Also write the default (smart choice) as animated.html
    smart = "layered"  # choose() would say layered for this DAG
    layout = compute_layout(graph, strategy=smart)
    scene = build_animated_scene(layout, graph, smart)
    html = to_html(scene, title="Kosika animated graph")
    (out / "animated.html").write_text(html, encoding="utf-8")
    print(f"animated.html written (smart choice: {smart})")


if __name__ == "__main__":
    main()
