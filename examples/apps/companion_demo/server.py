"""Kosi in a browser: talk to examples/agents/companion from a page.

    python server.py                 # http://127.0.0.1:8794

Each message is one real run of the agent, nothing simulated:

    kosika run main.kos --workflow main --input ... --model ... --memory ...

The reply's words are shown, and the drawing too when it made one: the
`embed` field Designs returns is one self-contained fragment of HTML, so the
page drops it in as it is.

Asks that want a picture are sent to a model strong enough to write Designs'
strict JSON structure (the local 3B/4B models time out on it); everything else
runs on the local model. Set KOSIKA_LOCAL_MODEL, KOSIKA_STRONG_MODEL and
GROQ_API_KEY to change which.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
AGENT = ROOT / "examples" / "agents" / "companion" / "main.kos"
MEMORY = HERE / "memory"

LOCAL = os.environ.get("KOSIKA_LOCAL_MODEL", "qwen2.5:3b")
STRONG = os.environ.get("KOSIKA_STRONG_MODEL", "openai/gpt-oss-120b")
GROQ = "https://api.groq.com/openai/v1"

# A drawing needs Designs' structure prompt, which the small local models
# cannot finish inside any timeout worth waiting for.
PICTURE_WORDS = {
    "draw",
    "draws",
    "diagram",
    "picture",
    "sketch",
    "chart",
    "timeline",
    "illustrate",
    "illustration",
    "visualise",
    "visualize",
    "poster",
    "flowchart",
    "mindmap",
    "animation",
    "animate",
    "art",
}


def wants_drawing(text: str) -> bool:
    return bool(set(re.findall(r"[a-z]+", text.lower())) & PICTURE_WORDS)


def record(output: str) -> dict:
    """`kosika run` prints the result record, then a line naming its trace."""
    body = output.split("\nTrace:")[0].strip()
    start = body.find("{")
    if start < 0:
        last = (
            body.strip().splitlines()[-1] if body.strip() else "Kosika gave no answer."
        )
        raise RuntimeError(last[:300])
    return ast.literal_eval(body[start:])


def ask(text: str) -> dict:
    started = time.time()
    picture = wants_drawing(text) and bool(os.environ.get("GROQ_API_KEY"))
    args = [sys.executable, "-m", "kosika.cli", "run", str(AGENT), "--workflow", "main"]
    if picture:
        os.environ["KOSIKA_MODEL_API_KEY"] = os.environ["GROQ_API_KEY"]
        args += [
            "--model",
            STRONG,
            "--model-url",
            GROQ,
            "--model-protocol",
            "openai",
            "--max-output-tokens",
            "8000",
        ]
    else:
        args += ["--model", LOCAL]
    args += ["--memory", str(MEMORY), "--model-timeout", "240"]

    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as held:
        json.dump({"msg": {"text": text}}, held)
        path = held.name
    args += ["--input", path]
    try:
        done = subprocess.run(args, capture_output=True, cwd=str(ROOT), timeout=600)
        out = done.stdout.decode("utf-8", "replace")
        err = done.stderr.decode("utf-8", "replace")
        if done.returncode != 0 and not out.strip():
            raise RuntimeError((err or out).strip()[:300] or "Kosika gave no answer.")
        got = record(out)
    finally:
        os.unlink(path)

    return {
        "text": str(got.get("text", "")),
        "kind": str(got.get("kind", "")),
        "title": str(got.get("title", "")),
        "why": str(got.get("why", "")),
        "embed": str(got.get("embed", "")),
        "problems": [str(p) for p in got.get("problems", []) or []],
        "model": STRONG if picture else LOCAL,
        "seconds": round(time.time() - started, 1),
    }


def handler(page: bytes):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # quiet
            pass

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self) -> None:
            if self.path != "/ask":
                self.send_error(404)
                return
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"
            )
            asked = str(body.get("text", ""))[:2000].strip()
            if not asked:
                self.send_error(400)
                return
            try:
                answer = ask(asked)
            except Exception as error:  # shown in the page, not hidden
                answer = {
                    "text": f"I could not answer that: {str(error)[:300]}",
                    "kind": "",
                    "title": "",
                    "why": "",
                    "embed": "",
                    "problems": [],
                    "model": "",
                    "seconds": 0,
                }
            data = json.dumps(answer).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8794)
    args = parser.parse_args()
    MEMORY.mkdir(parents=True, exist_ok=True)
    page = (HERE / "index.html").read_bytes()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(page))
    print(
        f"Kosi on http://127.0.0.1:{args.port}  (local {LOCAL}, drawing {STRONG})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
