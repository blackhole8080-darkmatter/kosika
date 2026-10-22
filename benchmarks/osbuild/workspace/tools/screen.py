"""Boot an OS image in QEMU with no window, and print what is on its screen.

    python3 tools/screen.py IMAGE [--keys "e n d ret"] [--wait 3] [--snapshots 1]

The image is booted as a raw floppy disk (like `-fda`), as the os-tutorial does. After
`--wait` seconds (4 by default) the 80x25 text screen is read straight from video memory
(0xb8000) through QEMU's monitor, and printed one line a row, trailing
spaces removed. With `--keys`, those keys are typed (QEMU `sendkey` names,
space separated) after the first read, and the screen is read again. With
`--snapshots 2`, the screen is read twice, a second apart, so a test can
see something change, such as a timer.

Each read is printed between lines `=== screen N ===`. QEMU is always
stopped: after the last read, or after 30 seconds whatever happens. It
needs no network and writes only `.screen-*.bin` beside the image.
"""

import argparse
import os
import subprocess
import sys
import time

ROWS, COLUMNS = 25, 80


def text_of(raw: bytes) -> str:
    rows = []
    for row in range(ROWS):
        cells = raw[row * COLUMNS * 2:(row + 1) * COLUMNS * 2]
        chars = "".join(
            chr(cells[i]) if 32 <= cells[i] < 127 else " " for i in range(0, len(cells), 2)
        )
        rows.append(chars.rstrip())
    while rows and not rows[-1]:
        rows.pop()
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--keys", default="")
    parser.add_argument("--wait", type=float, default=4.0)
    parser.add_argument("--snapshots", type=int, default=1)
    args = parser.parse_args()
    if not os.path.isfile(args.image):
        print(f"No image at {args.image}: the build did not make it.")
        return 2
    if os.path.getsize(args.image) < 512:
        print(f"{args.image} is {os.path.getsize(args.image)} bytes; a boot sector is 512.")
        return 2
    folder = os.path.dirname(os.path.abspath(args.image))
    # QEMU unpacked into the workspace (toolchain/root), when the system has
    # none: its own libraries and BIOS files, found beside it.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local = os.path.join(here, "toolchain", "root", "usr")
    program, extra, env = "qemu-system-i386", [], dict(os.environ)
    if os.path.isfile(os.path.join(local, "bin", "qemu-system-i386")):
        program = os.path.join(local, "bin", "qemu-system-i386")
        extra = ["-L", os.path.join(local, "share", "qemu")]
        env["LD_LIBRARY_PATH"] = os.path.join(local, "lib", "x86_64-linux-gnu")
    # QEMU now and then stops at start-up for reasons of its own, not the
    # image's; a boot that gives no screen at all is tried up to 3 times.
    problem = ""
    for _ in range(3):
        reads, problem = boot(program, extra, env, folder, args)
        if reads:
            break
    if not reads:
        print("QEMU stopped before the screen could be read: " + problem)
        return 1
    for n, text in enumerate(reads, 1):
        print(f"=== screen {n} ===")
        print(text)
    return 0


def boot(program, extra, env, folder, args):
    """One boot: the screens read, and what went wrong if none was."""

    qemu = subprocess.Popen(
        [program, *extra, "-drive", f"format=raw,if=floppy,file={args.image}", "-boot", "a", "-display", "none", "-monitor", "stdio",
         "-no-reboot", "-net", "none", "-m", "32"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, env=env,
    )
    started = time.monotonic()

    def say(command: str) -> None:
        qemu.stdin.write(command + "\n")
        qemu.stdin.flush()

    reads = []
    try:
        time.sleep(args.wait)
        count = max(1, args.snapshots)
        for n in range(count):
            dump = os.path.join(folder, f".screen-{n}.bin")
            if os.path.exists(dump):
                os.remove(dump)
            say(f'pmemsave 0xb8000 {ROWS * COLUMNS * 2} "{dump}"')
            for _ in range(50):
                if os.path.exists(dump) and os.path.getsize(dump) == ROWS * COLUMNS * 2:
                    break
                time.sleep(0.1)
            if qemu.poll() is not None or not os.path.exists(dump) or os.path.getsize(dump) != ROWS * COLUMNS * 2:
                break
            with open(dump, "rb") as handle:
                reads.append(text_of(handle.read()))
            if n + 1 < count:
                time.sleep(1.0)
        if args.keys and qemu.poll() is None:
            for key in args.keys.split():
                say(f"sendkey {key}")
                time.sleep(0.15)
            time.sleep(1.5)
            dump = os.path.join(folder, ".screen-keys.bin")
            say(f'pmemsave 0xb8000 {ROWS * COLUMNS * 2} "{dump}"')
            for _ in range(50):
                if os.path.exists(dump) and os.path.getsize(dump) == ROWS * COLUMNS * 2:
                    break
                time.sleep(0.1)
            if os.path.exists(dump) and os.path.getsize(dump) == ROWS * COLUMNS * 2:
                with open(dump, "rb") as handle:
                    reads.append(text_of(handle.read()))
    finally:
        if qemu.poll() is None:
            try:
                say("quit")
                qemu.wait(timeout=5)
            except Exception:
                qemu.kill()
        if time.monotonic() - started > 30:
            qemu.kill()
    problem = ""
    if not reads:
        try:
            said = (qemu.stderr.read() or "").strip()[-300:]
        except Exception:
            said = ""
        problem = f"exit code {qemu.returncode}" + (f": {said}" if said else "")
    return reads, problem


if __name__ == "__main__":
    sys.exit(main())
