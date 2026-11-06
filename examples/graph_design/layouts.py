"""Graph layouts for Kosika design: vertices, edges, and how they are drawn.

A prototype of the layout layer the design docs point at:

- positions are *computed* from the graph by a named strategy, never invented;
- every strategy is deterministic: the same graph always draws the same picture,
  which is what lets a layout be checked, hashed and replayed;
- edges are routed straight, curved (cubic Bezier) or orthogonally (elbow);
- the output is SVG, so it can also become a scene on the way to a film.

Run:  python examples/graph_design/layouts.py
Out:  examples/graph_design/out/*.svg and index.html
"""

from __future__ import annotations

import hashlib
import html
import json
import math
from dataclasses import dataclass

WIDTH, HEIGHT = 960, 620
MARGIN = 78
NODE_R = 23

#: The palette the project already uses (paper, ink, green, muted).
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


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    kind: str = "function"


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    kind: str = "call"


Point = tuple[float, float]


# ------------------------------------------------------------------ the graph
# A small program graph, the kind a mindmap draws: a workflow calls two
# functions, one of them asks a model and searches a folder, and records
# carry the answers.

DEMO_NODES = [
    Node("main", "main", "workflow"),
    Node("research", "research", "function"),
    Node("total", "total", "function"),
    Node("search", "search", "tool"),
    Node("writer", "writer", "model"),
    Node("answer", "Answer", "record"),
    Node("question", "Question", "record"),
    Node("quote", "Quote", "record"),
    Node("hits", "Hit", "record"),
    Node("sum", "sum", "function"),
]

DEMO_EDGES = [
    Edge("main", "research"),
    Edge("main", "total"),
    Edge("main", "question"),
    Edge("research", "search"),
    Edge("research", "writer"),
    Edge("research", "answer"),
    Edge("writer", "quote"),
    Edge("answer", "quote"),
    Edge("search", "hits"),
    Edge("total", "sum"),
]


# ------------------------------------------------------------------ strategies


def _neighbours(nodes: list[Node], edges: list[Edge]) -> dict[str, list[str]]:
    found = {node.id: [] for node in nodes}
    for edge in edges:
        found.setdefault(edge.source, []).append(edge.target)
    return found


def _is_acyclic(nodes: list[Node], edges: list[Edge]) -> bool:
    """Kahn's algorithm: a graph is acyclic when every node can be removed."""

    indegree = {node.id: 0 for node in nodes}
    for edge in edges:
        indegree[edge.target] = indegree.get(edge.target, 0) + 1
    ready = sorted(name for name, count in indegree.items() if count == 0)
    seen = 0
    while ready:
        here = ready.pop(0)
        seen += 1
        for there in _neighbours(nodes, edges).get(here, []):
            indegree[there] -= 1
            if indegree[there] == 0:
                ready.append(there)
                ready.sort()
    return seen == len(nodes)


def _layers(nodes: list[Node], edges: list[Edge]) -> dict[str, int]:
    """Longest-path layering; cycles are broken by declaration order."""

    order = [node.id for node in nodes]
    layer = {name: 0 for name in order}
    outgoing = _neighbours(nodes, edges)
    # Topological order when possible; declaration order otherwise.
    indegree = {name: 0 for name in order}
    for edge in edges:
        indegree[edge.target] = indegree.get(edge.target, 0) + 1
    ready = sorted(name for name, count in indegree.items() if count == 0)
    seen: list[str] = []
    while ready:
        here = ready.pop(0)
        seen.append(here)
        for there in outgoing.get(here, []):
            indegree[there] -= 1
            if indegree[there] == 0:
                ready.append(there)
                ready.sort()
    ordered = seen + [name for name in order if name not in seen]
    for here in ordered:
        for there in outgoing.get(here, []):
            if layer[there] < layer[here] + 1:
                layer[there] = layer[here] + 1
    return layer


def _fit(points: list[Point], *, padding: float = 34.0) -> list[Point]:
    """Scale and centre a layout inside the drawing box.

    The scale is uniform, so a strategy's own shape is preserved: a circle
    stays a circle, layers stay square to each other. Only the enclosing box
    changes. ``padding`` keeps labels from touching the frame.
    """

    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    span_x = right - left
    span_y = bottom - top
    box_w = WIDTH - 2 * MARGIN
    box_h = HEIGHT - 2 * MARGIN - padding
    # A single point, or a layout that is one row: let the other axis fill.
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


def _barycentric(rows: dict[int, list[str]], edges: list[Edge], sweeps: int = 4) -> None:
    """Reorder each layer so a vertex sits under the average of its neighbours.

    This is the crossing-reduction pass Sugiyama layouts use. It only ever
    permutes *within* a layer, so every edge still points downwards and the
    layering stays valid. Ties break on the id, so the result is deterministic.
    """

    layer_of = {name: index for index, row in rows.items() for name in row}
    preds: dict[str, list[str]] = {name: [] for name in layer_of}
    succs: dict[str, list[str]] = {name: [] for name in layer_of}
    for edge in edges:
        if edge.source in layer_of and edge.target in layer_of:
            succs[edge.source].append(edge.target)
            preds[edge.target].append(edge.source)

    column = {name: index for index, row in rows.items() for index, name in enumerate(row)}
    layers = sorted(rows)

    def mean(anchors: list[str], fallback: int) -> float:
        if not anchors:
            return float(fallback)
        return sum(column[anchor] for anchor in anchors) / len(anchors)

    for _ in range(sweeps):
        # Downwards: sort by the parents' average column.
        for index in layers[1:]:
            rows[index].sort(key=lambda name: (mean(preds[name], column[name]), name))
            for place, name in enumerate(rows[index]):
                column[name] = place
        # Upwards: sort by the children's average column.
        for index in reversed(layers[:-1]):
            rows[index].sort(key=lambda name: (mean(succs[name], column[name]), name))
            for place, name in enumerate(rows[index]):
                column[name] = place


def layered(nodes: list[Node], edges: list[Edge]) -> dict[str, Point]:
    """Top-down layers: sources at the top, dependencies below."""

    layer = _layers(nodes, edges)
    rows: dict[int, list[str]] = {}
    for name in sorted(layer, key=lambda key: (layer[key], key)):
        rows.setdefault(layer[name], []).append(name)
    _barycentric(rows, edges)

    points: list[Point] = []
    order: list[str] = []
    for index in sorted(rows):
        row = rows[index]
        # Centre each row on the drawing's vertical axis.
        for column, name in enumerate(row):
            order.append(name)
            points.append(((column - (len(row) - 1) / 2) * 120.0, index * 120.0))
    return dict(zip(order, _fit(points)))


def _ring_order(nodes: list[Node], edges: list[Edge]) -> list[str]:
    """Breadth-first from the busiest vertex: neighbours end up adjacent."""

    names = sorted(node.id for node in nodes)
    adjacent: dict[str, set[str]] = {name: set() for name in names}
    for edge in edges:
        if edge.source in adjacent and edge.target in adjacent:
            adjacent[edge.source].add(edge.target)
            adjacent[edge.target].add(edge.source)

    order: list[str] = []
    seen: set[str] = set()
    while len(order) < len(names):
        start = min((n for n in names if n not in seen), key=lambda n: (-len(adjacent[n]), n))
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


def _crossings(order: list[str], edges: list[Edge]) -> int:
    """How many edge pairs cross when vertices sit on a ring in this order."""

    place = {name: index for index, name in enumerate(order)}
    chords = []
    for edge in edges:
        if edge.source in place and edge.target in place and edge.source != edge.target:
            a, b = place[edge.source], place[edge.target]
            chords.append((min(a, b), max(a, b)))
    total = 0
    for index, (a, b) in enumerate(chords):
        for c, d in chords[index + 1 :]:
            if len({a, b, c, d}) < 4:
                continue
            if (a < c < b < d) or (c < a < d < b):
                total += 1
    return total


def _untangle(order: list[str], edges: list[Edge], limit: int = 400) -> list[str]:
    """Hill-climb the ring order by swapping neighbours: fewer crossings or stop.

    Every candidate is scored exactly, so the result is a local minimum that
    the same graph always reaches. No dice, no annealing schedule to tune.
    """

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


def circular(nodes: list[Node], edges: list[Edge]) -> dict[str, Point]:
    """Every vertex on one circle, ordered to cross as few edges as possible."""

    names = _untangle(_ring_order(nodes, edges), edges)
    count = max(len(names), 1)
    points = [
        (math.cos(2 * math.pi * i / count), math.sin(2 * math.pi * i / count))
        for i in range(count)
    ]
    return dict(zip(names, _fit(points)))


def grid(nodes: list[Node], edges: list[Edge]) -> dict[str, Point]:
    """A near-square grid, in breadth-first order so neighbours sit close."""

    names = _ring_order(nodes, edges)
    columns = max(1, math.ceil(math.sqrt(len(names))))
    points = [
        (index % columns * 100.0, index // columns * 100.0)
        for index in range(len(names))
    ]
    return dict(zip(names, _fit(points)))


def force(nodes: list[Node], edges: list[Edge], rounds: int = 240) -> dict[str, Point]:
    """Fruchterman–Reingold, started on a circle: deterministic, no dice."""

    names = sorted(node.id for node in nodes)
    count = max(len(names), 1)
    index = {name: i for i, name in enumerate(names)}
    area = (WIDTH - 2 * MARGIN) * (HEIGHT - 2 * MARGIN)
    ideal = math.sqrt(area / count)
    pos = [
        (
            (WIDTH / 2) + 240 * math.cos(2 * math.pi * i / count),
            (HEIGHT / 2) + 240 * math.sin(2 * math.pi * i / count),
        )
        for i in range(count)
    ]
    links = [
        (index[edge.source], index[edge.target])
        for edge in edges
        if edge.source in index and edge.target in index
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


def choose(nodes: list[Node], edges: list[Edge]) -> str:
    """The smart default: a DAG wants layers, a small ring wants a circle."""

    if _is_acyclic(nodes, edges) and len(nodes) >= 4:
        return "layered"
    if len(nodes) <= 6:
        return "circular"
    if len(edges) > 2 * len(nodes):
        return "grid"
    return "force"


def explain_choice(nodes: list[Node], edges: list[Edge]) -> str:
    """The reason behind :func:`choose`, in words."""

    if _is_acyclic(nodes, edges) and len(nodes) >= 4:
        return "acyclic, so the layers of dependency are the honest picture"
    if len(nodes) <= 6:
        return "few vertices, so one ring reads best"
    if len(edges) > 2 * len(nodes):
        return "dense, so a grid keeps every edge short"
    return "cyclic and mixed, so a deterministic force layout settles it"


STRATEGIES = {
    "layered": layered,
    "circular": circular,
    "grid": grid,
    "force": force,
}


# ------------------------------------------------------------------ edges


def _trim(start: Point, end: Point, radius: float = NODE_R + 2) -> tuple[Point, Point]:
    """Pull both ends back to the circle's edge, so arrowheads land on it."""

    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    return (
        (start[0] + ux * radius, start[1] + uy * radius),
        (end[0] - ux * radius, end[1] - uy * radius),
    )


def edge_path(a: Point, b: Point, style: str = "straight", bend: float = 0.16) -> str:
    """An SVG path for one edge: straight, curved, or an orthogonal elbow."""

    (ax, ay), (bx, by) = _trim(a, b)
    if style == "straight":
        return f"M {ax:.1f} {ay:.1f} L {bx:.1f} {by:.1f}"
    if style == "curved":
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy) or 1.0
        px, py = -dy / length, dx / length
        offset = length * bend
        c1 = (ax + dx * 0.33 + px * offset, ay + dy * 0.33 + py * offset)
        c2 = (ax + dx * 0.66 + px * offset, ay + dy * 0.66 + py * offset)
        return (
            f"M {ax:.1f} {ay:.1f} "
            f"C {c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} "
            f"{bx:.1f} {by:.1f}"
        )
    if style == "orthogonal":
        if abs(bx - ax) >= abs(by - ay):
            mid = (ax + bx) / 2
            return (
                f"M {ax:.1f} {ay:.1f} L {mid:.1f} {ay:.1f} "
                f"L {mid:.1f} {by:.1f} L {bx:.1f} {by:.1f}"
            )
        mid = (ay + by) / 2
        return (
            f"M {ax:.1f} {ay:.1f} L {ax:.1f} {mid:.1f} "
            f"L {bx:.1f} {mid:.1f} L {bx:.1f} {by:.1f}"
        )
    raise ValueError(f"unknown edge style {style!r}")


# ------------------------------------------------------------------ drawing


def _label_size(label: str) -> str:
    """Keep the label inside its circle rather than spilling over the rim."""

    if len(label) <= 5:
        return "11"
    if len(label) <= 7:
        return "10"
    return "8.8"


def layout_digest(positions: dict[str, Point]) -> str:
    """A short fingerprint of a layout: same graph, same strategy, same hash.

    This is what makes a layout checkable: two runs, two machines, one number.
    """

    payload = ";".join(
        f"{name}:{positions[name][0]:.1f},{positions[name][1]:.1f}"
        for name in sorted(positions)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def render_svg(
    nodes: list[Node],
    edges: list[Edge],
    positions: dict[str, Point],
    *,
    style: str = "straight",
    title: str = "",
    subtitle: str = "",
) -> str:
    """One deterministic SVG picture of the graph."""

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img" '
        f'aria-label="{html.escape(title or "graph")}">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAPER}"/>',
        "<defs>",
        f'<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">',
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{MUTED}"/>',
        "</marker>",
        "</defs>",
    ]
    if title:
        parts.append(
            f'<text x="{MARGIN}" y="42" font-family="Georgia, serif" font-size="22" '
            f'fill="{INK}">{html.escape(title)}</text>'
        )
    if subtitle:
        parts.append(
            f'<text x="{MARGIN}" y="64" font-family="Georgia, serif" font-size="13" '
            f'fill="{MUTED}">{html.escape(subtitle)}</text>'
        )
    for edge in edges:
        if edge.source not in positions or edge.target not in positions:
            continue
        path = edge_path(positions[edge.source], positions[edge.target], style)
        parts.append(
            f'<path d="{path}" fill="none" stroke="{LINE}" stroke-width="1.6" '
            f'marker-end="url(#arrow)"/>'
        )
    for node in nodes:
        if node.id not in positions:
            continue
        x, y = positions[node.id]
        colour = KIND_COLORS.get(node.kind, INK)
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{NODE_R}" fill="{colour}" '
            f'fill-opacity="0.14" stroke="{colour}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" '
            f'dominant-baseline="central" font-family="Georgia, serif" '
            f'font-size="{_label_size(node.label)}" fill="{INK}">'
            f"{html.escape(node.label)}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


def render_gallery(items: list[tuple[str, str, str]]) -> str:
    """items: (title, subtitle, svg). One page, all layouts side by side."""

    cards = []
    for title, subtitle, svg in items:
        cards.append(
            f'<figure><figcaption><b>{html.escape(title)}</b>'
            f'<span>{html.escape(subtitle)}</span></figcaption>{svg}</figure>'
        )
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Kosika graph layouts</title><style>"
        "body{margin:0;background:#eef1ea;font-family:Georgia,serif;color:#26332b}"
        "header{padding:28px 32px 8px}h1{margin:0 0 6px;font-size:26px}"
        "p.lead{margin:0;color:#7d8a78;font-size:14px;max-width:760px}"
        "main{display:grid;grid-template-columns:1fr 1fr;gap:18px;padding:20px 32px 40px}"
        "figure{margin:0;background:#f6f7f3;border:1px solid #d9dfd2;border-radius:10px;"
        "overflow:hidden;box-shadow:0 1px 2px rgba(38,51,43,.06)}"
        "figcaption{padding:12px 16px 8px;font-size:14px}"
        "figcaption span{display:block;color:#7d8a78;font-size:12px;margin-top:2px}"
        "svg{display:block;width:100%;height:auto}"
        "</style></head><body>"
        "<header><h1>Kosika graph layouts</h1>"
        "<p class='lead'>The same graph, positioned by four deterministic strategies "
        "and drawn with three edge routes. Every picture is a pure function of the "
        "graph and the strategy: no randomness, no model, no invention.</p></header>"
        f"<main>{''.join(cards)}</main></body></html>"
    )


# ------------------------------------------------------------------ demo


def main() -> None:
    from pathlib import Path

    out = Path(__file__).parent / "out"
    out.mkdir(exist_ok=True)

    items: list[tuple[str, str, str]] = []
    record: dict[str, object] = {"graph": {"nodes": [], "edges": []}, "layouts": {}}

    for name, strategy in STRATEGIES.items():
        positions = strategy(DEMO_NODES, DEMO_EDGES)
        digest = layout_digest(positions)
        svg = render_svg(
            DEMO_NODES,
            DEMO_EDGES,
            positions,
            style="straight",
            title=f"{name} layout",
            subtitle=f"deterministic positions · digest {digest} · straight edges",
        )
        (out / f"layout-{name}.svg").write_text(svg, encoding="utf-8")
        items.append(
            (
                f"{name} layout",
                f"straight edges · {digest}",
                render_svg(DEMO_NODES, DEMO_EDGES, positions, style="straight"),
            )
        )
        record["layouts"][name] = {  # type: ignore[index]
            "digest": digest,
            "positions": {
                key: [round(x, 2), round(y, 2)] for key, (x, y) in positions.items()
            },
        }

    # One layout, three edge routes: the movement of the edge is a choice.
    positions = layered(DEMO_NODES, DEMO_EDGES)
    for style in ("straight", "curved", "orthogonal"):
        svg = render_svg(
            DEMO_NODES,
            DEMO_EDGES,
            positions,
            style=style,
            title=f"layered · {style} edges",
            subtitle="same vertex positions; only the edge route changes",
        )
        (out / f"edge-{style}.svg").write_text(svg, encoding="utf-8")
        items.append(
            (
                f"{style} edges",
                "layered positions",
                render_svg(DEMO_NODES, DEMO_EDGES, positions, style=style),
            )
        )

    smart = choose(DEMO_NODES, DEMO_EDGES)
    record["graph"] = {
        "nodes": [{"id": n.id, "label": n.label, "kind": n.kind} for n in DEMO_NODES],
        "edges": [{"source": e.source, "target": e.target, "kind": e.kind} for e in DEMO_EDGES],
    }
    record["smartChoice"] = smart
    record["whySmartChoice"] = explain_choice(DEMO_NODES, DEMO_EDGES)
    (out / "layouts.json").write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )

    (out / "index.html").write_text(render_gallery(items), encoding="utf-8")
    print(f"smart choice for this graph: {smart} ({explain_choice(DEMO_NODES, DEMO_EDGES)})")
    print(f"wrote {len(items) + 2} files to {out}")


if __name__ == "__main__":
    main()
