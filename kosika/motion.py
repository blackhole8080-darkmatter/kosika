"""An experiment: describe an animation in Kosika, check it, and play it.

A Kosika program does not draw. It can, however, *describe* a scene as plain
data: some shapes, and the moves each one makes. This module takes such a
description, says exactly what is wrong with it if anything is, and turns it
into one self-contained web file that any browser plays. Nothing is fetched
from anywhere and no script runs in it: the motion is SVG's own.

The description is one record with these fields. A program is free to name
its records as it likes; only the fields matter.

    scene:  width, height, seconds, background, shapes, moves
    shape:  id, kind, x, y, size, color, text
            kind is circle, square, ring or text
    move:   shape, what, to, start, seconds, ease
            and, if wanted, repeat and color
            what is x, y, size, turn, fade or color
            ease is linear, in, out, both, bounce or back

`x` and `y` are the centre of a shape. `turn` is in degrees. `fade` goes
from 0 (invisible) to 1. A `color` move changes to the colour in its `color`
field and ignores `to`. `bounce` overshoots a little and settles; `back`
goes there and returns. `repeat` is how many times the move is made: 1 if
left out, and 0 for as long as the page is open.

A shape that should appear later starts with a `fade` move to 0 lasting 0
seconds, or simply begins off the page.

It is an experiment, small on purpose, to find out whether describing
motion this way is worth growing.
"""

from __future__ import annotations

import math
import re
from typing import Any
from xml.sax.saxutils import escape, quoteattr

KINDS = ("circle", "square", "ring", "text", "link")
WHATS = ("x", "y", "size", "turn", "fade", "color", "path", "draw", "text")
LINK_STYLES = ("straight", "curved", "orthogonal")
LINK_COLOR = "#7d8a78"
_SMOOTH = "0.42 0 0.58 1"
EASES = {
    "linear": "0 0 1 1",
    "in": "0.42 0 1 1",
    "out": "0 0 0.58 1",
    "both": _SMOOTH,
    "bounce": _SMOOTH,
    "back": _SMOOTH,
}
MAX_SHAPES = 400
MAX_MOVES = 4000
MAX_SECONDS = 300
MAX_SIDE = 4000
MAX_REPEAT = 1000
_COLOR = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\Z")
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,40}\Z")
_GRADIENT_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_FILTER_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def _is_gradient(value: Any) -> bool:
    return isinstance(value, dict) and value.get("kind") in ("linear", "radial")


def _is_filter(value: Any) -> bool:
    return isinstance(value, dict) and "xml" in value and "id" in value


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_color(value: Any) -> bool:
    return isinstance(value, str) and bool(_COLOR.match(value))


def _is_link_points(value: Any) -> bool:
    """A route is two or more [x, y] number pairs; the first is the start."""

    if not isinstance(value, list) or len(value) < 2:
        return False
    for point in value:
        if (
            not isinstance(point, list)
            or len(point) != 2
            or not _number(point[0])
            or not _number(point[1])
        ):
            return False
    return True


def _times(move: dict[str, Any]) -> float:
    """How many times a move is made; 0 means without end."""

    value = move.get("repeat", 1)
    return float(value) if _number(value) else 1.0


def check_scene(scene: Any) -> list[str]:
    """Everything wrong with a scene, in words. Empty when it can be played."""

    if not isinstance(scene, dict):
        return ["A scene is a record with width, height, seconds, shapes and moves."]
    problems: list[str] = []
    for field, most in (
        ("width", MAX_SIDE),
        ("height", MAX_SIDE),
        ("seconds", MAX_SECONDS),
    ):
        value: Any = scene.get(field)
        if not _number(value) or not 0 < value <= most:
            problems.append(f"{field} must be a number above 0 and at most {most}.")
    if not _is_color(scene.get("background", "#000000")):
        problems.append("background must be a colour written like #1a1a1a.")
    shapes, moves = scene.get("shapes"), scene.get("moves")
    if not isinstance(shapes, list) or not shapes:
        return [*problems, "A scene needs at least one shape."]
    if not isinstance(moves, list):
        return [*problems, "moves must be a list, even an empty one."]
    if len(shapes) > MAX_SHAPES:
        problems.append(f"A scene may have at most {MAX_SHAPES} shapes.")
    if len(moves) > MAX_MOVES:
        problems.append(f"A scene may have at most {MAX_MOVES} moves.")
    seen: set[str] = set()
    for number, shape in enumerate(shapes[:MAX_SHAPES], 1):
        if not isinstance(shape, dict):
            problems.append(f"Shape {number} is not a record.")
            continue
        name = shape.get("id")
        label = (
            f"Shape {name!r}" if isinstance(name, str) and name else f"Shape {number}"
        )
        kind = shape.get("kind")
        if not isinstance(name, str) or not _ID.match(name):
            problems.append(
                f"{label} needs an id of letters, digits and _, starting with a letter."
            )
        elif name in seen:
            problems.append(f"Two shapes are both called {name!r}.")
        else:
            seen.add(name)
        if kind not in KINDS:
            problems.append(
                f"{label} has kind {kind!r}; use one of: {', '.join(KINDS)}."
            )
            continue
        if kind == "link":
            source = shape.get("source")
            target = shape.get("target")
            points = shape.get("points")
            style = shape.get("style")
            if not isinstance(source, str) or not source:
                problems.append(f"{label} needs a source vertex id.")
            if not isinstance(target, str) or not target:
                problems.append(f"{label} needs a target vertex id.")
            if source == target:
                problems.append(f"{label} links {source!r} to itself; self-loops are not drawn.")
            if not _is_link_points(points):
                problems.append(
                    f"{label} needs points: a list of at least two [x, y] number pairs."
                )
            if style not in LINK_STYLES:
                problems.append(
                    f"{label} has style {style!r}; use one of: {', '.join(LINK_STYLES)}."
                )
            if not _is_color(shape.get("color")):
                problems.append(f"{label} needs a colour written like #e8813a.")
            if not _number(shape.get("size")) or shape["size"] < 0:
                problems.append(f"{label} needs a stroke width (size) of 0 or above.")
        else:
            for field in ("x", "y", "size"):
                if not _number(shape.get(field)) or abs(shape[field]) > MAX_SIDE * 2:
                    problems.append(f"{label} needs a number for {field}.")
            if _number(shape.get("size")) and shape["size"] < 0:
                problems.append(f"{label} has a size below 0.")
            if not _is_color(shape.get("color")):
                problems.append(f"{label} needs a colour written like #e8813a.")
            text = shape.get("text", "")
            if not isinstance(text, str) or len(text) > 200:
                problems.append(
                    f"{label} has text that is not words of at most 200 characters."
                )
            elif kind == "text" and not text:
                problems.append(f"{label} is of kind text and has no text.")

        # Optional gradient and filter references (validated against scene-level collections).
        gradient = shape.get("gradient")
        if gradient is not None and not _is_gradient(gradient):
            problems.append(f"{label} has an invalid gradient definition.")
        filter_ref = shape.get("filter")
        if filter_ref is not None and not isinstance(filter_ref, str):
            problems.append(f"{label} filter must reference a filter id by name.")

    # Cross-references: a link may name vertices declared anywhere in the scene.
    shape_map: dict[str, dict[str, Any]] = {}
    for number, shape in enumerate(shapes[:MAX_SHAPES], 1):
        if isinstance(shape, dict) and shape.get("id"):
            shape_map[shape["id"]] = shape
        if not isinstance(shape, dict) or shape.get("kind") != "link":
            continue
        label = f"Shape {shape.get('id')!r}"
        for field in ("source", "target"):
            vertex = shape.get(field)
            if isinstance(vertex, str) and vertex not in seen:
                problems.append(f"{label} {field}s {vertex!r}, but no shape has that id.")
    length = scene.get("seconds") if _number(scene.get("seconds")) else 0
    for number, move in enumerate(moves[:MAX_MOVES], 1):
        if not isinstance(move, dict):
            problems.append(f"Move {number} is not a record.")
            continue
        label = f"Move {number} (of {move.get('shape')!r})"
        if move.get("shape") not in seen:
            problems.append(
                f"Move {number} moves {move.get('shape')!r}, and no shape has that id."
            )
        what = move.get("what")
        if what not in WHATS:
            problems.append(
                f"{label} changes {what!r}; use one of: {', '.join(WHATS)}."
            )
        if what == "color":
            if not _is_color(move.get("color")):
                problems.append(
                    f"{label} changes colour and needs a color written like #e8813a."
                )
        elif what == "path":
            path_id = move.get("path", "")
            if not isinstance(path_id, str) or not path_id:
                problems.append(f"{label} moves along a path but names no path id.")
            elif path_id not in seen:
                problems.append(
                    f"{label} moves along {path_id!r}, but no shape has that id."
                )
        elif not move.get("keys") and not _number(move.get("to")):
            problems.append(f"{label} needs a number to move to.")
        elif not move.get("keys") and what == "fade" and not 0 <= move["to"] <= 1:
            problems.append(f"{label} fades to {move['to']}; a fade is from 0 to 1.")
        elif not move.get("keys") and what == "size" and move["to"] < 0:
            problems.append(f"{label} shrinks below 0.")
        elif what == "draw":
            shp = shape_map.get(move.get("shape", ""))
            if shp is None or shp.get("kind") != "link":
                problems.append(f"{label} draw is only valid on links.")
            elif not _is_link_points(shp.get("points")):
                problems.append(f"{label} needs route points for draw-on animation.")
        elif what == "text":
            shp = shape_map.get(move.get("shape", ""))
            if shp is None or shp.get("kind") != "text":
                problems.append(f"{label} text is only valid on text shapes.")
        keys = move.get("keys")
        if keys and len(keys) > 0:
            if not isinstance(keys, list) or len(keys) < 2:
                problems.append(
                    f"{label} has keys but needs at least two keyframes."
                )
            else:
                for index, key in enumerate(keys):
                    if not isinstance(key, dict):
                        problems.append(f"{label} key {index} is not a record.")
                        continue
                    if not _number(key.get("at")) or not 0 <= key["at"] <= 1:
                        problems.append(
                            f"{label} key {index} has at {key.get('at')!r}; "
                            "a key time is from 0 to 1."
                        )
                    if not _number(key.get("to")):
                        problems.append(
                            f"{label} key {index} needs a number to move to."
                        )
                times = [key["at"] for key in keys if isinstance(key, dict) and _number(key.get("at"))]
                if times:
                    if times[0] > 0:
                        problems.append(f"{label} first key must start at time 0.")
                    if times[-1] < 1:
                        problems.append(f"{label} last key must end at time 1.")
                    for i in range(len(times) - 1):
                        if times[i] >= times[i + 1]:
                            problems.append(
                                f"{label} keys must be in increasing time order."
                            )
                            break
        repeat: Any = move.get("repeat", 1)
        if (
            not _number(repeat)
            or repeat != int(repeat)
            or not 0 <= repeat <= MAX_REPEAT
        ):
            problems.append(
                f"{label} repeats {repeat!r} times; use a whole number from 1 to "
                f"{MAX_REPEAT}, or 0 for without end."
            )
            repeat = 1
        start: Any = move.get("start")
        seconds: Any = move.get("seconds")
        if not _number(start) or start < 0 or not _number(seconds) or seconds < 0:
            problems.append(
                f"{label} needs a start and a number of seconds, neither below 0."
            )
        elif length and repeat and start + seconds * repeat > length + 1e-9:
            problems.append(
                f"{label} ends at {start + seconds * repeat:g} s, after the scene's "
                f"{length:g} s."
            )
        elif repeat == 0 and seconds == 0:
            problems.append(f"{label} repeats without end and lasts no time at all.")
        if move.get("ease", "both") not in EASES:
            problems.append(
                f"{label} has ease {move.get('ease')!r}; use one of: {', '.join(EASES)}."
            )
    return problems


def _n(value: float) -> str:
    return f"{round(float(value), 3):g}"


def _steps(start: Any, to: Any, ease: str) -> tuple[list[Any], str, str]:
    """The values a move passes through, when, and how it eases between them."""

    if ease == "back":
        return [start, to, start], "0;0.5;1", f"{_SMOOTH};{_SMOOTH}"
    if ease == "bounce" and not isinstance(start, str):
        reach = to - start
        return (
            [start, to + reach * 0.12, to - reach * 0.05, to],
            "0;0.6;0.82;1",
            f"0 0 0.58 1;{_SMOOTH};{_SMOOTH}",
        )
    return [start, to], "0;1", EASES[ease]


def _keyed_steps(keys: list[dict[str, Any]], ease: str) -> tuple[list[Any], str, str]:
    """Values/keyTimes/keySplines for a move that passes through several keyframes."""

    values = [float(key["to"]) for key in keys]
    key_times = ";".join(_n(key["at"]) for key in keys)
    spline = EASES[ease]
    key_splines = ";".join(spline for _ in range(len(keys) - 1))
    return values, key_times, key_splines


def _animate(shape: dict[str, Any], state: dict[str, Any], move: dict[str, Any]) -> str:
    """One move as an SVG animation, starting from where the shape then is."""

    what, ease = move["what"], move.get("ease", "both")
    keys = move.get("keys") or []
    if keys:
        values, key_times, splines = _keyed_steps(keys, ease)
        state[what] = values[-1]
    else:
        to: Any = move["color"] if what == "color" else float(move["to"])
        values, key_times, splines = _steps(state[what], to, ease)
        # A move that goes there and back leaves the shape where it was.
        state[what] = values[-1]
    times = _times(move)
    again = (
        ""
        if times == 1
        else f' repeatCount="{"indefinite" if times == 0 else _n(times)}"'
    )
    timing = (
        f'begin="{_n(move["start"])}s" dur="{_n(max(move["seconds"], 0.001))}s" '
        f'fill="freeze" calcMode="spline" keyTimes="{key_times}" '
        f'keySplines="{splines}"{again}'
    )
    if what == "color":
        paint = "stroke" if shape["kind"] == "ring" else "fill"
        return (
            f'<animate attributeName="{paint}" values="{";".join(values)}" {timing}/>'
        )
    if what == "fade":
        listed = ";".join(_n(value) for value in values)
        return f'<animate attributeName="opacity" values="{listed}" {timing}/>'
    if what == "size":
        # Size is a scale about the shape's own centre.
        base = float(shape["size"]) or 1.0
        kind, listed = "scale", ";".join(_n(value / base) for value in values)
    elif what == "turn":
        kind, listed = "rotate", ";".join(_n(value) for value in values)
    elif what == "x":
        kind, listed = "translate", ";".join(f"{_n(value)} 0" for value in values)
    elif what == "draw":
        # Draw-on: animate stroke-dashoffset from a large value to 0.
        listed = ";".join(_n(value) for value in values)
        return (
            f'<animate attributeName="stroke-dashoffset" values="{listed}" {timing}/>'
        )
    elif what == "text":
        # Typewriter: reveal by animating opacity of the text group.
        listed = ";".join(_n(value) for value in values)
        return f'<animate attributeName="opacity" values="{listed}" {timing}/>'
    else:
        kind, listed = "translate", ";".join(f"0 {_n(value)}" for value in values)
    return (
        f'<animateTransform attributeName="transform" type="{kind}" '
        f'values="{listed}" {timing}/>'
    )


def _animate_path(shape: dict[str, Any], move: dict[str, Any], paths: dict[str, str]) -> str:
    """Move a shape along a link's path using SVG animateMotion."""

    path_id = paths.get(move["path"], "")
    if not path_id:
        return ""
    times = _times(move)
    again = (
        ""
        if times == 1
        else f' repeatCount="{"indefinite" if times == 0 else _n(times)}"'
    )
    timing = (
        f'begin="{_n(move["start"])}s" dur="{_n(max(move["seconds"], 0.001))}s" '
        f'fill="freeze" rotate="auto"{again}'
    )
    return (
        f'<animateMotion {timing}>'
        f'<mpath xlink:href="#{path_id}"/>'
        f"</animateMotion>"
    )


def _draw(shape: dict[str, Any], inside: str, fill: str | None = None, filter_str: str = "") -> str:
    size, color = float(shape["size"]), shape["color"]
    half = _n(size / 2)
    fill = fill or color
    if shape["kind"] == "circle":
        return (
            f'<circle r="{half}" fill="{fill}"{filter_str}>{inside}</circle>'
        )
    if shape["kind"] == "ring":
        return (
            f'<circle r="{half}" fill="none" stroke="{color}" '
            f'stroke-width="{_n(max(size / 12, 1))}"{filter_str}>{inside}</circle>'
        )
    if shape["kind"] == "square":
        return (
            f'<rect x="-{half}" y="-{half}" width="{_n(size)}" height="{_n(size)}" '
            f'rx="{_n(size / 5)}" fill="{fill}"{filter_str}>{inside}</rect>'
        )
    if shape["kind"] == "link":
        return _draw_link(shape)
    return (
        f'<text font-family="system-ui, sans-serif" font-weight="700" '
        f'font-size="{_n(size)}" text-anchor="middle" dominant-baseline="central" '
        f'fill="{fill}"{filter_str}>{escape(shape.get("text", ""))}{inside}</text>'
    )


def _link_path(shape: dict[str, Any]) -> str:
    """One edge as an SVG path: straight/curved/orthogonal through its points."""

    points = shape["points"]
    style = shape.get("style", "straight")
    if style == "curved" and len(points) == 4:
        (ax, ay), (bx, by), (cx, cy), (dx, dy) = points
        return (
            f"M {_n(ax)} {_n(ay)} "
            f"C {_n(bx)} {_n(by)} {_n(cx)} {_n(cy)} {_n(dx)} {_n(dy)}"
        )
    # straight / orthogonal: a polyline through the route points
    parts = [f"M {_n(points[0][0])} {_n(points[0][1])}"]
    for x, y in points[1:]:
        parts.append(f"L {_n(x)} {_n(y)}")
    return " ".join(parts)


def _path_length(points: list[tuple[float, float]]) -> float:
    """Approximate the total length of a polyline through the given points."""

    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        total += math.hypot(x2 - x1, y2 - y1)
    return total


def _draw_link(shape: dict[str, Any], draw_offset: float | None = None) -> str:
    color = shape["color"]
    width = float(shape["size"])
    dash = shape.get("dash")
    # Draw-on: set dasharray to path length and dashoffset to the same value
    # so the line starts hidden and animates in.
    length = _path_length(shape["points"]) if draw_offset is not None else 0
    dasharray = f' stroke-dasharray="{_n(length)} {_n(length)}"' if draw_offset is not None else ""
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    dashoffset = f' stroke-dashoffset="{_n(draw_offset)}"' if draw_offset is not None else ""
    return (
        f'<path id={quoteattr(shape["id"])} d="{_link_path(shape)}" fill="none" stroke="{color}" '
        f'stroke-width="{_n(max(width, 0.5))}"{dasharray}{dash_attr}{dashoffset} '
        f'marker-end="url(#arrow)"/>'
    )


def _fill_attr(shape: dict[str, Any], default: str) -> str:
    """Resolve a shape's fill: a color, a gradient reference (url(#id)), or default."""

    fill = shape.get("fill", default)
    if isinstance(fill, str) and fill.startswith("url("):
        return fill
    if _is_color(fill):
        return fill
    return default


def _filter_attr(shape: dict[str, Any]) -> str:
    """Return the filter attribute string if the shape references a filter."""

    filter_ref = shape.get("filter")
    if isinstance(filter_ref, str) and filter_ref:
        return f' filter="url(#{filter_ref})"'
    return ""


def _gradient_xml(gradient: dict[str, Any]) -> str:
    """Render a gradient definition as SVG."""

    gid = gradient["id"]
    kind = gradient["kind"]
    stops = gradient.get("stops", [])
    if kind == "linear":
        x1 = gradient.get("x1", 0)
        y1 = gradient.get("y1", 0)
        x2 = gradient.get("x2", 1)
        y2 = gradient.get("y2", 0)
        parts = [
            f'<linearGradient id={quoteattr(gid)} gradientUnits="userSpaceOnUse" '
            f'x1="{_n(x1)}" y1="{_n(y1)}" x2="{_n(x2)}" y2="{_n(y2)}">'
        ]
    else:
        cx = gradient.get("cx", 0.5)
        cy = gradient.get("cy", 0.5)
        r = gradient.get("r", 0.5)
        parts = [
            f'<radialGradient id={quoteattr(gid)} '
            f'cx="{_n(cx)}" cy="{_n(cy)}" r="{_n(r)}">'
        ]
    for stop in stops:
        offset = stop.get("offset", 0)
        color = stop.get("color", "#000000")
        opacity = stop.get("opacity", 1.0)
        parts.append(
            f'<stop offset="{_n(offset * 100)}%" stop-color="{color}" '
            f'stop-opacity="{_n(opacity)}"/>'
        )
    parts.append("</" + ("linearGradient" if kind == "linear" else "radialGradient") + ">")
    return "".join(parts)


def _marker_xml(color: str) -> str:
    """An arrowhead marker definition."""

    return (
        f'<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{color}"/>'
        "</marker>"
    )


def _camera_xml(camera: dict[str, Any], width: float, height: float) -> str:
    """A slow camera drift: pan and zoom across the whole scene."""

    cx, cy = camera.get("cx", width / 2), camera.get("cy", height / 2)
    duration = camera.get("duration", max(width, height) / 200)
    moves = camera.get("moves", [])
    if not moves:
        # Default: slow drift with gentle zoom.
        moves = [
            {"at": 0, "tx": 0, "ty": 0, "s": 1.0},
            {"at": 0.5, "tx": width * 0.08, "ty": height * 0.05, "s": 1.15},
            {"at": 1.0, "tx": width * 0.04, "ty": height * 0.02, "s": 1.05},
        ]
    tx_values = ";".join(f"{_n(m['tx'])} {_n(m['ty'])}" for m in moves)
    scale_values = ";".join(_n(m["s"]) for m in moves)
    key_times = ";".join(_n(m["at"]) for m in moves)
    spline = EASES["both"]
    key_splines = ";".join(spline for _ in range(len(moves) - 1))
    timing = (
        f'begin="0s" dur="{_n(duration)}s" fill="freeze" repeatCount="indefinite" '
        f'calcMode="spline" keyTimes="{key_times}" keySplines="{key_splines}"'
    )
    return (
        f'<animateTransform attributeName="transform" type="translate" '
        f'values="{tx_values}" additive="sum" accumulate="sum" {timing}/>'
        f'<animateTransform attributeName="transform" type="scale" '
        f'values="{scale_values}" additive="sum" accumulate="sum" {timing}/>'
    )


def _atmosphere_defs(width: float, height: float, palette: dict[str, str]) -> list[str]:
    """Grid pattern + radial vignette for a moody, cinematic background."""

    ink = palette.get("ink", "#26332b")
    muted = palette.get("muted", "#7d8a78")
    return [
        # Subtle grid pattern.
        (
            '<pattern id="pattern-grid" width="60" height="60" patternUnits="userSpaceOnUse">'
            f'<path d="M 60 0 L 0 0 0 60" fill="none" stroke="{muted}" '
            f'stroke-width="0.5" stroke-opacity="0.3"/>'
            f'<path d="M 30 0 L 30 60" fill="none" stroke="{muted}" '
            f'stroke-width="0.5" stroke-opacity="0.15"/>'
            "</pattern>"
        ),
        # Radial vignette: bright center, dark edges.
        (
            f'<radialGradient id="grad-vignette" cx="50%" cy="50%" r="70%" '
            f'gradientUnits="userSpaceOnUse">'
            f'<stop offset="0%" stop-color="{ink}" stop-opacity="0.25"/>'
            f'<stop offset="60%" stop-color="{ink}" stop-opacity="0.55"/>'
            f'<stop offset="100%" stop-color="{ink}" stop-opacity="0.95"/>'
            "</radialGradient>"
        ),
    ]


def _palette_from_scene(scene: dict[str, Any]) -> dict[str, str]:
    """Infer a palette dict from the scene background and shapes."""

    bg = scene.get("background", "#0b0c10")
    # Pick a contrasting ink from the brightest shape color, or default.
    ink = "#cbd5e1"
    for shape in scene.get("shapes", []):
        color = shape.get("color", "")
        if _is_color(color):
            ink = color
            break
    return {"ink": ink, "muted": "#64748b", "background": bg}


def to_svg(scene: dict[str, Any], *, backdrop: bool = True) -> str:
    """The scene as one SVG picture that moves. The scene must check.
    Without `backdrop` the background is left out, so the picture can sit
    on whatever is behind it."""

    problems = check_scene(scene)
    if problems:
        raise ValueError("The scene cannot be played: " + problems[0])
    width, height = _n(scene["width"]), _n(scene["height"])
    background = scene.get("background", "#000000")
    has_links = any(shape.get("kind") == "link" for shape in scene["shapes"])
    svg_tag = (
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' xmlns:xlink="http://www.w3.org/1999/xlink"'
        if has_links else
        f'<svg xmlns="http://www.w3.org/2000/svg"'
    )
    parts = [
        f'{svg_tag} viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img">',
    ]
    # Collect gradients and filters from shapes and scene-level definitions.
    gradients: list[dict[str, Any]] = []
    filters: list[dict[str, Any]] = []
    seen_grad_ids: set[str] = set()
    seen_filt_ids: set[str] = set()
    for shape in scene["shapes"]:
        gradient = shape.get("gradient")
        if _is_gradient(gradient) and gradient["id"] not in seen_grad_ids:
            seen_grad_ids.add(gradient["id"])
            gradients.append(gradient)
        filter_ref = shape.get("filter")
        if isinstance(filter_ref, str) and filter_ref not in seen_filt_ids:
            seen_filt_ids.add(filter_ref)
    scene_filters = scene.get("filters") or []
    for filt in scene_filters:
        if _is_filter(filt) and filt["id"] not in seen_filt_ids:
            seen_filt_ids.add(filt["id"])
            filters.append(filt)

    defs_parts: list[str] = []
    for gradient in gradients:
        defs_parts.append(_gradient_xml(gradient))
    for filt in filters:
        defs_parts.append(filt["xml"])
    if has_links:
        defs_parts.append(_marker_xml(LINK_COLOR))
    # Atmosphere: grid + vignette.
    if scene.get("atmosphere", False):
        pal = _palette_from_scene(scene)
        defs_parts.extend(_atmosphere_defs(float(scene["width"]), float(scene["height"]), pal))
    if defs_parts:
        parts.append("<defs>" + "".join(defs_parts) + "</defs>")
    if backdrop:
        parts.append(f'<rect width="{width}" height="{height}" fill="{background}"/>')
        if scene.get("atmosphere", False):
            parts.append(
                f'<rect width="{width}" height="{height}" fill="url(#grad-vignette)"/>'
            )
            parts.append(
                f'<rect width="{width}" height="{height}" fill="url(#pattern-grid)" '
                f'opacity="0.15"/>'
            )
    # Camera drift wrapper.
    camera = scene.get("camera")
    cam_open = ""
    cam_close = ""
    if camera:
        cam_open = (
            f'<g transform="translate({_n(float(scene["width"]) / 2)} {_n(float(scene["height"]) / 2)})">'
            f"{_camera_xml(camera, float(scene['width']), float(scene['height']))}"
        )
        cam_close = "</g>"
    if cam_open:
        parts.append(cam_open)
    # Link shapes are addressable by animateMotion via xlink:href.
    paths = {shape["id"]: shape["id"] for shape in scene["shapes"] if shape.get("kind") == "link"}
    # Pre-compute draw offsets for links that have draw moves.
    draw_offsets: dict[str, float] = {}
    for shape in scene["shapes"]:
        if shape.get("kind") != "link":
            continue
        sid = shape["id"]
        for move in scene["moves"]:
            if move.get("shape") == sid and move.get("what") == "draw":
                draw_offsets[sid] = _path_length(shape["points"])
                break
    for shape in scene["shapes"]:
        if shape.get("kind") == "link":
            # A link draws its own absolute path; no translate/animation wrap.
            draw_offset = draw_offsets.get(shape["id"])
            parts.append(_draw_link(shape, draw_offset=draw_offset))
            continue
        state: dict[str, Any] = {
            "x": float(shape["x"]),
            "y": float(shape["y"]),
            "size": float(shape["size"]),
            "turn": 0.0,
            "fade": 1.0,
            "color": shape["color"],
        }
        mine = sorted(
            (move for move in scene["moves"] if move["shape"] == shape["id"]),
            key=lambda move: move["start"],
        )
        path_moves = [move for move in mine if move.get("what") == "path"]
        regular_moves = [move for move in mine if move.get("what") != "path"]
        made = {
            what: "".join(
                _animate(shape, state, move) for move in regular_moves if move["what"] == what
            )
            for what in WHATS
        }
        path_anims = "".join(
            _animate_path(shape, move, paths) for move in path_moves
        )
        fill = _fill_attr(shape, shape["color"])
        filter_str = _filter_attr(shape)
        # One group for each kind of move, so that going across, going up
        # and down, turning, growing and fading each have their own
        # transform and none undoes another. Within a group a later move
        # takes over from an earlier one.
        parts.append(
            f'<g id={quoteattr(shape["id"])} transform="translate({_n(shape["x"])} 0)">'
            f"{path_anims}"
            f"{made['x']}"
            f'<g transform="translate(0 {_n(shape["y"])})">{made["y"]}'
            f"<g>{made['turn']}<g>{made['size']}<g>{made['fade']}"
            f"{_draw(shape, made['color'], fill=fill, filter_str=filter_str)}"
            "</g></g></g></g></g>"
        )
    if cam_close:
        parts.append(cam_close)
    parts.append("</svg>")
    return "\n".join(parts)


def to_html(scene: dict[str, Any], title: str = "Kosika scene") -> str:
    """A whole web page holding the scene, with nothing fetched from anywhere."""

    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>{escape(title)}</title>"
        "<style>html,body{margin:0;height:100%;display:grid;place-items:center;"
        f"background:{scene.get('background', '#000000')}}}"
        "svg{max-width:100vw;max-height:100vh;height:auto}</style></head>\n<body>\n"
        f"{to_svg(scene)}\n</body></html>\n"
    )
