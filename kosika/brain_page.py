"""Studio's Brain page: the Kosika Brain's network, live.

The facts are the Brain's timeline: every episode a Kosika part kept, in the
Brain's memory folder (KOSIKA_BRAIN_MEMORY, else Kosika AI's lessons folder,
~/.kosika/ai/lessons). This reads timeline.json, keeps the newest episodes,
and app_assets/brain.kos draws them with std/brain_net.kos, where every link
is as thick as it is strong: a check that passes strengthens it.

The drawing is kept until the timeline changes, so the page can ask every
few seconds and only pay for a new drawing when the Brain learned something.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

BRAIN_KOS = Path(__file__).parent / "app_assets" / "brain.kos"
NEWEST = int(os.environ.get("KOSIKA_BRAIN_PAGE_NEWEST", "40"))
FIELDS = ("part", "session", "seq", "at", "ask", "did", "outcome", "checked_by", "words")

_lock = threading.Lock()
_kept: dict[str, Any] = {}


def memory_folder() -> Path:
    told = os.environ.get("KOSIKA_BRAIN_MEMORY", "")
    if told:
        return Path(told).expanduser()
    from .ai_page import HOME

    return HOME / "lessons"


def timeline_file() -> Path:
    return memory_folder() / "timeline.json"


def episode_of(value: Any) -> dict[str, Any] | None:
    """One timeline entry as brain.kos's BrainEpisode, or None if it is not one."""

    if not isinstance(value, dict):
        return None
    seq = value.get("seq", 0)
    return {**{name: str(value.get(name, "")) for name in FIELDS if name != "seq"},
            "seq": seq if isinstance(seq, (int, float)) and not isinstance(seq, bool) else 0}


def episodes(path: Path | None = None) -> tuple[list[dict[str, Any]], int]:
    """The newest episodes, oldest first, and how many the timeline holds."""

    path = path or timeline_file()
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], 0
    if not isinstance(entries, dict):
        return [], 0
    found = [e for e in (episode_of(v) for v in entries.values()) if e is not None]
    found.sort(key=lambda e: (e["at"], e["seq"]))
    return found[-NEWEST:], len(found)


def draw(shown: list[dict[str, Any]], total: int) -> dict[str, Any]:
    import subprocess

    from .designs import _engine, _result

    with tempfile.TemporaryDirectory(prefix="kosika-brain-") as work:
        given = Path(work) / "ask.json"
        given.write_text(json.dumps({"a": {"episodes": shown, "total": total}}), encoding="utf-8")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        ran = subprocess.run(
            _engine() + ["run", str(BRAIN_KOS), "--workflow", "page", "--input", str(given),
                         "--max-function-calls", "200000"],
            capture_output=True, text=True, encoding="utf-8", timeout=240, cwd=work, creationflags=flags,
        )
    if ran.returncode != 0:
        raise RuntimeError((ran.stderr or ran.stdout).strip()[-400:] or "brain.kos gave no answer")
    return _result(ran.stdout, "title")


def page() -> dict[str, Any]:
    """The Brain's network page; drawn again only when the timeline changed."""

    path = timeline_file()
    try:
        changed = path.stat().st_mtime
    except OSError:
        changed = 0.0
    with _lock:
        if _kept and _kept.get("changed") == changed and _kept.get("path") == str(path):
            return dict(_kept)
        shown, total = episodes(path)
        try:
            drawn, error = draw(shown, total), ""
        except (RuntimeError, OSError, ValueError, SyntaxError) as exc:
            drawn, error = {}, f"brain.kos: {exc}"
        except Exception as exc:  # a timeout or a broken engine must not take Studio down
            drawn, error = {}, f"brain.kos: {exc}"
        made = {"title": "Kosika Brain", "page": "", "svg": "", "episodes": len(shown), "total": total,
                "passed": 0, "failed": 0, "unchecked": 0, "links": 0, "summary": "", "kind_stopped": 0, "kind": "",
                **drawn, "ok": not error, "error": error, "changed": changed, "path": str(path)}
        if not error:
            _kept.clear()
            _kept.update(made)
        return made
