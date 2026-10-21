"""MayaOS's reach into Linux: the Linux the person installed under WSL.

For now MayaOS only looks, and only at a distribution the person named
(for example Ubuntu): the kernel, how long it has been up, the load,
memory, disk, how many packages wait for an update, and the busiest
processes. The commands are fixed here, never chosen by a program or a
page, so reading them changes nothing.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Any, Callable

TIMEOUT_SECONDS = 60

#: What `status` asks, in one go, as plain lines `key=value`.
STATUS_SCRIPT = r"""
echo "kernel=$(uname -sr)"
echo "uptime=$(uptime -p 2>/dev/null || uptime)"
echo "load=$(cut -d' ' -f1-3 /proc/loadavg)"
free -m | awk '/^Mem:/ {print "memory=" $3 " of " $2 " MB used"}'
df -h / | awk 'NR==2 {print "disk=" $3 " of " $2 " used (" $5 ") on /"}'
if command -v apt >/dev/null 2>&1; then
  echo "upgrades=$(apt list --upgradable 2>/dev/null | grep -c upgradable)"
else
  echo "upgrades=unknown"
fi
ps -eo comm,%cpu --sort=-%cpu | awk 'NR>1 && NR<=6 {printf "top=%s %s%%\n", $1, $2}'
"""

#: (command line, timeout) -> (exit code, stdout, stderr). Tests hand in their own.
Runner = Callable[[list[str], float], tuple[int, str, str]]


class WslError(RuntimeError):
    """Linux cannot be reached, or the distribution is not there."""


def _run(command: list[str], timeout: float) -> tuple[int, str, str]:
    try:
        done = subprocess.run(command, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise WslError("WSL is not installed on this computer") from exc
    except subprocess.TimeoutExpired as exc:
        raise WslError(f"Linux did not answer within {timeout:g} seconds") from exc
    return done.returncode, _text(done.stdout), _text(done.stderr)


def _text(raw: bytes) -> str:
    # wsl.exe itself speaks UTF-16; what Linux prints is UTF-8.
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(raw) > 1 and raw[1:2] == b"\x00"):
        return raw.decode("utf-16-le", errors="replace").replace("\x00", "")
    return raw.decode("utf-8", errors="replace")


def distributions(runner: Runner = _run) -> list[str]:
    """The Linux distributions installed under WSL, by name."""

    if shutil.which("wsl.exe") is None and runner is _run:
        return []
    code, out, _ = runner(["wsl.exe", "-l", "-q"], TIMEOUT_SECONDS)
    return [line.strip() for line in out.splitlines() if line.strip()] if code == 0 else []


def status(distro: str, runner: Runner = _run) -> dict[str, Any]:
    """How the distribution is doing, read without changing anything."""

    code, out, err = runner(["wsl.exe", "-d", distro, "--exec", "sh", "-c", STATUS_SCRIPT], TIMEOUT_SECONDS)
    if code != 0:
        raise WslError(f"Linux ({distro}) did not answer: {(err or out).strip()[:300]}")
    found: dict[str, Any] = {"distro": distro, "top": []}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        if key == "top":
            found["top"].append(value.strip())
        elif key in ("kernel", "uptime", "load", "memory", "disk", "upgrades"):
            found[key] = value.strip()
    for key in ("kernel", "uptime", "load", "memory", "disk", "upgrades"):
        found.setdefault(key, "unknown")
    return found
