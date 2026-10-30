"""Deterministic graph layouts that feed Kosika scenes.

Positions and edge routes are a pure function of the graph and the chosen
strategy: the same input always yields the same picture, which is what lets
a layout be checked, hashed and replayed.  Outputs are :mod:`kosika.motion`
scene dicts, so :func:`to_svg` / :func:`to_html` can render them and the
:mod:`kosika.std.scene` vocabulary can express them.

Run the demo:  python -m kosika.layout
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from .motion import KINDS, LINK_STYLES, check_scene, to_svg

WIDTH, HEIGHT = 960, 620
MARGIN = 78
NODE_R = 23

PAPER = "#f6f7f3"
INK = "#26332b"
GREEN = "#286b5d"
MUTED = "#7d8a78"
LINE = "#b9c2b2"

KIND_COLORS = {
    "workflow": GREEN,
    "function": "#3b5bdb",
    "model": "#b8860b",
    "tool": MUTED,
    "record": "#8b5cf6",
}

# A graph is just pairs, like Kosika's relation matrices.
Graph = tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]


# ------------------------------------------------------------------ strategies


def _neighbours(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, list[str]]:
    found = {name: [] for name in nodes}
    for source, target in edges:
        found.setdefault(source, []).append(target)
    return found


def _pairs(edges: list[tuple[str, str, str]]) -> list[tuple[str, str]]:
    """Drop the edge kind, leaving just (source, target) pairs."""

    return [(source, target) for source, target, _kind in edges]


def _is_acyclic(nodes: list[str], edges: list[tuple[str, str, str]]) -> bool:
    pair_edges = _pairs(edges)
    indegree = {name: 0 for name in nodes}
    for _, target in pair_edges:
        indegree[target] = indegree.get(target, 0) + 1
    ready = sorted(name for name, count in indegree.items() if count == 0)
    seen = 0
    while ready:
        here = ready.pop(0)
        seen += 1
        for there in _neighbours(nodes, pair_edges).get(here, []):
            indegree[there] -= 1
            if indegree[there] == 0:
                ready.append(there)
                ready.sort()
    return seen == len(nodes)


def _layers(nodes: list[str], edges: list[tuple[str, str, str]]) -> dict[str, int]:
    """Longest-path layering; cycles are broken by declaration order."""

    pair_edges = _pairs(edges)
    outgoing = _neighbours(nodes, pair_edges)
    indegree = {name: 0 for name in nodes}
    for _, target in pair_edges:
        indegree[target] = indegree.get(target, 0) + 1
    ready = sorted(name for name, count in indegree.items() if count == 0)
    order: list[str] = []
    while ready:
        here = ready.pop(0)
        order.append(here)
        for there in outgoing.get(here, []):
            indegree[there] -= 1
            if indegree[there] == 0:
                ready.append(there)
                ready.sort()
    ordered = order + [name for name in nodes if name not in order]
    layer = {name: 0 for name in nodes}
    for here in ordered:
        for there in outgoing.get(here, []):
            if layer[there] < layer[here] + 1:
                layer[there] = layer[here] + 1
    return layer


def _barycentric(rows: dict[int, list[str]], edges: list[tuple[str, str]], sweeps: int = 4) -> None:
    """Reorder each layer so a vertex sits under the average of its neighbours.

    Sugiyama crossing reduction: only permutes within a layer, so every edge
    still points downwards and the layering stays valid. Ties break on id,
    so the result is deterministic.
    """

    pair_edges = _pairs(edges)
    layer_of = {name: index for index, row in rows.items() for name in row}
    preds: dict[str, list[str]] = {name: [] for name in layer_of}
    succs: dict[str, list[str]] = {name: [] for name in layer_of}
    for source, target in pair_edges:
        if source in layer_of and target in layer_of:
            succs[source].append(target)
            preds[target].append(source)

    column = {name: index for index, row in rows.items() for index, name in enumerate(row)}
    layers = sorted(rows)

    def mean(anchors: list[str], fallback: int) -> float:
        if not anchors:
            return float(fallback)
        return sum(column[anchor] for anchor in anchors) / len(anchors)

    for _ in range(sweeps):
        for index in layers[1:]:
            rows[index].sort(key=lambda name: (mean(preds[name], column[name]), name))
            for place, name in enumerate(rows[index]):
                column[name] = place
        for index in reversed(layers[:-1]):
            rows[index].sort(key=lambda name: (mean(succs[name], column[name]), name))
            for place, name in enumerate(rows[index]):
                column[name] = place


def _ring_order(nodes: list[str], edges: list[tuple[str, str, str]]) -> list[str]:
    """Breadth-first from the busiest vertex; neighbours end up adjacent."""

    pair_edges = _pairs(edges)
    names = sorted(nodes)
    adjacent: dict[str, set[str]] = {name: set() for name in names}
    for source, target in pair_edges:
        if source in adjacent and target in adjacent:
            adjacent[source].add(target)
            adjacent[target].add(source)

    order: list[str] = []
    seen: set[str] = set()
    while len(order) < len(names):
        start = min(
            (n for n in names if n not in seen),
            key=lambda n: (-len(adjacent[n]), n),
        )
        seen.add(start)
        queue = [start]
        while queue:
            here = queue.pop(0)
            order.append(here)
            for there in sorted(adjacent[here]):
                if there not in seen:
                    seen.add(there)
                    queue.append(there)
    return order


def _crossings(order: list[str], edges: list[tuple[str, str, str]]) -> int:
    """Count edge crossings when vertices sit on a ring in this order."""

    pair_edges = _pairs(edges)
    place = {name: index for index, name in enumerate(order)}
    chords = []
    for source, target in pair_edges:
        if source in place and target in place and source != target:
            chords.append((min(place[source], place[target]), max(place[source], place[target])))
    total = 0
    for index, (a, b) in enumerate(chords):
        for c, d in chords[index + 1 :]:
            if len({a, b, c, d}) < 4:
                continue
            if (a < c < b < d) or (c < a < d < b):
                total += 1
    return total


def _untangle(order: list[str], edges: list[tuple[str, str, str]], limit: int = 400) -> list[str]:
    """Hill-climb the ring order by swapping neighbours while crossings drop."""

    best = list(order)
    best_score = _crossings(best, edges)
    steps = 0
    while steps < limit:
        improved = False
        for index in range(len(best)):
            other = (index + 1) % len(best)
            if index == other:
                continue
            trial = list(best)
            trial[index], trial[other] = trial[other], trial[index]
            score = _crossings(trial, edges)
            if score < best_score:
                best, best_score = trial, score
                improved = True
                steps += 1
                break
        if not improved:
            break
    return best


def _fit(points: list[tuple[float, float]], *, padding: float = 34.0) -> list[tuple[float, float]]:
    """Uniformly scale and centre a layout inside the drawing box.

    A strategy's own shape is preserved: a circle stays a circle, layers stay
    square to each other.  Only the enclosing box changes.  ``padding`` keeps
    labels from touching the frame.
    """

    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    span_x = right - left
    span_y = bottom - top
    box_w = WIDTH - 2 * MARGIN
    box_h = HEIGHT - 2 * MARGIN - padding
    scale = min(
        box_w / span_x if span_x > 1e-9 else float("inf"),
        box_h / span_y if span_y > 1e-9 else float("inf"),
    )
    if scale == float("inf"):
        scale = 1.0
    return [
        (
            MARGIN + (x - left) * scale + (box_w - span_x * scale) / 2,
            MARGIN + padding + (y - top) * scale + (box_h - span_y * scale) / 2,
        )
        for x, y in points
    ]


def layered(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, tuple[float, float]]:
    """Top-down layers: sources at the top, dependencies below."""

    layer = _layers(nodes, edges)
    rows: dict[int, list[str]] = {}
    for name in sorted(layer, key=lambda key: (layer[key], key)):
        rows.setdefault(layer[name], []).append(name)
    _barycentric(rows, edges)

    points: list[tuple[float, float]] = []
    order: list[str] = []
    for index in sorted(rows):
        row = rows[index]
        for column, name in enumerate(row):
            order.append(name)
            points.append(((column - (len(row) - 1) / 2) * 120.0, index * 120.0))
    return dict(zip(order, _fit(points)))


def circular(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, tuple[float, float]]:
    """Every vertex on one circle, ordered to cross as few edges as possible."""

    names = _untangle(_ring_order(nodes, edges), edges)
    count = max(len(names), 1)
    points = [
        (math.cos(2 * math.pi * i / count), math.sin(2 * math.pi * i / count))
        for i in range(count)
    ]
    return dict(zip(names, _fit(points)))


def grid(nodes: list[str], edges: list[tuple[str, str]]) -> dict[str, tuple[float, float]]:
    """A near-square grid in breadth-first order, so neighbours stay close."""

    names = _ring_order(nodes, edges)
    columns = max(1, math.ceil(math.sqrt(len(names))))
    points = [
        (index % columns * 100.0, index // columns * 100.0)
        for index in range(len(names))
    ]
    return dict(zip(names, _fit(points)))


def force(nodes: list[str], edges: list[tuple[str, str, str]], rounds: int = 240) -> dict[str, tuple[float, float]]:
    """Fruchterman–Reingold started on a circle: deterministic, no dice."""

    pair_edges = _pairs(edges)
    names = sorted(nodes)
    count = max(len(names), 1)
    index = {name: i for i, name in enumerate(names)}
    ideal = math.sqrt(((WIDTH - 2 * MARGIN) * (HEIGHT - 2 * MARGIN)) / count)
    pos = [
        (
            (WIDTH / 2) + 240 * math.cos(2 * math.pi * i / count),
            (HEIGHT / 2) + 240 * math.sin(2 * math.pi * i / count),
        )
        for i in range(count)
    ]
    links = [
        (index[source], index[target])
        for source, target in pair_edges
        if source in index and target in index
    ]
    for step in range(rounds):
        heat = ideal * (1.0 - step / rounds) * 0.9
        disp = [(0.0, 0.0) for _ in names]
        for i in range(count):
            for j in range(i + 1, count):
                dx = pos[i][0] - pos[j][0]
                dy = pos[i][1] - pos[j][1]
                distance = math.hypot(dx, dy) or 0.01
                push = (ideal * ideal) / distance
                ux, uy = dx / distance, dy / distance
                disp[i] = (disp[i][0] + ux * push, disp[i][1] + uy * push)
                disp[j] = (disp[j][0] - ux * push, disp[j][1] - uy * push)
        for a, b in links:
            dx = pos[a][0] - pos[b][0]
            dy = pos[a][1] - pos[b][1]
            distance = math.hypot(dx, dy) or 0.01
            pull = (distance * distance) / ideal
            ux, uy = dx / distance, dy / distance
            disp[a] = (disp[a][0] - ux * pull, disp[a][1] - uy * pull)
            disp[b] = (disp[b][0] + ux * pull, disp[b][1] + uy * pull)
        for i in range(count):
            dx, dy = disp[i]
            length = math.hypot(dx, dy) or 0.01
            move = min(length, heat)
            pos[i] = (
                pos[i][0] + dx / length * move,
                pos[i][1] + dy / length * move,
            )
    return dict(zip(names, _fit(pos)))


STRATEGIES = {
    "layered": layered,
    "circular": circular,
    "grid": grid,
    "force": force,
}


def choose(nodes: list[str], edges: list[tuple[str, str]]) -> str:
    """The smart default: a DAG wants layers, a small ring wants a circle."""

    if _is_acyclic(nodes, edges) and len(nodes) >= 4:
        return "layered"
    if len(nodes) <= 6:
        return "circular"
    if len(edges) > 2 * len(nodes):
        return "grid"
    return "force"


def explain_choice(nodes: list[str], edges: list[tuple[str, str]]) -> str:
    """The reason behind :func:`choose`, in words."""

    if _is_acyclic(nodes, edges) and len(nodes) >= 4:
        return "acyclic, so the layers of dependency are the honest picture"
    if len(nodes) <= 6:
        return "few vertices, so one ring reads best"
    if len(edges) > 2 * len(nodes):
        return "dense, so a grid keeps every edge short"
    return "cyclic and mixed, so a deterministic force layout settles it"


# ------------------------------------------------------------------ edges


def _trim(
    start: tuple[float, float],
    end: tuple[float, float],
    radius: float = NODE_R + 2,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Pull both ends back to the circle's rim, so arrowheads land on it."""

    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    return (
        (start[0] + ux * radius, start[1] + uy * radius),
        (end[0] - ux * radius, end[1] - uy * radius),
    )


def route_points(
    a: tuple[float, float],
    b: tuple[float, float],
    style: str = "straight",
    bend: float = 0.16,
) -> list[tuple[float, float]]:
    """The control points for one edge, already trimmed to the node rim."""

    (ax, ay), (bx, by) = _trim(a, b)
    if style == "straight":
        return [(ax, ay), (bx, by)]
    if style == "curved":
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy) or 1.0
        px, py = -dy / length, dx / length
        offset = length * bend
        return [
            (ax, ay),
            (ax + dx * 0.33 + px * offset, ay + dy * 0.33 + py * offset),
            (ax + dx * 0.66 + px * offset, ay + dy * 0.66 + py * offset),
            (bx, by),
        ]
    if style == "orthogonal":
        if abs(bx - ax) >= abs(by - ay):
            mid = (ax + bx) / 2
            return [(ax, ay), (mid, ay), (mid, by), (bx, by)]
        mid = (ay + by) / 2
        return [(ax, ay), (ax, mid), (bx, mid), (bx, by)]
    raise ValueError(f"unknown edge style {style!r}")


# ------------------------------------------------------------------ layout result


@dataclass(frozen=True)
class Layout:
    strategy: str
    positions: dict[str, tuple[float, float]]
    routes: dict[str, list[tuple[float, float]]]  # edge key -> points
    styles: dict[str, str]  # edge key -> style
    digest: str

    def edge_key(self, source: str, target: str, index: int) -> str:
        return f"{index}:{source}->{target}"


def layout_digest(positions: dict[str, tuple[float, float]], routes: dict[str, list[tuple[float, float]]]) -> str:
    """A short fingerprint of a layout: same graph, same strategy, same hash."""

    payload = ";".join(
        f"{name}:{positions[name][0]:.1f},{positions[name][1]:.1f}"
        for name in sorted(positions)
    )
    payload += "|" + ";".join(
        f"{key}:{','.join(f'{x:.1f},{y:.1f}' for x, y in pts)}"
        for key, pts in sorted(routes.items())
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _link_id(index: int, source: str, target: str) -> str:
    """A valid shape id for an edge: letters, digits, underscore only."""

    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in f"{source}->{target}")
    return f"link{index}_{safe}"


def compute_layout(
    graph: Graph,
    *,
    strategy: str = "auto",
    width: int = WIDTH,
    height: int = HEIGHT,
) -> Layout:
    """Compute positions and routes for every vertex and edge."""

    nodes, edges = graph
    names = [node[0] for node in nodes]
    if strategy == "auto":
        strategy = choose(names, edges)

    fn = STRATEGIES[strategy]
    positions = fn(names, edges)

    routes: dict[str, list[tuple[float, float]]] = {}
    styles: dict[str, str] = {}
    for index, (source, target, _kind) in enumerate(edges):
        key = _link_id(index, source, target)
        style = "curved" if strategy == "layered" else "straight"
        routes[key] = route_points(
            positions[source], positions[target], style
        )
        styles[key] = style

    return Layout(
        strategy=strategy,
        positions=positions,
        routes=routes,
        styles=styles,
        digest=layout_digest(positions, routes),
    )


# ------------------------------------------------------------------ motion scene


def scene_of(layout: Layout, graph: Graph, *, width: int = WIDTH, height: int = HEIGHT, seconds: float = 1.0) -> dict:
    """A :mod:`kosika.motion` scene for this layout: nodes as circles, edges as links.

    The scene passes :func:`kosika.motion.check_scene`, so :func:`to_svg` and
    :func:`to_html` can render it, and :mod:`kosika.std.scene` can express it.
    """

    nodes, edges = graph
    shapes: list[dict] = []
    moves: list[dict] = []

    for node_id, label, kind in nodes:
        x, y = layout.positions[node_id]
        colour = KIND_COLORS.get(kind, INK)
        shapes.append(
            {
                "id": node_id,
                "kind": "circle",
                "x": x,
                "y": y,
                "size": NODE_R * 2,
                "color": colour,
                "text": label,
                "source": "",
                "target": "",
                "points": [],
                "style": "straight",
            }
        )
        shapes.append(
            {
                "id": f"{node_id}__label",
                "kind": "text",
                "x": x,
                "y": y + NODE_R + 10,
                "size": 11,
                "color": INK,
                "text": label,
                "source": "",
                "target": "",
                "points": [],
                "style": "straight",
            }
        )

    for index, (source, target, _kind) in enumerate(edges):
        key = _link_id(index, source, target)
        shapes.append(
            {
                "id": key,
                "kind": "link",
                "x": 0,
                "y": 0,
                "size": 1.6,
                "color": LINE,
                "text": "",
                "source": source,
                "target": target,
                "points": [list(p) for p in layout.routes[key]],
                "style": layout.styles[key],
            }
        )

    scene = {
        "width": width,
        "height": height,
        "seconds": seconds,
        "background": PAPER,
        "shapes": shapes,
        "moves": moves,
    }
    problems = check_scene(scene)
    if problems:
        raise ValueError("The layout scene cannot be played: " + problems[0])
    return scene


def render_svg(layout: Layout, graph: Graph, *, width: int = WIDTH, height: int = HEIGHT, seconds: float = 1.0) -> str:
    """Render one deterministic SVG picture of the graph."""

    scene = scene_of(layout, graph, width=width, height=height, seconds=seconds)
    return to_svg(scene)


# ------------------------------------------------------------------ demo graph


DEMO_NODES = [
    ("main", "main", "workflow"),
    ("research", "research", "function"),
    ("total", "total", "function"),
    ("search", "search", "tool"),
    ("writer", "writer", "model"),
    ("Answer", "Answer", "record"),
    ("Question", "Question", "record"),
    ("Quote", "Quote", "record"),
    ("Hit", "Hit", "record"),
    ("sum", "sum", "function"),
]

DEMO_EDGES = [
    ("main", "research", "call"),
    ("main", "total", "call"),
    ("main", "Question", "call"),
    ("research", "search", "call"),
    ("research", "writer", "call"),
    ("research", "Answer", "call"),
    ("writer", "Quote", "call"),
    ("Answer", "Quote", "call"),
    ("search", "Hit", "call"),
    ("total", "sum", "call"),
]

DEMO_GRAPH: Graph = (DEMO_NODES, DEMO_EDGES)


def main() -> None:
    from pathlib import Path

    out = Path(__file__).resolve().parent.parent / "examples" / "graph_design" / "out"
    out.mkdir(parents=True, exist_ok=True)

    graph = DEMO_GRAPH
    items: list[tuple[str, str, str]] = []
    record = {"graph": {"nodes": [], "edges": []}, "layouts": {}}

    for name, fn in STRATEGIES.items():
        result = compute_layout(graph, strategy=name)
        svg = render_svg(result, graph)
        (out / f"layout-{name}.svg").write_text(svg, encoding="utf-8")
        items.append((f"{name} layout", f"digest {result.digest}", svg))
        record["layouts"][name] = {
            "digest": result.digest,
            "positions": {k: [round(x, 2), round(y, 2)] for k, (x, y) in result.positions.items()},
        }

    positions = compute_layout(graph, strategy="layered").positions
    for style in ("straight", "curved", "orthogonal"):
        routes = {
            _link_id(i, s, t): route_points(positions[s], positions[t], style)
            for i, (s, t, _) in enumerate(graph[1])
        }
        styles = {_link_id(i, s, t): style for i, (s, t, _) in enumerate(graph[1])}
        digest = layout_digest(positions, routes)
        result = Layout(strategy="layered", positions=positions, routes=routes, styles=styles, digest=digest)
        svg = render_svg(result, graph)
        (out / f"edge-{style}.svg").write_text(svg, encoding="utf-8")
        items.append((f"{style} edges", "layered positions", svg))

    smart = choose(graph[0], graph[1])
    record["graph"] = {
        "nodes": [{"id": n[0], "label": n[1], "kind": n[2]} for n in graph[0]],
        "edges": [{"source": e[0], "target": e[1], "kind": e[2]} for e in graph[1]],
    }
    record["smartChoice"] = smart
    record["whySmartChoice"] = explain_choice(graph[0], graph[1])

    (out / "layouts.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"smart choice for this graph: {smart} ({explain_choice(graph[0], graph[1])})")
    print(f"wrote {len(items) + 1} files to {out}")


if __name__ == "__main__":
    main()
