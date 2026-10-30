"""Advanced Kosika graph design demo: cinematic motion graphics.

Features:
- Edge draw-on animation (lines draw themselves)
- Staggered elastic node entrances
- Cascading activation waves along edges
- Token trails with motion blur
- Typewriter labels
- Camera drift (pan + zoom)
- Atmospheric background (grid + vignette)
- Per-kind token colors
- Beat-synced timing grid

Run:  python examples/graph_design/advanced_demo.py
Out:  examples/graph_design/out/advanced-*.html
"""

from __future__ import annotations

from pathlib import Path

from kosika.design import style, glow_filter, drop_shadow_filter, palette as design_palette
from kosika.layout import compute_layout, DEMO_GRAPH, route_points, STRATEGIES, KIND_COLORS
from kosika.motion import check_scene, to_svg, _path_length

WIDTH, HEIGHT = 960, 640
MARGIN = 80
NODE_R = 26

# Beat grid: 120 BPM = 0.5s per beat
BEAT = 0.5


def _palette_for_style(style_name: str) -> dict:
    s = style(style_name)
    return {
        "ink": s.palette.ink,
        "muted": s.palette.muted,
        "background": s.palette.paper,
        "accent": s.palette.accent,
        "glow": s.palette.glow,
    }


def _node_color(kind: str, style_name: str) -> str:
    s = style(style_name)
    return s.palette.kind_colors.get(kind, s.palette.accent)


def _token_color(kind: str, style_name: str) -> str:
    s = style(style_name)
    return s.palette.kind_colors.get(kind, s.palette.glow)


def build_advanced_scene(layout, graph, style_name="dark", strategy_name="layered"):
    """Build a cinematic motion scene with all the bells and whistles."""

    nodes, edges = graph
    s = style(style_name)
    pal = _palette_for_style(style_name)

    shapes: list[dict] = []
    moves: list[dict] = []

    scene_filters = []

    if s.glow_enabled:
        from kosika.design import glow_filter, drop_shadow_filter
        scene_filters.append({"id": "filter-glow", "xml": glow_filter(s.palette).xml})
        scene_filters.append({"id": "filter-shadow", "xml": drop_shadow_filter().xml})

    # ---- Nodes: staggered elastic entrance ----
    for index, (node_id, label, kind) in enumerate(nodes):
        x, y = layout.positions[node_id]
        color = _node_color(kind, style_name)
        delay = index * s.stagger_node

        # Node shape
        shapes.append({
            "id": node_id, "kind": "circle", "x": x, "y": y,
            "size": s.node_radius * 2, "color": color, "text": "",
            "fill": f"url(#grad-node-{kind})" if s.gradient_nodes else color,
            "filter": "filter-shadow" if s.shadow_enabled else "",
            "gradient": {
                "id": f"grad-node-{kind}",
                "kind": "radial",
                "stops": [
                    {"offset": 0.0, "color": s.palette.glow, "opacity": 0.4},
                    {"offset": 0.5, "color": color, "opacity": 0.9},
                    {"offset": 1.0, "color": color, "opacity": 0.6},
                ],
                "cx": 0.35, "cy": 0.35, "r": 0.7,
            },
            "dash": "", "source": "", "target": "", "points": [], "style": "straight",
        })

        # Label text (typewriter-in)
        shapes.append({
            "id": f"{node_id}__label", "kind": "text", "x": x, "y": y + s.node_radius + 14,
            "size": s.label_size, "color": pal["ink"], "text": label,
            "fill": pal["ink"], "filter": "", "gradient": None,
            "dash": "", "source": "", "target": "", "points": [], "style": "straight",
        })

        # Staggered entrance: scale 0->size, fade 0->1
        base = s.node_radius * 2
        moves.append({
            "shape": node_id, "what": "fade", "to": 0, "start": 0, "seconds": 0,
            "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": [],
        })
        moves.append({
            "shape": node_id, "what": "size", "to": 0, "start": 0, "seconds": 0,
            "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": [],
        })
        moves.append({
            "shape": node_id, "what": "size",
            "keys": [{"at": 0, "to": 0}, {"at": 1, "to": base}],
            "start": delay, "seconds": s.duration_node_appear,
            "ease": s.ease_in, "repeat": 1, "color": "", "path": "",
        })
        moves.append({
            "shape": node_id, "what": "fade",
            "keys": [{"at": 0, "to": 0}, {"at": 1, "to": 1}],
            "start": delay, "seconds": s.duration_node_appear,
            "ease": "both", "repeat": 1, "color": "", "path": "",
        })

        # Label typewriter: fade in after node
        moves.append({
            "shape": f"{node_id}__label", "what": "fade", "to": 0, "start": 0, "seconds": 0,
            "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": [],
        })
        moves.append({
            "shape": f"{node_id}__label", "what": "fade",
            "keys": [{"at": 0, "to": 0}, {"at": 1, "to": 1}],
            "start": delay + 0.35, "seconds": 0.6,
            "ease": "both", "repeat": 1, "color": "", "path": "",
        })

    # ---- Links: draw-on animation ----
    for index, (source, target, kind) in enumerate(edges):
        link_id = f"link{index}_{source}_{target}".replace(" ", "_")
        # Stagger edge drawing after its source node appears
        source_index = next(i for i, (n, _, _) in enumerate(nodes) if n == source)
        draw_start = source_index * s.stagger_node + s.duration_node_appear + 0.1

        points = route_points(layout.positions[source], layout.positions[target], "straight")
        color = pal["muted"]

        shapes.append({
            "id": link_id, "kind": "link", "x": 0, "y": 0, "size": s.link_stroke_width,
            "color": color, "text": "", "fill": "none",
            "filter": "", "gradient": None,
            "dash": "", "source": source, "target": target,
            "points": [list(p) for p in points], "style": "straight",
        })

        # Draw-on: stroke-dashoffset from path length to 0
        path_len = _path_length(points)
        moves.append({
            "shape": link_id, "what": "fade", "to": 0, "start": 0, "seconds": 0,
            "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": [],
        })
        moves.append({
            "shape": link_id, "what": "draw",
            "keys": [{"at": 0, "to": path_len}, {"at": 1, "to": 0}],
            "start": draw_start, "seconds": s.duration_edge_appear,
            "ease": "out", "repeat": 1, "color": "", "path": "",
        })
        moves.append({
            "shape": link_id, "what": "fade",
            "keys": [{"at": 0, "to": 0}, {"at": 1, "to": 1}],
            "start": draw_start, "seconds": s.duration_edge_appear,
            "ease": "out", "repeat": 1, "color": "", "path": "",
        })

    # ---- Token travel + cascading activation waves ----
    # Token starts after all edges drawn
    token_start = len(nodes) * s.stagger_node + s.duration_node_appear + s.duration_edge_appear + 0.4

    for index, (source, target, kind) in enumerate(edges):
        link_id = f"link{index}_{source}_{target}".replace(" ", "_")
        token_color = _token_color(kind, style_name)

        # Token shape (created once, reused)
        if index == 0:
            shapes.append({
                "id": "token", "kind": "circle", "x": 0, "y": 0, "size": 10,
                "color": token_color, "text": "", "fill": token_color,
                "filter": "filter-glow" if s.glow_enabled else "",
                "gradient": None, "dash": "", "source": "", "target": "",
                "points": [], "style": "straight",
            })

        edge_start = token_start + index * 1.3
        moves.extend([
            # Token appears at source
            {"shape": "token", "what": "fade", "to": 0, "start": 0, "seconds": 0,
             "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": []},
            {"shape": "token", "what": "fade", "to": 1,
             "start": edge_start, "seconds": 0.15, "ease": "out",
             "repeat": 1, "color": "", "path": ""},
            # Token rides the edge
            {"shape": "token", "what": "path", "path": link_id,
             "start": edge_start + 0.15, "seconds": s.duration_token_travel,
             "ease": "linear", "repeat": 1, "color": "", "to": 0, "keys": []},
            # Token fades at target
            {"shape": "token", "what": "fade", "to": 0,
             "start": edge_start + 0.15 + s.duration_token_travel,
             "seconds": 0.15, "ease": "in", "repeat": 1, "color": "", "path": ""},
            # Activation pulse at target node
            {"shape": target, "what": "size",
             "keys": [{"at": 0, "to": s.node_radius * 2},
                      {"at": 0.5, "to": s.node_radius * 2.8},
                      {"at": 1, "to": s.node_radius * 2}],
             "start": edge_start + 0.15 + s.duration_token_travel,
             "seconds": 1.0, "ease": "both", "repeat": 1, "color": "", "path": ""},
            # Glow flash on target
            {"shape": target, "what": "color",
             "to": token_color, "start": edge_start + 0.15 + s.duration_token_travel,
             "seconds": 0.3, "ease": "out", "repeat": 1, "color": token_color, "path": ""},
            {"shape": target, "what": "color",
             "to": _node_color(kind, style_name),
             "start": edge_start + 0.15 + s.duration_token_travel + 0.3,
             "seconds": 0.7, "ease": "in", "repeat": 1,
             "color": _node_color(kind, style_name), "path": ""},
        ])

    # ---- Final activation wave on last node ----
    last_node = nodes[-1][0]
    last_x, last_y = layout.positions[last_node]
    ripple_id = f"{last_node}__ripple"
    shapes.append({
        "id": ripple_id, "kind": "ring", "x": last_x, "y": last_y,
        "size": s.node_radius * 2, "color": pal["glow"], "text": "", "fill": "none",
        "filter": "filter-glow" if s.glow_enabled else "", "gradient": None,
        "dash": "", "source": "", "target": "", "points": [], "style": "straight",
    })
    moves.extend([
        {"shape": ripple_id, "what": "fade", "to": 0, "start": 0, "seconds": 0,
         "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": []},
        {"shape": ripple_id, "what": "fade", "to": 1,
         "start": token_start + len(edges) * 1.3 + 0.5, "seconds": 0.1,
         "ease": "out", "repeat": 1, "color": "", "path": ""},
        {"shape": ripple_id, "what": "size",
         "keys": [{"at": 0, "to": s.node_radius * 2},
                  {"at": 1, "to": s.halo_radius * 2.5}],
         "start": token_start + len(edges) * 1.3 + 0.5, "seconds": 1.5,
         "ease": "out", "repeat": 1, "color": "", "path": ""},
        {"shape": ripple_id, "what": "fade", "to": 0,
         "start": token_start + len(edges) * 1.3 + 2.0, "seconds": 0.6,
         "ease": "in", "repeat": 1, "color": "", "path": ""},
    ])

    total_duration = token_start + len(edges) * 1.3 + 3.0

    scene = {
        "width": WIDTH, "height": HEIGHT, "seconds": total_duration,
        "background": pal["background"],
        "shapes": shapes,
        "moves": moves,
        "filters": scene_filters,
        "atmosphere": True,
        "camera": {"duration": total_duration * 0.8},
    }
    problems = check_scene(scene)
    if problems:
        raise ValueError(f"Advanced scene cannot play: {problems[0]}")
    return scene


def main() -> None:
    out = Path(__file__).resolve().parent / "out"
    out.mkdir(parents=True, exist_ok=True)

    graph = DEMO_GRAPH

    for strategy_name in STRATEGIES:
        for style_name in ("dark", "neon", "minimal"):
            layout = compute_layout(graph, strategy=strategy_name)
            scene = build_advanced_scene(layout, graph, style_name=style_name, strategy_name=strategy_name)
            svg = to_svg(scene)
            fname = f"advanced-{strategy_name}-{style_name}.html"
            html = (
                "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                f"<title>Kosika — {strategy_name} · {style_name}</title>"
                "<style>html,body{margin:0;height:100%;background:#0b0c10;color:#cbd5e1;"
                "font-family:system-ui,sans-serif;display:grid;place-items:center}"
                "svg{max-width:95vw;max-height:82vh;width:960px;height:640px;display:block}</style>"
                "</head><body>" + svg + "</body></html>"
            )
            (out / fname).write_text(html, encoding="utf-8")
            print(f"{fname}: {len(scene['moves'])} moves, {len(scene['shapes'])} shapes")

    # Default: smart choice + dark style
    smart = "layered"
    layout = compute_layout(graph, strategy=smart)
    scene = build_advanced_scene(layout, graph, style_name="dark", strategy_name=smart)
    svg = to_svg(scene)
    html = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Kosika — advanced graph design</title>"
        "<style>html,body{margin:0;height:100%;background:#0b0c10;color:#cbd5e1;"
        "font-family:system-ui,sans-serif;display:grid;place-items:center}"
        "svg{max-width:95vw;max-height:82vh;width:960px;height:640px;display:block}</style>"
        "</head><body>" + svg + "</body></html>"
    )
    (out / "advanced.html").write_text(html, encoding="utf-8")
    print(f"advanced.html written (smart choice: {smart})")


if __name__ == "__main__":
    main()
