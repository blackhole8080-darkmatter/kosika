"""Side by side: the cloud run and the local 7B run, ask by ask.

    python examples/agents/designs/bench/stress/compare_runs.py

Reads the report.json each run kept (openrouter/ and local7b/) and prints what
each model drew for the same six asks, then the first line of every problem
the local run reported. Nothing here asks a model anything.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
IDS = ("d1", "d2", "c1", "c2", "a1", "a2")


def load(folder: str) -> dict[str, dict]:
    path = HERE / folder / "report.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    # A run appends, so the same id can appear twice; the last one is the run
    # that finished, and an earlier failed attempt stays in its own folder.
    return {row["id"]: row for row in rows}


def cell(rows: dict[str, dict], ident: str) -> str:
    row = rows.get(ident)
    if row is None:
        return "not drawn"
    got = row.get("got") or "-"
    return (
        f"{got:9} {row['seconds']:4.0f}s labels={row.get('labels', 0):<3} "
        f"empty={row.get('empty', 0)} rep={row.get('repeated', 0)} "
        f"out={len(row.get('outside') or [])} ov={row.get('overlaps', 0)}"
    )


def main() -> int:
    cloud, local = load("openrouter"), load("local7b")
    if not local:
        print("local7b/report.json is not there yet")
    for ident in IDS:
        ask = (cloud.get(ident) or local.get(ident) or {}).get("asked", "?")
        print(f"\n{ident}  {ask}")
        print(f"  cloud 550B  {cell(cloud, ident)}")
        print(f"  local 7B    {cell(local, ident)}")

    print("\nlocal problems, first line each:")
    any_problem = False
    for ident in IDS:
        row = local.get(ident) or {}
        for problem in row.get("problems") or []:
            any_problem = True
            print(f"  {ident}: {problem.splitlines()[0][:160]}")
    if not any_problem:
        print("  (none reported)")

    print("\ndrawn/right kind:")
    for name, rows in (("cloud", cloud), ("local", local)):
        drawn = sum(1 for ident in IDS if (rows.get(ident) or {}).get("drawing"))
        right = sum(1 for ident in IDS if (rows.get(ident) or {}).get("right"))
        print(f"  {name:6} drawn={drawn}/6 right_kind={right}/6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
