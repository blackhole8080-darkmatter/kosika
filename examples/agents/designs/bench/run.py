"""Score Kosika Designs on a fixed set of 30 asks.

    python examples/agents/designs/bench/run.py [--only a01,a02] [--label note]

Each ask in asks.json says what a person asked and which way of drawing
suits it. Canvas asks are asked for by name, since a canvas is chosen,
not guessed; every other ask is left to Kosika to choose. Each run is
scored on:

  chose    Kosika chose the way of drawing the ask wants (canvas asks
           always count, since they are named)
  drawn    a picture came back on the first call, with nothing refused
  whole    for a canvas, both the diagram and the moving picture are in it
  fixed    how many slips Kosika repaired itself instead of refusing
  seconds  how long the call took

Every run is added to history.jsonl, and scorecard.md shows the latest
beside the one before, so a change to Designs is a number, not a feeling.
The free model answers differently each time, so compare totals across a
few runs rather than one ask at a time.

Needs a free model, as kosika/designs.py says.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))

from kosika.designs import DesignsError, design  # noqa: E402


def limited(problems: list[str]) -> bool:
    """The free models said 'too many requests': not Kosika's doing."""
    said = " ".join(problems).lower()
    return "429" in said or "rate limit" in said or "rate_limit" in said


def score(ask: dict, keep: Path | None = None) -> dict:
    named = "canvas" if ask["want"] == "canvas" else ""
    # A rate limit is waited out, twice, before the ask is set aside as
    # not scored: it says nothing about Designs.
    for wait in (60, 180, 0):
        started = time.monotonic()
        try:
            made = design(ask["text"], place="agent", kind=named)
            problems = made["problems"]
        except DesignsError as exc:
            made, problems = {"kind": "", "title": "", "fixed": [], "embed": ""}, [str(exc)]
        seconds = round(time.monotonic() - started, 1)
        if not limited(problems) or not wait:
            break
        print(f"  {ask['id']}: the free models are rate-limited; waiting {wait} s", flush=True)
        time.sleep(wait)
    drawn = not problems and bool(made.get("embed"))
    if keep is not None and drawn:
        (keep / f"{ask['id']}.html").write_text(made["page"], encoding="utf-8")
        (keep / f"{ask['id']}.embed.html").write_text(made["embed"], encoding="utf-8")
    whole = None
    if ask["want"] == "canvas":
        embed = made.get("embed", "")
        whole = drawn and "kd-frame" in embed and "svg class=\"art\"" in embed
    return {
        "id": ask["id"],
        "text": ask["text"],
        "want": ask["want"],
        "got": made.get("kind", ""),
        "chose": made.get("kind", "") == ask["want"],
        "drawn": drawn,
        "whole": whole,
        "fixed": len(made.get("fixed", [])),
        "seconds": seconds,
        "title": made.get("title", ""),
        "problems": problems[:3],
        "limited": limited(problems),
    }


def totals(every: list[dict]) -> dict:
    rows = [r for r in every if not r.get("limited")]
    canvases = [r for r in rows if r["want"] == "canvas"]
    return {
        "asks": len(rows),
        "chose": sum(r["chose"] for r in rows),
        "drawn": sum(r["drawn"] for r in rows),
        "whole_canvases": sum(bool(r["whole"]) for r in canvases),
        "canvases": len(canvases),
        "fixed": sum(r["fixed"] for r in rows),
        "median_seconds": sorted(r["seconds"] for r in rows)[len(rows) // 2] if rows else 0,
        "rate_limited": len(every) - len(rows),
    }


def card(latest: dict, before: dict | None) -> str:
    t = latest["totals"]

    def line(name: str, key: str, out_of: str = "asks") -> str:
        was = f" (was {before['totals'][key]})" if before else ""
        whole = t[out_of] if out_of else ""
        return f"| {name} | {t[key]}{' / ' + str(whole) if whole != '' else ''}{was} |"

    out = [
        "# Kosika Designs scorecard",
        "",
        f"Run {latest['at']}" + (f", {latest['label']}" if latest.get("label") else "") + ".",
        "",
        "| Measure | Score |",
        "| --- | --- |",
        line("Kosika chose the right way to draw", "chose"),
        line("Drawn on the first call", "drawn"),
        line("Canvases with all their parts", "whole_canvases", "canvases"),
        line("Slips Kosika fixed itself", "fixed", ""),
        line("Median seconds per ask", "median_seconds", ""),
        f"| Not scored (free models rate-limited) | {t.get('rate_limited', 0)} |",
        "",
        "| Ask | Wanted | Got | Drawn | Seconds | What went wrong |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in latest["rows"]:
        wrong = "; ".join(r["problems"]).replace("|", "/")[:140]
        out.append(f"| {r['text']} | {r['want']} | {r['got'] or '-'} | {'yes' if r['drawn'] else 'no'} | {r['seconds']} | {wrong} |")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default="", help="comma-separated ask ids")
    parser.add_argument("--label", default="", help="a note for this run, such as what changed")
    parser.add_argument("--keep", default="", help="a folder to keep each drawing in, as a page and an embed")
    args = parser.parse_args()
    asks = json.loads((HERE / "asks.json").read_text(encoding="utf-8"))["asks"]
    if args.only:
        wanted = set(args.only.split(","))
        asks = [a for a in asks if a["id"] in wanted]
    keep = Path(args.keep) if args.keep else None
    if keep is not None:
        keep.mkdir(parents=True, exist_ok=True)
    rows = []
    for ask in asks:
        row = score(ask, keep)
        rows.append(row)
        mark = "drawn" if row["drawn"] else "limited" if row["limited"] else "refused"
        print(f"{row['id']} {mark:7} {row['got'] or '-':12} {row['seconds']:6}s  {ask['text']}", flush=True)
    if all(r.get("limited") for r in rows):
        print("Every ask was rate-limited: nothing was scored, and the history is left as it was.")
        return 1
    history = HERE / "history.jsonl"
    before = None
    if history.exists():
        lines = [line for line in history.read_text(encoding="utf-8").splitlines() if line.strip()]
        full = [json.loads(line) for line in lines]
        full = [run for run in full if run["totals"]["asks"] + run["totals"].get("rate_limited", 0) == len(rows)]
        before = full[-1] if full else None
    latest = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "label": args.label, "totals": totals(rows), "rows": rows}
    with history.open("a", encoding="utf-8") as f:
        f.write(json.dumps(latest) + "\n")
    (HERE / "scorecard.md").write_text(card(latest, before), encoding="utf-8")
    print(json.dumps(latest["totals"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
