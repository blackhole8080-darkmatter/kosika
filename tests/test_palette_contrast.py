"""Studio's colours must pass Kosika Designs' own rules.

Kosika Designs (``kosika/std/designs_palette.kos``) states what readable
means: text at least 7 to 1 against its background, softer text 4.5, and the
colours that carry meaning -- accents, gold, violet, group colours -- 3. A
boundary a person acts on, such as the edge of an input or a button, is a
meaningful colour too: the Web Content Accessibility Guidelines put it at 3
(1.4.11), and a decorative hairline is not, so it has a lower floor of its own
rather than being told it must be loud.

Studio's theme blocks are hand-written, so nothing has kept them true. This
test is that keeping: it reads every block in ``kosika/app_assets/index.html``
that defines a background, works out each pair the way ``contrast()`` does,
and fails naming the block and the pair.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STUDIO = Path(__file__).resolve().parents[1] / "kosika" / "app_assets" / "index.html"

#: What Kosika Designs asks of each token, as a contrast ratio against the
#: block's background. `line` is the decorative hairline; `line-strong` is the
#: edge of anything a person acts on.
RULES = {
    "ink": 7.0,
    "soft": 4.5,
    "accent": 3.0,
    "think": 3.0,
    "ok": 3.0,
    "bad": 3.0,
    "warn": 3.0,
    "mem": 3.0,
    "line-strong": 3.0,
    "line": 1.2,
}

#: The token that draws a boundary someone acts on must also be told apart
#: from the panel colour, because an input's edge sits on a panel.
ON_PANEL = ("line-strong",)

HEX = re.compile(r"#([0-9a-fA-F]{3,8})\b")
RGBA = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+)\s*)?\)")
BLOCK = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _linear(channel: int) -> float:
    value = channel / 255
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(rgb: tuple[int, int, int]) -> float:
    red, green, blue = rgb
    return 0.2126 * _linear(red) + 0.7152 * _linear(green) + 0.0722 * _linear(blue)


def contrast(one: tuple[int, int, int], two: tuple[int, int, int]) -> float:
    high, low = sorted((luminance(one), luminance(two)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def parse_colour(text: str) -> tuple[tuple[int, int, int], float] | None:
    """A colour as (channels, alpha), or None when it is not a plain colour."""

    match = HEX.search(text)
    if match:
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(character * 2 for character in digits)
        if len(digits) < 6:
            return None
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)), 1.0
    match = RGBA.search(text)
    if match:
        alpha = float(match.group(4)) if match.group(4) is not None else 1.0
        return (int(match.group(1)), int(match.group(2)), int(match.group(3))), alpha
    return None


def over(colour: tuple[int, int, int], alpha: float, background: tuple[int, int, int]):
    """A translucent colour as it lands on a background."""

    if alpha >= 1.0:
        return colour
    return tuple(
        round(fore * alpha + back * (1 - alpha))
        for fore, back in zip(colour, background)
    )


def boundaries() -> list[tuple[str, dict[str, str]]]:
    """Every theme block in Studio that sets a background, with its tokens."""

    page = STUDIO.read_text(encoding="utf-8", errors="replace")
    # A comment can sit between two rules; it is not part of the selector.
    page = re.sub(r"/\*.*?\*/", "", page, flags=re.S)
    found: list[tuple[str, dict[str, str]]] = []
    for selector, body in BLOCK.findall(page):
        if "--bg" not in body:
            continue
        tokens = dict(
            re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", body)
        )
        if "bg" not in tokens:
            continue
        selector = " ".join(selector.split())
        found.append((selector, {name: value.strip() for name, value in tokens.items()}))
    return found


def pairs() -> list[tuple[str, str, float, float]]:
    """(block, token, ratio, required) for every pair the rules cover."""

    measured: list[tuple[str, str, float, float]] = []
    for selector, tokens in boundaries():
        background = parse_colour(tokens["bg"])
        if background is None:
            continue
        panel = parse_colour(tokens.get("panel", ""))
        for token, least in RULES.items():
            if token not in tokens:
                continue
            parsed = parse_colour(tokens[token])
            if parsed is None:
                continue
            colour, alpha = parsed
            ratio = contrast(over(colour, alpha, background[0]), background[0])
            measured.append((selector, token, ratio, least))
            if token in ON_PANEL and panel is not None:
                on_panel = contrast(over(colour, alpha, panel[0]), panel[0])
                measured.append((f"{selector} (on panel)", token, on_panel, least))
    return measured


def test_the_studio_page_is_where_the_themes_are():
    assert STUDIO.is_file(), f"Studio's page is missing: {STUDIO}"


def test_every_theme_names_a_boundary_colour():
    """A theme that draws no `--line-strong` cannot meet the 3:1 boundary."""

    missing = [
        selector
        for selector, tokens in boundaries()
        if "ink" in tokens and "line-strong" not in tokens
    ]
    assert not missing, (
        "these Studio themes have no --line-strong, so the edge of an input "
        "or button falls back to the decorative hairline:\n  "
        + "\n  ".join(missing)
    )


def test_readable_pairs():
    failures = [
        f"{selector}: --{token}/bg is {ratio:.2f}, needs {least}"
        for selector, token, ratio, least in pairs()
        if ratio < least
    ]
    assert not failures, "Studio colours below Kosika Designs' floor:\n  " + "\n  ".join(
        failures
    )


@pytest.mark.parametrize("name,least", sorted(RULES.items()))
def test_each_rule_is_checked(name: str, least: float):
    """The gate covers every rule it claims to, not only the ones that pass."""

    assert least > 0
    assert any(token == name for _selector, token, _ratio, _least in pairs())
