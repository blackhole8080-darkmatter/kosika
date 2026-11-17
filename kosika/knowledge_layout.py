"""Render hypergraph facts as a deterministic, labelled picture.

The maths brain knows facts as (head, relation, tail). This bridge turns them
into the (nodes, edges) shape :mod:`kosika.layout` positions, reuses that
layout's deterministic positions and digest, and emits an SVG whose edges carry
their relation as a label — so the knowledge reads straight off the picture.

No model and no randomness: the same facts always draw the same SVG with the
same digest. The production renderer (:mod:`kosika.motion`) draws blank arrows,
so the labelled edge text is emitted here rather than changing shared code.
"""

from __future__ import annotations

import html
import math
from typing import Any

from .knowledge_graph import Fact
from .layout import (
    GREEN,
    HEIGHT,
    INK,
    LINE,
    NODE_R,
    PAPER,
    WIDTH,
    compute_layout,
    route_points,
)

Graph = tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]


def facts_to_graph(facts: list[Fact]) -> Graph:
    """Distinct entities as nodes, distinct (head, tail, relation) as edges.

    Sorted so the picture is a pure function of the facts, not their order.
    """
    entities = sorted({f.head for f in facts} | {f.tail for f in facts})
    triples = sorted({(f.head, f.tail, f.relation) for f in facts})
    nodes = [(entity, entity, "entity") for entity in entities]
    return nodes, triples


def _path_d(points: list[tuple[float, float]]) -> str:
    (x0, y0) = points[0]
    if len(points) == 2:
        (x1, y1) = points[1]
        return f"M {x0:.1f} {y0:.1f} L {x1:.1f} {y1:.1f}"
    (x1, y1), (x2, y2), (x3, y3) = points[1], points[2], points[3]
    return (
        f"M {x0:.1f} {y0:.1f} "
        f"C {x1:.1f} {y1:.1f} {x2:.1f} {y2:.1f} {x3:.1f} {y3:.1f}"
    )


def _svg(nodes: list, edges: list, layout: Any, title: str) -> str:
    style = "curved" if layout.strategy == "layered" else "straight"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" '
        f'width="{WIDTH}" height="{HEIGHT}" role="img" '
        f'aria-label="{html.escape(title or "knowledge graph")}">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAPER}"/>',
        "<defs>",
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">',
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{LINE}"/>',
        "</marker>",
        "</defs>",
    ]
    if title:
        parts.append(
            f'<text x="78" y="42" font-family="Georgia, serif" font-size="22" '
            f'fill="{INK}">{html.escape(title)}</text>'
        )
    for source, target, relation in edges:
        a = layout.positions[source]
        b = layout.positions[target]
        points = route_points(a, b, style)
        parts.append(
            f'<path d="{_path_d(points)}" fill="none" stroke="{LINE}" '
            f'stroke-width="1.6" marker-end="url(#arrow)"/>'
        )
        # The relation, written beside the edge so the picture is self-explaining.
        # Sit it at the path's midpoint, not the endpoints': a skip-level edge
        # bows away from the node it would otherwise land on.
        if len(points) == 2:
            (px, py), (qx, qy) = points
            mx, my = (px + qx) / 2, (py + qy) / 2
        else:
            (px, py), (c1x, c1y), (c2x, c2y), (qx, qy) = points
            mx = (px + 3 * c1x + 3 * c2x + qx) / 8
            my = (py + 3 * c1y + 3 * c2y + qy) / 8
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        mx -= dy / length * 11
        my += dx / length * 11
        parts.append(
            f'<text x="{mx:.1f}" y="{my:.1f}" text-anchor="middle" '
            f'dominant-baseline="central" font-family="Georgia, serif" '
            f'font-size="11" fill="{INK}" stroke="{PAPER}" stroke-width="3" '
            f'paint-order="stroke">{html.escape(relation)}</text>'
        )
    for node_id, label, _kind in nodes:
        x, y = layout.positions[node_id]
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{NODE_R}" fill="{GREEN}" '
            f'fill-opacity="0.14" stroke="{GREEN}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{y + NODE_R + 14:.1f}" text-anchor="middle" '
            f'font-family="Georgia, serif" font-size="12.5" fill="{INK}" '
            f'stroke="{PAPER}" stroke-width="3" paint-order="stroke">'
            f'{html.escape(label)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _html(svg: str, title: str, layout: Any, count: int) -> str:
    caption = f"{layout.strategy} layout · digest {layout.digest} · {count} facts"
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title or 'Knowledge graph')}</title>"
        "<style>html,body{margin:0;background:#eef1ea;font-family:Georgia,serif;"
        "color:#26332b}main{max-width:960px;margin:0 auto;padding:28px 24px}"
        "h1{font-size:24px;margin:0 0 4px}p{color:#7d8a78;font-size:13px;margin:0 0 18px}"
        "svg{display:block;width:100%;height:auto;background:#f6f7f3;"
        "border:1px solid #d9dfd2;border-radius:10px}</style></head>\n"
        f"<body><main><h1>{html.escape(title or 'Knowledge graph')}</h1>"
        f"<p>{html.escape(caption)}</p>\n{svg}\n</main></body></html>\n"
    )


def render_knowledge(
    facts: list[Fact], *, title: str = "", strategy: str = "auto"
) -> dict[str, Any]:
    """Position the facts with kosika.layout and emit a labelled SVG + HTML page.

    Returns svg, html, digest, strategy, the node ids, the edge triples and the
    fact count. Empty facts render an empty picture rather than failing.
    """
    nodes, edges = facts_to_graph(facts)
    layout = compute_layout((nodes, edges), strategy=strategy)
    svg = _svg(nodes, edges, layout, title)
    return {
        "svg": svg,
        "html": _html(svg, title, layout, len(edges)),
        "digest": layout.digest,
        "strategy": layout.strategy,
        "nodes": [node[0] for node in nodes],
        "edges": edges,
        "count": len(edges),
    }