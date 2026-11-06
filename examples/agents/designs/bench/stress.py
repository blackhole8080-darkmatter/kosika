"""Stress the three drawing varieties with deliberately hard asks.

    python examples/agents/designs/bench/stress.py [--only ids] [--keep folder]

Each ask names its variety, because these test how much a variety can hold,
not how well Kosika chooses one (that is run.py --choices). Kosika Brain is
off for these runs: no kept canvas is reused, so every drawing is made now.

For each drawing it reports what was drawn and what went wrong:

  labels    how many words are on the picture, and how many are empty,
            repeated, or a placeholder ("?", "undefined", "None")
  boxes     labels whose estimated box leaves the picture
  overlaps  pairs of labels whose estimated boxes collide
  fixed     slips Kosika repaired instead of refusing
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("KOSIKA_BRAIN", "0")

from kosika.designs import DesignsError, design  # noqa: E402

ASKS = [
    # designs: the diagram has to hold many parts, long labels and a chain
    {"id": "d1", "kind": "diagram", "text": "the whole human digestive system, from mouth to colon, and how food moves through every organ"},
    {"id": "d2", "kind": "diagram", "text": "how a database commits a transaction, step by step, through the write-ahead log, fsync and the commit record"},
    # canvas: a mechanism plus a moving picture, so both halves must survive
    {"id": "c1", "kind": "canvas", "text": "how a nuclear reactor makes electricity, from fission in the core to the grid"},
    {"id": "c2", "kind": "canvas", "text": "how the immune system answers a viral infection, from first contact to memory cells"},
    # art: many layered figures, where crowding and clipping show first
    {"id": "a1", "kind": "art", "text": "a thunderstorm over a city at night, lightning reflected in wet streets, rain in sheets"},
    {"id": "a2", "kind": "art", "text": "a coral reef in deep water at dawn, currents bending the weed, schooling fish, shafts of light"},
]

PLACEHOLDERS = {"", "?", "??", "undefined", "none", "null", "n/a", "na", "-", "todo", "tbd", "..."}


def labels_of(svg: str, size: float = 14.0) -> list[dict]:
    """Every <text> in the drawing, with an estimated box around it."""
    found = []
    for node in re.finditer(r"<text\b([^>]*)>(.*?)</text>", svg, re.S):
        attrs, body = node.group(1), re.sub(r"<[^>]+>", "", node.group(2))
        body = re.sub(r"&[a-z]+;|&#\d+;", "", body).strip()
        x = re.search(r'\bx="(-?[\d.]+)"', attrs)
        y = re.search(r'\by="(-?[\d.]+)"', attrs)
        fs = re.search(r'font-size="([\d.]+)"', attrs)
        if not (x and y) or not body:
            continue
        font = float(fs.group(1)) if fs else size
        width = 0.56 * font * len(body)
        anchor = "middle" if 'text-anchor="middle"' in attrs else "start"
        left = float(x.group(1)) - (width / 2 if anchor == "middle" else 0)
        found.append({
            "text": body, "x": float(x.group(1)), "y": float(y.group(1)),
            "left": left, "right": left + width, "top": float(y.group(1)) - font,
            "bottom": float(y.group(1)) + font * 0.25,
        })
    return found


def drawing_svg(embed: str) -> str:
    """The drawing's own <svg>, cut where its last tag closes.

    A drawing may hold svgs of its own (a small picture inside a card), so a
    lazy match would stop at the first nested close and measure a fragment.
    """
    start = re.search(r'<svg class="[^"]*\bart\b[^"]*"', embed) or re.search(r"<svg\b", embed)
    if not start:
        return ""
    depth = 0
    for tag in re.finditer(r"<svg\b|</svg>", embed[start.start():]):
        depth += -1 if tag.group(0) == "</svg>" else 1
        if depth == 0:
            return embed[start.start(): start.start() + tag.end()]
    return embed[start.start():]


def look(embed: str) -> dict:
    """What a drawing holds, and what is visibly wrong with it.

    An embed may hold its page in an iframe's srcdoc, where every tag is
    escaped; unescaping first measures the drawing rather than the wrapper.
    """
    svg = drawing_svg(html.unescape(embed)) or drawing_svg(embed)
    box = re.search(r'viewBox="(-?[\d.]+) (-?[\d.]+) ([\d.]+) ([\d.]+)"', svg)
    w, h = (float(box.group(3)), float(box.group(4))) if box else (0.0, 0.0)
    words = labels_of(svg)
    texts = [w["text"] for w in words]
    seen: dict[str, int] = {}
    for t in texts:
        seen[t] = seen.get(t, 0) + 1
    # Each label against the picture's width and height: a word half off the
    # edge is drawn but cannot be read.
    outside = [one["text"] for one in words
               if one["left"] < -2 or one["right"] > w + 2 or one["top"] < -2 or one["bottom"] > h + 2]
    # Two labels collide when their estimated boxes share more than a sliver.
    collide = 0
    for i, one in enumerate(words):
        for two in words[i + 1:]:
            across = min(one["right"], two["right"]) - max(one["left"], two["left"])
            down = min(one["bottom"], two["bottom"]) - max(one["top"], two["top"])
            if across > 2 and down > 2:
                collide += 1
    return {
        "svg": len(svg),
        "nested": max(0, len(re.findall(r"<svg\b", svg)) - 1),
        "viewBox": [w, h],
        "elements": len(re.findall(r"<(?:g|rect|circle|path|line|polygon|ellipse|text)\b", svg)),
        "groups": len(re.findall(r"<g\b", svg)),
        "cards": len(re.findall(r'class="[^"]*\bcard\b', svg)) + len(re.findall(r'class="[^"]*\bpart\b', svg)),
        "links": len(re.findall(r'class="[^"]*\b(?:link|wire)\b', svg)),
        "labels": len(texts),
        "words": sum(len(t.split()) for t in texts),
        "longest": max((len(t) for t in texts), default=0),
        "empty": sum(1 for t in texts if t.lower() in PLACEHOLDERS),
        "repeated": sum(1 for t, n in seen.items() if n > 1),
        "outside": outside[:6],
        "overlaps": collide,
        "drawing": bool(svg),
    }


def one(ask: dict, keep: Path) -> dict:
    started = time.monotonic()
    try:
        made = design(ask["text"], place="agent", kind=ask["kind"])
        problems = made["problems"]
    except DesignsError as exc:
        made, problems = {"kind": "", "title": "", "why": "", "page": "", "embed": "", "fixed": []}, [str(exc)]
    seconds = round(time.monotonic() - started, 1)
    embed, page = made.get("embed", ""), made.get("page", "")
    if embed:
        (keep / f"{ask['id']}.embed.html").write_text(embed, encoding="utf-8")
    if page:
        (keep / f"{ask['id']}.html").write_text(page, encoding="utf-8")
    seen = look(embed) if embed else {"drawing": False}
    row = {
        "id": ask["id"], "asked": ask["kind"], "text": ask["text"],
        "got": made.get("kind", ""), "right": made.get("kind", "") == ask["kind"],
        "title": made.get("title", ""), "why": made.get("why", "")[:300],
        "problems": problems[:4], "fixed": len(made.get("fixed", [])),
        "seconds": seconds,
        "canvas_whole": bool("kd-frame" in embed and re.search(r'<svg class="[^"]*\bart\b', html.unescape(embed))) if ask["kind"] == "canvas" else None,
        **seen,
    }
    print(f"{row['id']} {row['got'] or '-':12} {seconds:6}s  labels={row.get('labels', 0):3} "
          f"empty={row.get('empty', 0)} rep={row.get('repeated', 0)} out={len(row.get('outside', []))} "
          f"ov={row.get('overlaps', 0)} fixed={row['fixed']} {('PROBLEM ' + problems[0][:80]) if problems else ''}",
          flush=True)
    return row


def measure_files(asks: list[dict], keep: Path) -> int:
    """Measure the drawn pages already on disk, with the same checks."""
    rows = []
    for ask in asks:
        path = keep / f"{ask['id']}.embed.html"
        if not path.exists():
            print(f"{ask['id']}  not drawn")
            continue
        row = {"id": ask["id"], "asked": ask["kind"], "text": ask["text"], **look(path.read_text(encoding="utf-8"))}
        rows.append(row)
        print(f"{row['id']} {row['viewBox'][0]:.0f}x{row['viewBox'][1]:.0f} elements={row['elements']:4} "
              f"cards={row['cards']:3} links={row['links']:3} labels={row['labels']:3} words={row['words']:4} "
              f"empty={row['empty']} rep={row['repeated']} out={len(row['outside'])} ov={row['overlaps']}", flush=True)
    (keep / "analysis.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default="")
    parser.add_argument("--keep", default=str(HERE / "stress"))
    parser.add_argument("--from-files", action="store_true", help="measure the drawings already in --keep, without asking again")
    args = parser.parse_args()
    asks = ASKS
    if args.only:
        wanted = set(args.only.split(","))
        asks = [a for a in asks if a["id"] in wanted]
    keep = Path(args.keep)
    keep.mkdir(parents=True, exist_ok=True)
    if args.from_files:
        return measure_files(asks, keep)
    rows = [one(ask, keep) for ask in asks]
    report = keep / "report.json"
    before = json.loads(report.read_text(encoding="utf-8")) if report.exists() else []
    report.write_text(json.dumps(before + rows, indent=1), encoding="utf-8")
    print(json.dumps({
        "asks": len(rows),
        "drawn": sum(1 for r in rows if r.get("drawing")),
        "right_kind": sum(1 for r in rows if r["right"]),
        "fixed": sum(r["fixed"] for r in rows),
        "median_seconds": sorted(r["seconds"] for r in rows)[len(rows) // 2] if rows else 0,
        "canvases_whole": sum(1 for r in rows if r["canvas_whole"]),
        "label_problems": sum(r.get("empty", 0) + r.get("repeated", 0) + len(r.get("outside", [])) for r in rows),
        "overlaps": sum(r.get("overlaps", 0) for r in rows),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
