"""Build "Kosika Studio Setup.exe": the installed app, in one file.

    python desktop/build.py            # from the repo root, on Windows

What it puts together (in desktop/build/stage, then zipped into the setup):

    Kosika Studio.exe         the native launcher (desktop/app), with the icon
    uninstall.exe             the uninstaller (desktop/setup, no payload)
    app/                      this repo's committed code (git archive HEAD),
                              stamped with app/VERSION
    runtime/python/           Python's embeddable build, with tkinter (for the
                              folder picker), tree-sitter and pytest
    engine/kosika-native.exe  the Go engine (native/)

Needs: Go, git, and a Python of the same minor version as PYTHON_VERSION
(its tkinter is copied in). Downloads Python's embeddable zip once, into
desktop/build/cache.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PYTHON_VERSION = "3.12.10"
HERE = Path(__file__).resolve().parent
REPO = HERE.parent
BUILD = HERE / "build"
STAGE = BUILD / "stage"
CACHE = BUILD / "cache"
PACKAGES = ["tree-sitter>=0.25,<0.27", "tree-sitter-rust>=0.24,<0.25", "pytest"]


def run(command: list[str], cwd: Path | None = None, env: dict | None = None) -> str:
    print("  $", " ".join(command))
    done = subprocess.run(
        command, cwd=cwd, env=env, check=True, capture_output=True, text=True
    )
    return done.stdout


def stamp() -> dict:
    sha = run(["git", "rev-parse", "HEAD"], REPO).strip()
    line = run(["git", "log", "-1", "--format=%cI%n%s"], REPO).splitlines()
    return {"sha": sha, "date": line[0], "message": line[1] if len(line) > 1 else ""}


def stage_app(version: dict) -> None:
    """The repo's files as they are on disk: tracked ones and new ones not
    ignored, so a build can be tried before it is committed."""
    print("app: the repo's code")
    listed = run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], REPO)
    target = STAGE / "app"
    for name in filter(None, listed.split(chr(0))):
        source = REPO / name
        if name.startswith("desktop/") or not source.is_file():
            continue
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    (target / "VERSION").write_text(json.dumps(version, indent=2), encoding="utf-8")


def stage_python() -> None:
    print(f"runtime: Python {PYTHON_VERSION}, embeddable")
    CACHE.mkdir(parents=True, exist_ok=True)
    name = f"python-{PYTHON_VERSION}-embed-amd64.zip"
    cached = CACHE / name
    if not cached.exists():
        url = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{name}"
        print("  downloading", url)
        urllib.request.urlretrieve(url, cached)
    target = STAGE / "runtime" / "python"
    with zipfile.ZipFile(cached) as bundle:
        bundle.extractall(target)
    major_minor = "".join(PYTHON_VERSION.split(".")[:2])
    # The ._pth file is the embeddable Python's whole search path: its own
    # library, Lib (tkinter), site-packages, and the app's code.
    (target / f"python{major_minor}._pth").write_text(
        "\n".join(
            [f"python{major_minor}.zip", ".", "Lib", r"Lib\site-packages", r"..\..\app", "import site", ""]
        ),
        encoding="utf-8",
    )
    # tkinter is not in the embeddable build; it is copied from this
    # computer's Python, which must be the same version.
    if sys.version_info[:2] != tuple(int(p) for p in PYTHON_VERSION.split(".")[:2]):
        raise SystemExit(f"Run this with Python {PYTHON_VERSION[:4]}, to copy its tkinter")
    base = Path(sys.base_prefix)
    shutil.copytree(base / "Lib" / "tkinter", target / "Lib" / "tkinter",
                    ignore=shutil.ignore_patterns("test", "__pycache__"))
    for dll in ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll"):
        shutil.copy2(base / "DLLs" / dll, target / dll)
    shutil.copytree(base / "tcl", target / "tcl",
                    ignore=shutil.ignore_patterns("tix*", "*demos*", "*.a", "*.lib"))
    print("  packages:", ", ".join(PACKAGES))
    run([sys.executable, "-m", "pip", "install", "--quiet", "--no-compile",
         "--target", str(target / "Lib" / "site-packages"),
         "--only-binary=:all:", "--platform", "win_amd64",
         "--python-version", PYTHON_VERSION[:4], "--implementation", "cp", *PACKAGES])


def go_build(package: str, out: Path, cwd: Path, gui: bool = False, tags: str = "") -> None:
    flags = "-s -w" + (" -H windowsgui" if gui else "")
    command = ["go", "build", "-trimpath", f"-ldflags={flags}", "-o", str(out)]
    if tags:
        command.append(f"-tags={tags}")
    run([*command, package], cwd, env={**os.environ, "GOOS": "windows", "GOARCH": "amd64", "CGO_ENABLED": "0"})


def icon_resources() -> None:
    """The launcher's icon and manifest, as a .syso the Go linker picks up."""

    sys.path.insert(0, str(REPO))
    from kosika.desktop_app import icon_bytes

    winres = HERE / "app" / "winres"
    winres.mkdir(exist_ok=True)
    (winres / "icon.ico").write_bytes(icon_bytes())
    (winres / "winres.json").write_text(json.dumps({
        "RT_GROUP_ICON": {"#1": {"0000": "icon.ico"}},
        "RT_MANIFEST": {"#1": {"0409": {
            "identity": {"name": "Kosika.Studio", "version": "0.1.0.0"},
            "description": "Kosika Studio",
            "minimum-os": "win10",
            "execution-level": "as invoker",
            "dpi-awareness": "per monitor v2",
            "use-common-controls-v6": True,
        }}},
        "RT_VERSION": {"#1": {"0000": {
            "fixed": {"file_version": "0.1.0.0", "product_version": "0.1.0.0"},
            "info": {"0409": {"FileDescription": "Kosika Studio", "ProductName": "Kosika Studio",
                              "CompanyName": "Kosika", "ProductVersion": "0.1"}},
        }}},
    }, indent=2), encoding="utf-8")
    run(["go", "run", "github.com/tc-hib/go-winres@v0.3.3", "make", "--arch", "amd64",
         "--in", str(winres / "winres.json"), "--out", str(HERE / "app" / "rsrc")], HERE)
    # The installer and uninstaller wear the same icon.
    shutil.copy2(HERE / "app" / "rsrc_windows_amd64.syso", HERE / "setup" / "rsrc_windows_amd64.syso")


def zip_folder(folder: Path, out: Path) -> None:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(folder).as_posix())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--keep-stage", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("The installer is built on Windows")

    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True)
    version = stamp()
    print("Kosika Studio from", version["sha"][:7], "-", version["message"])
    stage_app(version)
    stage_python()
    print("engine: the Go engine")
    go_build(".", STAGE / "engine" / "kosika-native.exe", REPO / "native")
    print("launcher and uninstaller")
    icon_resources()
    go_build("./app", STAGE / "Kosika Studio.exe", HERE, gui=True)
    go_build("./setup", STAGE / "uninstall.exe", HERE, gui=True)
    print("setup")
    payload = HERE / "setup" / "payload.zip"
    zip_folder(STAGE, payload)
    setup = BUILD / "Kosika Studio Setup.exe"
    go_build("./setup", setup, HERE, gui=True, tags="payload")
    payload.unlink()
    if not args.keep_stage:
        shutil.rmtree(STAGE, ignore_errors=True)
    print(f"\n{setup}  ({setup.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
