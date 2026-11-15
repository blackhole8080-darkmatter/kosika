"""The two typefaces Kosika carries with it.

EB Garamond, a bookish serif, is for names and headings. Figtree, a clean
sans, is for everything else. Both are under the SIL Open Font License,
whose text sits beside each file, so they may be shipped with Kosika and
work with no internet and nothing installed.
"""

from __future__ import annotations

import sys
from pathlib import Path

FOLDER = Path(__file__).parent / "app_assets" / "fonts"
#: The family for names and headings, and the family for the rest.
DISPLAY = "EB Garamond"
TEXT = "Figtree"
#: The file each family is in. Each holds every weight of its family.
FILES = {DISPLAY: "EBGaramond.ttf", TEXT: "Figtree.ttf"}


def font_path(family: str) -> Path | None:
    """Where a family's file is, or None when it is not one of ours or the
    file has gone missing."""

    name = FILES.get(family)
    path = FOLDER / name if name else None
    return path if path is not None and path.is_file() else None


def served(name: str) -> bytes | None:
    """A font file by its file name, for Studio's page to ask for. Only the
    files listed above are ever given out."""

    if name not in FILES.values():
        return None
    try:
        return (FOLDER / name).read_bytes()
    except OSError:
        return None


def lend_to_this_program() -> list[str]:
    """Let this program, and only this program, use the two families by
    name, and say which could be lent. Nothing is installed: Windows
    forgets them when the program ends. Elsewhere it lends nothing."""

    if sys.platform != "win32":
        return []
    import ctypes

    lent = []
    for family in FILES:
        path = font_path(family)
        try:
            # 0x10 keeps the font private to this program.
            if path and ctypes.windll.gdi32.AddFontResourceExW(str(path), 0x10, 0):
                lent.append(family)
        except (AttributeError, OSError):
            pass
    return lent
