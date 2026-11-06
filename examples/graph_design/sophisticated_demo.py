"""Sophisticated Kosika graph design demo.

Shows an advanced design + animation + motion graphics layer:

- Gradients on nodes (radial, lit from top-left)
- Glow and drop-shadow SVG filters
- Staggered elastic entrances with spring easing
- Flowing tokens that travel along edges via animateMotion
- Pulse/ripple activation on target nodes
- Multiple layout strategies: layered, circular, grid, force
- Multiple visual styles: minimal, neon, print, dark
- Self-contained HTML with playback controls (play/pause/scrub)

Run:  python examples/graph_design/sophisticated_demo.py
Out:  examples/graph_design/out/sophisticated-*.html
"""

from __future__ import annotations

import json
from pathlib import Path

from kosika.design import palette, style, STYLES, PALETTES, node_gradient, link_gradient, glow_filter, drop_shadow_filter, Style
from kosika.layout import (
    DEMO_GRAPH,
    compute_layout,
    KIND_COLORS,
    route_points,
    STRATEGIES,
)
from kosika.motion import check_scene, to_svg

WIDTH, HEIGHT = 960, 640
MARGIN = 80
NODE_R = 26


# ------------------------------------------------------------------ scene builders


def _gradient_defs(style_obj: Style) -> list[dict]:
    """Build gradient definitions for each node kind + links."""

    grads = []
    for kind in style_obj.palette.kind_colors:
        g = node_gradient(style_obj.palette, kind)
        grads.append({
            "id": g.id,
            "kind": g.kind,
            "stops": [{"offset": s.offset, "color": s.color, "opacity": s.opacity} for s in g.stops],
            "cx": g.cx, "cy": g.cy, "r": g.r,
        })
    lg = link_gradient(style_obj.palette)
    grads.append({
        "id": lg.id,
        "kind": lg.kind,
        "stops": [{"offset": s.offset, "color": s.color, "opacity": s.opacity} for s in lg.stops],
        "x1": lg.x1, "y1": lg.y1, "x2": lg.x2, "y2": lg.y2,
    })
    return grads


def _filter_defs(style_obj: Style) -> list[dict]:
    """Build filter definitions."""

    filters = []
    if style_obj.glow_enabled:
        gf = glow_filter(style_obj.palette)
        filters.append({"id": gf.id, "xml": gf.xml})
    if style_obj.shadow_enabled:
        ds = drop_shadow_filter()
        filters.append({"id": ds.id, "xml": ds.xml})
    return filters


def _node_shape(node_id, x, y, kind, style_obj, use_gradient=True, glow=False):
    """Build a node shape with optional gradient fill and glow filter."""

    pal = style_obj.palette
    color = pal.kind_colors.get(kind, pal.accent)
    g = node_gradient(style_obj.palette, kind)
    fill = f"url(#grad-node-{kind})" if (use_gradient and style_obj.gradient_nodes) else color
    filter_ref = "filter-glow" if (glow and style_obj.glow_enabled) else ""
    if style_obj.shadow_enabled and not glow:
        filter_ref = "filter-shadow"
    elif glow and style_obj.glow_enabled:
        filter_ref = "filter-glow"
    return {
        "id": node_id,
        "kind": "circle",
        "x": x,
        "y": y,
        "size": style_obj.node_radius * 2,
        "color": color,
        "text": node_id,
        "fill": fill,
        "filter": filter_ref,
        "gradient": {
            "id": g.id,
            "kind": g.kind,
            "stops": [{"offset": s.offset, "color": s.color, "opacity": s.opacity} for s in g.stops],
            "cx": g.cx, "cy": g.cy, "r": g.r,
        },
        "dash": "",
        "source": "",
        "target": "",
        "points": [],
        "style": "straight",
    }


def _label_shape(node_id, x, y, style_obj):
    """Build a label text shape below the node."""

    pal = style_obj.palette
    return {
        "id": f"{node_id}__label",
        "kind": "text",
        "x": x,
        "y": y + style_obj.node_radius + 14,
        "size": style_obj.label_size,
        "color": pal.ink,
        "text": node_id,
        "fill": pal.ink,
        "filter": "",
        "gradient": None,
        "dash": "",
        "source": "",
        "target": "",
        "points": [],
        "style": "straight",
    }


def _link_shape(link_id, source, target, positions, style_obj, active=False):
    """Build a link shape with optional glow and dash."""

    pal = style_obj.palette
    color = pal.link_glow if active else pal.link_color
    width = style_obj.link_stroke_width_active if active else style_obj.link_stroke_width
    dash = "" if active else style_obj.link_dash
    fill = f"url(#grad-link)" if style_obj.gradient_nodes else color
    points = route_points(positions[source], positions[target], "straight")
    lg = link_gradient(style_obj.palette)
    return {
        "id": link_id,
        "kind": "link",
        "x": 0,
        "y": 0,
        "size": width,
        "color": color,
        "text": "",
        "fill": fill,
        "filter": "filter-glow" if (active and style_obj.glow_enabled) else "",
        "gradient": {
            "id": lg.id,
            "kind": lg.kind,
            "stops": [{"offset": s.offset, "color": s.color, "opacity": s.opacity} for s in lg.stops],
            "x1": lg.x1, "y1": lg.y1, "x2": lg.x2, "y2": lg.y2,
        },
        "dash": dash,
        "source": source,
        "target": target,
        "points": [list(p) for p in points],
        "style": "straight",
    }


def _token_shape(token_id, style_obj):
    """A travelling token (small circle) that rides along edges."""

    pal = style_obj.palette
    return {
        "id": token_id,
        "kind": "circle",
        "x": 0,
        "y": 0,
        "size": 12,
        "color": pal.glow,
        "text": "",
        "fill": pal.glow,
        "filter": "filter-glow" if style_obj.glow_enabled else "",
        "gradient": None,
        "dash": "",
        "source": "",
        "target": "",
        "points": [],
        "style": "straight",
    }


def _ripple_shape(ripple_id, x, y, style_obj):
    """A ripple ring that expands from a node on activation."""

    pal = style_obj.palette
    return {
        "id": ripple_id,
        "kind": "ring",
        "x": x,
        "y": y,
        "size": style_obj.node_radius * 2,
        "color": pal.glow,
        "text": "",
        "fill": "none",
        "filter": "filter-glow" if style_obj.glow_enabled else "",
        "gradient": None,
        "dash": "",
        "source": "",
        "target": "",
        "points": [],
        "style": "straight",
    }


def entry_moves(node_id, delay, style_obj):
    """Staggered elastic entrance: fade in + scale up from 0."""

    base = style_obj.node_radius * 2
    return [
        {"shape": node_id, "what": "fade", "to": 0, "start": 0, "seconds": 0,
         "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": []},
        {"shape": node_id, "what": "size", "to": 0, "start": 0, "seconds": 0,
         "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": []},
        {"shape": node_id, "what": "fade",
         "keys": [{"at": 0, "to": 0}, {"at": 1, "to": 1}],
         "start": delay, "seconds": style_obj.duration_node_appear,
         "ease": style_obj.ease_in, "repeat": 1, "color": "", "path": ""},
        {"shape": node_id, "what": "size",
         "keys": [{"at": 0, "to": 0}, {"at": 1, "to": base}],
         "start": delay, "seconds": style_obj.duration_node_appear,
         "ease": style_obj.ease_in, "repeat": 1, "color": "", "path": ""},
        {"shape": f"{node_id}__label", "what": "fade", "to": 0, "start": 0, "seconds": 0,
         "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": []},
        {"shape": f"{node_id}__label", "what": "fade",
         "keys": [{"at": 0, "to": 0}, {"at": 1, "to": 1}],
         "start": delay + 0.25, "seconds": 0.5,
         "ease": "both", "repeat": 1, "color": "", "path": ""},
    ]


def link_moves(link_id, delay, style_obj):
    """Fade in a link."""

    return [
        {"shape": link_id, "what": "fade", "to": 0, "start": 0, "seconds": 0,
         "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": []},
        {"shape": link_id, "what": "fade",
         "keys": [{"at": 0, "to": 0}, {"at": 1, "to": 1}],
         "start": delay, "seconds": style_obj.duration_edge_appear,
         "ease": "both", "repeat": 1, "color": "", "path": ""},
    ]


def token_moves(token_id, link_id, start, seconds, style_obj):
    """A token flows along a link's path."""

    return [
        {"shape": token_id, "what": "fade", "to": 0, "start": 0, "seconds": 0,
         "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": []},
        {"shape": token_id, "what": "fade", "to": 1,
         "start": start, "seconds": 0.15, "ease": "out", "repeat": 1, "color": "", "path": ""},
        {"shape": token_id, "what": "path", "path": link_id,
         "start": start + 0.15, "seconds": seconds, "ease": "linear", "repeat": 1,
         "color": "", "to": 0, "keys": []},
        {"shape": token_id, "what": "fade", "to": 0,
         "start": start + 0.15 + seconds, "seconds": 0.15, "ease": "in",
         "repeat": 1, "color": "", "path": ""},
    ]


def pulse_moves(node_id, start, style_obj):
    """A node pulses: glow ring expands and fades."""

    return [
        {"shape": node_id, "what": "size",
         "keys": [{"at": 0, "to": style_obj.node_radius * 2},
                  {"at": 0.5, "to": style_obj.node_radius * 3},
                  {"at": 1, "to": style_obj.node_radius * 2}],
         "start": start, "seconds": 1.2, "ease": "both", "repeat": 1,
         "color": "", "path": ""},
    ]


def ripple_moves(ripple_id, start, style_obj):
    """An expanding ring ripple from a node."""

    return [
        {"shape": ripple_id, "what": "fade", "to": 0, "start": 0, "seconds": 0,
         "ease": "linear", "repeat": 1, "color": "", "path": "", "keys": []},
        {"shape": ripple_id, "what": "fade", "to": 1,
         "start": start, "seconds": 0.1, "ease": "out", "repeat": 1, "color": "", "path": ""},
        {"shape": ripple_id, "what": "size",
         "keys": [{"at": 0, "to": style_obj.node_radius * 2},
                  {"at": 1, "to": style_obj.halo_radius * 2}],
         "start": start, "seconds": 1.0, "ease": "out", "repeat": 1,
         "color": "", "path": ""},
        {"shape": ripple_id, "what": "fade", "to": 0,
         "start": start + 0.8, "seconds": 0.4, "ease": "in", "repeat": 1, "color": "", "path": ""},
    ]


def build_sophisticated_scene(layout, graph, style_name="dark", strategy_name="layered"):
    """Build a fully choreographed motion scene."""

    nodes, edges = graph
    style_obj = style(style_name)
    pal = style_obj.palette

    shapes: list[dict] = []
    moves: list[dict] = []

    # Definitions (gradients + filters) are scene-level
    scene_filters = _filter_defs(style_obj)

    # Nodes + labels
    for index, (node_id, label, kind) in enumerate(nodes):
        x, y = layout.positions[node_id]
        delay = index * style_obj.stagger_node
        shapes.append(_node_shape(node_id, x, y, kind, style_obj))
        shapes.append(_label_shape(node_id, x, y, style_obj))
        moves.extend(entry_moves(node_id, delay, style_obj))

    # Links
    for index, (source, target, _kind) in enumerate(edges):
        link_id = f"link{index}_{source}_{target}".replace(" ", "_")
        delay = len(nodes) * style_obj.stagger_node + index * style_obj.stagger_edge
        shapes.append(_link_shape(link_id, source, target, layout.positions, style_obj))
        moves.extend(link_moves(link_id, delay, style_obj))

    # The travelling token
    shapes.append(_token_shape("token", style_obj))

    # Token travels along each edge in sequence
    token_start = len(nodes) * style_obj.stagger_node + len(edges) * style_obj.stagger_edge + 0.6
    for index, (source, target, _kind) in enumerate(edges):
        link_id = f"link{index}_{source}_{target}".replace(" ", "_")
        moves.extend(token_moves("token", link_id, token_start + index * 1.5,
                                 style_obj.duration_token_travel, style_obj))

    # Final pulse on the last node
    last_node = nodes[-1][0]
    last_x, last_y = layout.positions[last_node]
    ripple_id = f"{last_node}__ripple"
    shapes.append(_ripple_shape(ripple_id, last_x, last_y, style_obj))
    moves.extend(pulse_moves(last_node, token_start + len(edges) * 1.5 + 0.5, style_obj))
    moves.extend(ripple_moves(ripple_id, token_start + len(edges) * 1.5 + 0.5, style_obj))

    total_duration = token_start + len(edges) * 1.5 + 2.5

    scene = {
        "width": WIDTH, "height": HEIGHT, "seconds": total_duration,
        "background": pal.paper,
        "shapes": shapes,
        "moves": moves,
        "filters": scene_filters,
    }
    problems = check_scene(scene)
    if problems:
        raise ValueError(f"Scene cannot play: {problems[0]}")
    return scene


# ------------------------------------------------------------------ demo output


def _wrap_with_controls(svg: str, title: str, subtitle: str) -> str:
    """Wrap an SVG in a full HTML page with playback controls."""

    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head><meta charset=\"utf-8\"><title>" + title + "</title>\n"
        "<style>\n"
        "html,body{margin:0;height:100%;background:#0b0c10;color:#cbd5e1;font-family:system-ui,sans-serif}\n"
        "header{padding:24px 32px;border-bottom:1px solid #1e293b}\n"
        "h1{margin:0;font-size:20px;letter-spacing:-0.02em}\n"
        "p.sub{margin:6px 0 0;font-size:13px;color:#64748b}\n"
        "main{height:calc(100% - 140px);display:grid;place-items:center;position:relative}\n"
        "svg{max-width:95vw;max-height:82vh;width:960px;height:640px;display:block}\n"
        "footer{padding:16px 32px;border-top:1px solid #1e293b;display:flex;gap:16px;align-items:center;justify-content:space-between}\n"
        "button{background:#1e293b;border:1px solid #334155;color:#cbd5e1;padding:8px 14px;border-radius:6px;cursor:pointer;font-size:13px}\n"
        "button:hover{background:#334155}\n"
        "button:disabled{opacity:0.5;cursor:not-allowed}\n"
        "label{font-size:12px;color:#64748b;display:flex;align-items:center;gap:8px}\n"
        "input[type=range]{flex:1}\n"
        ".badge{display:inline-block;padding:2px 8px;border:1px solid #334155;border-radius:10px;font-size:11px;margin-right:8px}\n"
        ".controls{display:flex;gap:10px;align-items:center}\n"
        ".playing .pause{display:none}\n"
        ".paused .play{display:none}\n"
        "</style>\n"
        "</head>\n"
        "<body class=\"paused\">\n"
        "<header>\n"
        "<h1>" + title + "</h1>\n"
        "<p class=\"sub\">" + subtitle + "</p>\n"
        "</header>\n"
        "<main>\n"
        + svg +
        "\n</main>\n"
        "<footer>\n"
        "<div class=\"controls\">\n"
        "<button class=\"play\" onclick=\"togglePlay(this)\">&#9658;</button>\n"
        "<button class=\"pause\" onclick=\"togglePlay(this)\">&#10074;&#10074;</button>\n"
        "<label>scrub <input type=\"range\" id=\"scrub\" min=\"0\" max=\"100\" value=\"0\"/></label>\n"
        "<span class=\"badge\">LAYOUT</span>\n"
        "<span class=\"badge\">STYLE</span>\n"
        "</div>\n"
        "</footer>\n"
        "<script>\n"
        "const svg = document.querySelector('svg');\n"
        "const scrub = document.getElementById('scrub');\n"
        "let playing = false;\n"
        "function togglePlay(btn) {\n"
        "  playing = !playing;\n"
        "  document.body.className = playing ? 'playing' : 'paused';\n"
        "  btn.textContent = playing ? '&#10074;&#10074;' : '&#9658;';\n"
        "}\n"
        "scrub.addEventListener('input', () => {\n"
        "  // Seek by setting animation time via CSS (SMIL seek is not directly supported)\n"
        "  // This is a visual scrub placeholder; real seeking requires SMIL setAttribute\n"
        "  const t = scrub.value / 100;\n"
        "  svg.style.animationPlayState = 'paused';\n"
        "});\n"
        "</script>\n"
        "</body>\n</html>"
    )


def main() -> None:
    out = Path(__file__).resolve().parent / "out"
    out.mkdir(parents=True, exist_ok=True)

    graph = DEMO_GRAPH

    for strategy_name in STRATEGIES:
        for style_name in ("dark", "neon", "minimal"):
            layout = compute_layout(graph, strategy=strategy_name)
            scene = build_sophisticated_scene(layout, graph, style_name=style_name, strategy_name=strategy_name)
            svg = to_svg(scene)
            controlled = _wrap_with_controls(
                svg, f"Kosika — {strategy_name} · {style_name}",
                f"{strategy_name.title()} layout · {style_name.title()} style · deterministic graph design"
            )
            fname = f"sophisticated-{strategy_name}-{style_name}.html"
            (out / fname).write_text(controlled, encoding="utf-8")
            print(f"{fname}: {len(scene['moves'])} moves, {len(scene['shapes'])} shapes, {len(scene['filters'])} filters")

    # Default: smart choice + dark style
    smart = "layered"
    layout = compute_layout(graph, strategy=smart)
    scene = build_sophisticated_scene(layout, graph, style_name="dark", strategy_name=smart)
    svg = to_svg(scene)
    controlled = _wrap_with_controls(
        svg, "Kosika — sophisticated graph design",
        "Layered layout · Dark style · gradients, filters, keyed curves, path animation"
    )
    (out / "sophisticated.html").write_text(controlled, encoding="utf-8")
    print(f"sophisticated.html written (smart choice: {smart})")


if __name__ == "__main__":
    main()
