"""Kosi's morning plan: the facts of the night, and morning.kos's plan.

The facts come from what is already on this laptop:

* the coder runs that finished since six last evening (kosika-daily);
* the changes waiting for a Keep, the open requests and who is at work
  (the same facts Home shows, kosika/home.py);
* today's research digest (KOSIKA_DIGEST_DIR, or kosika-bench/digest/digests
  under Aryan_Private): its papers, by title, field and link.

app_assets/morning.kos turns them into the plan. "Looks good" is kept in
~/.kosika/morning/DATE.json, so the plan is approved once a day, and starts
the swarm when the plan says to.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

from . import coders, home

MORNING_KOS = Path(__file__).parent / "app_assets" / "morning.kos"
#: The fields shown first from the digest; KOSIKA_INTERESTS="A,B" changes them.
INTERESTS = ["Machine learning", "Artificial intelligence", "Quantum physics", "Computation and language"]


def digest_folder() -> Path:
    asked = os.environ.get("KOSIKA_DIGEST_DIR")
    if asked:
        return Path(asked)
    return Path.home() / "Aryan_Private" / "kosika-bench" / "digest" / "digests"


def papers(folder: Path | None = None, most: int = 30) -> list[dict[str, str]]:
    """The papers of the newest digest: '### Title', then 'Field · date ·
    authors', then the arXiv link."""

    folder = folder or digest_folder()
    digests = sorted(folder.glob("*.md"), key=lambda p: p.stat().st_mtime) if folder.exists() else []
    if not digests:
        return []
    lines = digests[-1].read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[dict[str, str]] = []
    for i, line in enumerate(lines):
        if not line.startswith("### "):
            continue
        field, url = "", ""
        for after in lines[i + 1:i + 4]:
            if not field and " · " in after:
                field = after.split(" · ")[0].strip()
            found = re.search(r"https?://\S+", after)
            if not url and found:
                url = found.group(0)
        if url.startswith("https://arxiv.org/"):
            out.append({"title": line[4:].strip(), "field": field, "url": url})
        if len(out) >= most:
            break
    return out


def overnight(work: Path, since: datetime.datetime) -> list[dict[str, str]]:
    """Runs whose report was written since `since`, oldest first."""

    out = []
    for i, folder in enumerate(coders._runs(work)):
        report = folder / "report.md"
        if report.exists() and datetime.datetime.fromtimestamp(report.stat().st_mtime) >= since:
            run = coders.run_of(folder, i)
            out.append({"run": run["run"], "request": run["request"], "result": run["result"] or "failed"})
    return out


def approvals() -> Path:
    return Path(os.environ.get("KOSIKA_OVERLAY_HOME") or (Path.home() / ".kosika")) / "morning"


def approved(day: datetime.date) -> dict[str, Any] | None:
    try:
        return json.loads((approvals() / f"{day.isoformat()}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def facts(work: Path | None = None, now: datetime.datetime | None = None) -> dict[str, Any]:
    work = work or coders.work_folder()
    now = now or datetime.datetime.now()
    evening = datetime.datetime.combine(now.date() - datetime.timedelta(days=1), datetime.time(18))
    interests = [w.strip() for w in os.environ.get("KOSIKA_INTERESTS", "").split(",") if w.strip()] or INTERESTS
    return {"day": now.timetuple().tm_yday, "hour": now.hour, "person": home.person(),
            "overnight": overnight(work, evening), "waiting": len(home.waiting(work, most=50)),
            "open": sum(1 for item in coders.todo(work)["items"] if item["state"] == "open"), "working": len(home.working()), "papers": papers(), "interests": interests}


def plan(work: Path | None = None, now: datetime.datetime | None = None) -> dict[str, Any]:
    from .modules import load_program
    from .runtime import run

    told = facts(work, now)
    try:
        with tempfile.TemporaryDirectory(prefix="kosika-morning-") as scratch:
            made = run(load_program(MORNING_KOS), "morning", Path(scratch) / "run.jsonl", inputs={"m": told})
        shown = made.get("plan", made)
        ok, error = True, ""
    except (ValueError, RuntimeError, OSError) as exc:
        shown, ok, error = {"greeting": "Good morning", "summary": "", "steps": [], "approve": "Looks good"}, False, f"morning.kos: {exc}"
    day = (now or datetime.datetime.now()).date()
    return {**shown, "ok": ok, "error": error, "approved": approved(day), "date": day.isoformat()}


def approve(body: dict[str, Any], work: Path | None = None) -> dict[str, Any]:
    """'Looks good': kept for the day, and the swarm started if the plan says so."""

    today = datetime.date.today()
    shown = plan(work)
    started = 0
    if any(step.get("go") == "start" for step in shown.get("steps", [])):
        try:
            started = int(coders.start(int(body.get("size") or 3), work).get("started", 0))
        except (OSError, ValueError, RuntimeError):
            started = 0
    record = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "by": str(body.get("by") or "studio"),
              "started": started}
    approvals().mkdir(parents=True, exist_ok=True)
    (approvals() / f"{today.isoformat()}.json").write_text(json.dumps(record), encoding="utf-8")
    return record


# ------------------------------------------------------------- the phone

def phone_text(shown: dict[str, Any]) -> str:
    """The plan as a short phone notification: one line a step."""

    lines = [shown.get("summary", "")]
    for step in shown.get("steps", []):
        lines.append(f"- {step['title']}: {step['detail']}")
    lines.append("Open Kosika Studio and press \u201c" + str(shown.get("approve", "Looks good")) + "\u201d to approve.")
    return "\n".join(line for line in lines if line)


def topic_file() -> Path:
    asked = os.environ.get("KOSIKA_NTFY_TOPIC_FILE")
    return Path(asked) if asked else Path.home() / "Aryan_Private" / "kosika-bench" / "digest" / "topic.txt"


def send(shown: dict[str, Any] | None = None) -> int:
    """Send the plan to the phone, through the ntfy topic the research digest
    already uses. Returns the HTTP status."""

    import urllib.request

    topic = topic_file().read_text(encoding="utf-8").strip()
    if not topic:
        raise ValueError(f"No ntfy topic in {topic_file()}")
    shown = shown or plan()
    request = urllib.request.Request(f"https://ntfy.sh/{urllib.parse.quote(topic)}", data=phone_text(shown).encode("utf-8"),
                                     method="POST", headers={"Title": shown.get("greeting", "Good morning").encode("utf-8").decode("latin-1", "replace"),
                                                             "Tags": "sunrise", "User-Agent": "kosika-morning"})
    with urllib.request.urlopen(request, timeout=30) as answer:
        return answer.status


if __name__ == "__main__":
    import sys
    import urllib.parse  # noqa: F401  (used by send)

    if "--send" in sys.argv:
        print("sent:", send())
    else:
        print(phone_text(plan()))
