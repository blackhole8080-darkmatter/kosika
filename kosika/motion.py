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

import re
from typing import Any
from xml.sax.saxutils import escape, quoteattr

KINDS = ("circle", "square", "ring", "text")
WHATS = ("x", "y", "size", "turn", "fade", "color")
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
MAX_MOVES = 2000
MAX_SECONDS = 120
MAX_SIDE = 4000
MAX_REPEAT = 1000
_COLOR = re.compile(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\Z")
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,40}\Z")


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_color(value: Any) -> bool:
    return isinstance(value, str) and bool(_COLOR.match(value))


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
        if not isinstance(name, str) or not _ID.match(name):
            problems.append(
                f"{label} needs an id of letters, digits and _, starting with a letter."
            )
        elif name in seen:
            problems.append(f"Two shapes are both called {name!r}.")
        else:
            seen.add(name)
        if shape.get("kind") not in KINDS:
            problems.append(
                f"{label} has kind {shape.get('kind')!r}; use one of: {', '.join(KINDS)}."
            )
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
        elif shape.get("kind") == "text" and not text:
            problems.append(f"{label} is of kind text and has no text.")
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
        elif not _number(move.get("to")):
            problems.append(f"{label} needs a number to move to.")
        elif what == "fade" and not 0 <= move["to"] <= 1:
            problems.append(f"{label} fades to {move['to']}; a fade is from 0 to 1.")
        elif what == "size" and move["to"] < 0:
            problems.append(f"{label} shrinks below 0.")
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


def _animate(shape: dict[str, Any], state: dict[str, Any], move: dict[str, Any]) -> str:
    """One move as an SVG animation, starting from where the shape then is."""

    what, ease = move["what"], move.get("ease", "both")
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
    else:
        kind, listed = "translate", ";".join(f"0 {_n(value)}" for value in values)
    return (
        f'<animateTransform attributeName="transform" type="{kind}" '
        f'values="{listed}" {timing}/>'
    )


def _draw(shape: dict[str, Any], inside: str) -> str:
    size, color = float(shape["size"]), shape["color"]
    half = _n(size / 2)
    if shape["kind"] == "circle":
        return f'<circle r="{half}" fill="{color}">{inside}</circle>'
    if shape["kind"] == "ring":
        return (
            f'<circle r="{half}" fill="none" stroke="{color}" '
            f'stroke-width="{_n(max(size / 12, 1))}">{inside}</circle>'
        )
    if shape["kind"] == "square":
        return (
            f'<rect x="-{half}" y="-{half}" width="{_n(size)}" height="{_n(size)}" '
            f'rx="{_n(size / 5)}" fill="{color}">{inside}</rect>'
        )
    return (
        f'<text font-family="system-ui, sans-serif" font-weight="700" '
        f'font-size="{_n(size)}" text-anchor="middle" dominant-baseline="central" '
        f'fill="{color}">{escape(shape.get("text", ""))}{inside}</text>'
    )


def to_svg(scene: dict[str, Any]) -> str:
    """The scene as one SVG picture that moves. The scene must check."""

    problems = check_scene(scene)
    if problems:
        raise ValueError("The scene cannot be played: " + problems[0])
    width, height = _n(scene["width"]), _n(scene["height"])
    background = scene.get("background", "#000000")
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img">',
        f'<rect width="{width}" height="{height}" fill="{background}"/>',
    ]
    for shape in scene["shapes"]:
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
        made = {
            what: "".join(
                _animate(shape, state, move) for move in mine if move["what"] == what
            )
            for what in WHATS
        }
        # One group for each kind of move, so that going across, going up
        # and down, turning, growing and fading each have their own
        # transform and none undoes another. Within a group a later move
        # takes over from an earlier one.
        parts.append(
            f'<g id={quoteattr(shape["id"])} transform="translate({_n(shape["x"])} 0)">'
            f"{made['x']}"
            f'<g transform="translate(0 {_n(shape["y"])})">{made["y"]}'
            f"<g>{made['turn']}<g>{made['size']}<g>{made['fade']}"
            f"{_draw(shape, made['color'])}"
            "</g></g></g></g></g>"
        )
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
