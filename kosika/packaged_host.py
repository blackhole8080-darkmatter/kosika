"""Private desktop child process; exits when its parent closes stdin."""

from __future__ import annotations

import argparse
import json
import sys
import threading

from kosika.workbench import WorkbenchServer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8793)
    args = parser.parse_args()
    with WorkbenchServer(args.port) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            print(json.dumps({"ready": True, "port": server.server_port}), flush=True)
            # The desktop shell owns this pipe. EOF also handles a shell crash.
            sys.stdin.buffer.read()
        finally:
            server.shutdown()
            thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
