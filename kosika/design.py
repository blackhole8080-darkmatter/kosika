"""Kosika design system: themes, palettes, gradients, filters, and visual styles.

This module turns a raw layout into a captivating visual design:

- Named color palettes (ink, paper, accent, glow, surface)
- Linear and radial gradients for node and edge fills
- SVG filters: glow, drop-shadow, blur, glassmorphism
- Visual styles: minimal, neon, print, dark
- Port assignment: where edges attach to a node's rim

Everything is deterministic: same graph + same style = same picture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# ------------------------------------------------------------------ palettes


@dataclass(frozen=True)
class Palette:
    """A coherent color scheme for a graph design."""

    name: str
    paper: str          # background
    ink: str            # primary text / lines
    surface: str        # card / panel fill
    accent: str         # primary highlight
    glow: str           # bloom / neon color
    muted: str          # secondary text / borders
    node_fill: str      # default node fill
    node_stroke: str    # default node outline
    link_color: str     # default edge color
    link_glow: str      # edge glow when active
    kind_colors: dict[str, str]  # per-kind node colors


PALETTES: dict[str, Palette] = {
    "ink": Palette(
        name="ink",
        paper="#f6f7f3",
        ink="#26332b",
        surface="#ffffff",
        accent="#286b5d",
        glow="#4fd1a3",
        muted="#7d8a78",
        node_fill="#ffffff",
        node_stroke="#286b5d",
        link_color="#b9c2b2",
        link_glow="#286b5d",
        kind_colors={
            "workflow": "#286b5d",
            "function": "#3b5bdb",
            "model": "#b8860b",
            "tool": "#7d8a78",
            "record": "#8b5cf6",
        },
    ),
    "neon": Palette(
        name="neon",
        paper="#0a0a0f",
        ink="#e8eaf2",
        surface="#161620",
        accent="#00f0ff",
        glow="#3b82f6",
        muted="#64748b",
        node_fill="#161620",
        node_stroke="#00f0ff",
        link_color="#334155",
        link_glow="#00f0ff",
        kind_colors={
            "workflow": "#00f0ff",
            "function": "#a855f7",
            "model": "#f43f5e",
            "tool": "#22d3ee",
            "record": "#fbbf24",
        },
    ),
    "print": Palette(
        name="print",
        paper="#ffffff",
        ink="#1a1a1a",
        surface="#f5f5f5",
        accent="#4a4a4a",
        glow="#7d8a78",
        muted="#8a8a8a",
        node_fill="#ffffff",
        node_stroke="#4a4a4a",
        link_color="#d4d4d4",
        link_glow="#4a4a4a",
        kind_colors={
            "workflow": "#4a4a4a",
            "function": "#1a1a1a",
            "model": "#666666",
            "tool": "#8a8a8a",
            "record": "#b3b3b3",
        },
    ),
    "dark": Palette(
        name="dark",
        paper="#0b0c10",
        ink="#cbd5e1",
        surface="#1e293b",
        accent="#38bdf8",
        glow="#818cf8",
        muted="#64748b",
        node_fill="#1e293b",
        node_stroke="#38bdf8",
        link_color="#334155",
        link_glow="#38bdf8",
        kind_colors={
            "workflow": "#38bdf8",
            "function": "#818cf8",
            "model": "#f472b6",
            "tool": "#22d3ee",
            "record": "#fbbf24",
        },
    ),
}


def palette(name: str = "ink") -> Palette:
    return PALETTES[name]


# ------------------------------------------------------------------ gradients


@dataclass(frozen=True)
class GradientStop:
    offset: float  # 0..1
    color: str
    opacity: float = 1.0


@dataclass(frozen=True)
class Gradient:
    id: str
    kind: str  # "linear" or "radial"
    stops: tuple[GradientStop, ...]
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 1.0
    y2: float = 0.0
    cx: float = 0.5
    cy: float = 0.5
    r: float = 0.5


def node_gradient(palette: Palette, kind: str) -> Gradient:
    """A subtle radial gradient for a node, lit from the top-left."""

    base = palette.kind_colors.get(kind, palette.accent)
    lighter = _lighten(base, 0.35)
    return Gradient(
        id=f"grad-node-{kind}",
        kind="radial",
        stops=(
            GradientStop(0.0, lighter, 1.0),
            GradientStop(0.55, base, 0.9),
            GradientStop(1.0, _darken(base, 0.2), 0.7),
        ),
        cx=0.35,
        cy=0.35,
        r=0.7,
    )


def link_gradient(palette: Palette) -> Gradient:
    """A soft gradient for edges, brighter in the middle."""

    return Gradient(
        id="grad-link",
        kind="linear",
        stops=(
            GradientStop(0.0, palette.link_color, 0.5),
            GradientStop(0.5, palette.link_glow, 0.9),
            GradientStop(1.0, palette.link_color, 0.5),
        ),
        x1=0.0,
        y1=0.0,
        x2=1.0,
        y2=0.0,
    )


# ------------------------------------------------------------------ filters


@dataclass(frozen=True)
class Filter:
    id: str
    xml: str


def glow_filter(palette: Palette, color: str | None = None) -> Filter:
    """A soft outer glow (bloom) filter."""

    color = color or palette.glow
    return Filter(
        id="filter-glow",
        xml=(
            f'<filter id="filter-glow" x="-50%" y="-50%" width="200%" height="200%">'
            f'<feGaussianBlur stdDeviation="4" result="blur"/>'
            f'<feFlood flood-color="{color}" flood-opacity="0.55" result="color"/>'
            f'<feComposite in="color" in2="blur" operator="in" result="colored-blur"/>'
            f'<feMerge>'
            f'<feMergeNode in="colored-blur"/>'
            f'<feMergeNode in="SourceGraphic"/>'
            f"</feMerge>"
            f"</filter>"
        ),
    )


def drop_shadow_filter() -> Filter:
    """A subtle drop shadow for depth."""

    return Filter(
        id="filter-shadow",
        xml=(
            '<filter id="filter-shadow" x="-20%" y="-20%" width="140%" height="140%">'
            '<feGaussianBlur in="SourceAlpha" stdDeviation="2" result="blur"/>'
            '<feOffset in="blur" dx="1" dy="2" result="offset-blur"/>'
            '<feComponentTransfer>'
            '<feFuncA type="linear" slope="0.35"/>'
            '</feComponentTransfer>'
            '<feMerge>'
            '<feMergeNode in="offset-blur"/>'
            '<feMergeNode in="SourceGraphic"/>'
            '</feMerge>'
            '</filter>'
        ),
    )


def glass_filter() -> Filter:
    """A frosted-glass blur for background panels."""

    return Filter(
        id="filter-glass",
        xml=(
            '<filter id="filter-glass">'
            '<feGaussianBlur stdDeviation="6" result="blur"/>'
            '</filter>'
        ),
    )


# ------------------------------------------------------------------ styles


@dataclass(frozen=True)
class Style:
    """A complete visual style for a graph design."""

    name: str
    palette: Palette
    node_radius: float = 26
    node_stroke_width: float = 2.5
    link_stroke_width: float = 1.8
    link_stroke_width_active: float = 3.0
    link_dash: str | None = None  # e.g. "4 2" for dashed
    glow_enabled: bool = True
    shadow_enabled: bool = True
    gradient_nodes: bool = True
    label_size: float = 12
    label_color: str = ""
    halo_radius: float = 36  # active-state glow ring
    ease_in: str = "back"
    ease_out: str = "both"
    duration_node_appear: float = 0.7
    duration_edge_appear: float = 0.5
    duration_token_travel: float = 1.0
    stagger_node: float = 0.12
    stagger_edge: float = 0.06


STYLES: dict[str, Style] = {
    "minimal": Style(
        name="minimal",
        palette=palette("ink"),
        node_radius=22,
        node_stroke_width=2.0,
        link_stroke_width=1.6,
        glow_enabled=False,
        shadow_enabled=False,
        gradient_nodes=False,
        label_size=11,
        ease_in="both",
        ease_out="linear",
        duration_node_appear=0.5,
        duration_edge_appear=0.4,
        stagger_node=0.08,
        stagger_edge=0.04,
    ),
    "neon": Style(
        name="neon",
        palette=palette("neon"),
        node_radius=28,
        node_stroke_width=3.0,
        link_stroke_width=2.2,
        link_stroke_width_active=4.0,
        glow_enabled=True,
        shadow_enabled=False,
        gradient_nodes=True,
        label_size=13,
        label_color="#e8eaf2",
        ease_in="back",
        ease_out="both",
        duration_node_appear=0.8,
        duration_edge_appear=0.6,
        stagger_node=0.15,
        stagger_edge=0.08,
    ),
    "print": Style(
        name="print",
        palette=palette("print"),
        node_radius=24,
        node_stroke_width=1.5,
        link_stroke_width=1.2,
        glow_enabled=False,
        shadow_enabled=False,
        gradient_nodes=False,
        label_size=10,
        ease_in="linear",
        ease_out="linear",
        duration_node_appear=0.4,
        duration_edge_appear=0.3,
        stagger_node=0.06,
        stagger_edge=0.03,
    ),
    "dark": Style(
        name="dark",
        palette=palette("dark"),
        node_radius=26,
        node_stroke_width=2.5,
        link_stroke_width=2.0,
        link_stroke_width_active=3.5,
        glow_enabled=True,
        shadow_enabled=True,
        gradient_nodes=True,
        label_size=12,
        label_color="#cbd5e1",
        ease_in="back",
        ease_out="both",
        duration_node_appear=0.7,
        duration_edge_appear=0.5,
        stagger_node=0.12,
        stagger_edge=0.06,
    ),
}


def style(name: str = "dark") -> Style:
    return STYLES[name]


# ------------------------------------------------------------------ helpers


def _lighten(color: str, amount: float) -> str:
    """Lighten a hex color by `amount` (0..1)."""

    color = color.lstrip("#")
    r, g, b = (int(color[i : i + 2], 16) for i in (0, 2, 4))
    r = min(255, int(r + (255 - r) * amount))
    g = min(255, int(g + (255 - g) * amount))
    b = min(255, int(b + (255 - b) * amount))
    return f"#{r:02x}{g:02x}{b:02x}"


def _darken(color: str, amount: float) -> str:
    """Darken a hex color by `amount` (0..1)."""

    color = color.lstrip("#")
    r, g, b = (int(color[i : i + 2], 16) for i in (0, 2, 4))
    r = max(0, int(r * (1 - amount)))
    g = max(0, int(g * (1 - amount)))
    b = max(0, int(b * (1 - amount)))
    return f"#{r:02x}{g:02x}{b:02x}"


# ------------------------------------------------------------------ ports


def port_positions(node_count: int, radius: float) -> list[tuple[float, float]]:
    """Evenly spaced attachment points around a node's rim.

    Returns (x, y) offsets from the node center, one per incident edge.
    Deterministic: same count always yields the same ports.
    """

    if node_count == 0:
        return []
    if node_count == 1:
        return [(0.0, -radius)]
    return [
        (
            radius * math.cos(2 * math.pi * i / node_count - math.pi / 2),
            radius * math.sin(2 * math.pi * i / node_count - math.pi / 2),
        )
        for i in range(node_count)
    ]
