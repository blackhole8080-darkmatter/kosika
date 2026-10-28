"""Kosika AI Chat: a chat on the left, a live canvas on the right.

    python server.py                         # http://127.0.0.1:8793
    python server.py --kosika path/to/kosika-native.exe --model-url http://127.0.0.1:8791/v1 --model kosika-virtual

This file only connects things; the thinking is in Kosika:

- chat.kos `answer` writes the reply in words
- designs/main.kos `describe` describes the structure to draw, checked by
  Kosika and sent back once to the model if anything is wrong
- designs/draw.kos `draw` lays it out and draws it, building itself step by
  step on the canvas, each sentence marking its part
- designs/eyes.py finds one freely licensed real photo on Wikimedia Commons
  when the structure asks for one, shown with its source
- Kosi (agents/assistant) reads every message first and decides whether it
  answers itself or hands it to a helper: Gatherer looks things up, Steward
  reads the services' status, Lookout watches a page, Desk reads a folder.
  What a helper did is drawn on the canvas. Anything that would change
  something (a new watch) is only shown as a plan, and runs when you press
  Approve.

The reply and the canvas are made at the same time. Models are the free
ones the user's keys reach (Groq by default, or the Kosika virtual model);
keys stay in the environment and are never written anywhere.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESIGNS = HERE.parent.parent / "agents" / "designs"
FONTS = HERE.parent.parent.parent / "kosika" / "app_assets" / "fonts"
sys.path.insert(0, str(DESIGNS))
import eyes  # noqa: E402  the photo finder lives beside the Designs programs


def record(output: str) -> dict:
    """kosika run prints the result record, then a line naming its trace."""
    text = output.split("\nTrace:")[0].strip()
    if not text.startswith("{"):
        raise RuntimeError(text[-400:] or "Kosika gave no answer.")
    return ast.literal_eval(text)


class Kosika:
    def __init__(self, binary: str, model: str, url: str) -> None:
        self.binary = binary
        self.model = model
        self.url = url
        self.native = "native" in Path(binary).name

    def run(self, source: Path, workflow: str, value: dict, *, model: bool, extra: list | None = None) -> dict:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(value, f)
        args = [self.binary, "run", str(source), "--workflow", workflow, "--input", f.name,
                "--trace", os.path.join(tempfile.gettempdir(), "kosika-chat-trace.jsonl")]
        if model:
            args += ["--model", self.model, "--model-url", self.url, "--model-protocol", "openai",
                     "--model-timeout", "400", "--max-output-tokens", "8000"]
        if not self.native and not model:
            args += ["--max-function-calls", "1000000", "--max-workflow-steps", "100000"]
        args += extra or []
        done = subprocess.run(args, capture_output=True, timeout=600, cwd=source.parent)
        os.unlink(f.name)
        return record(done.stdout.decode("utf-8", errors="replace"))


def photo_for(words: str) -> dict:
    empty = {k: "" for k in ("title", "author", "license", "license_url", "page_url", "image")}
    if not words.strip():
        return empty
    try:
        found = eyes.thumbs([words])
    except OSError:
        return empty
    if not found:
        return empty
    return {k: found[0][k] for k in empty}


AGENTS = HERE.parent.parent / "agents"
# What each helper may read: the sites it was granted in Studio.
TOUCHES = {"gatherer": ["news.google.com", "en.wikipedia.org"], "steward": ["your services' status"], "lookout": ["github.com"], "desk": []}


class Chat:
    def __init__(self, kosika: Kosika, kosi: Kosika, services: str, lookout_memory: str) -> None:
        self.kosika = kosika
        # Kosi and its helpers run on the kosika command: Kosi's program uses
        # parts of the language the Go engine does not read yet.
        self.kosi = kosi
        self.services = services
        self.lookout_memory = lookout_memory
        self.pool = ThreadPoolExecutor(4)
        self.lock = threading.Lock()

    def route(self, ask: str) -> dict:
        try:
            return self.kosi.run(AGENTS / "assistant" / "main.kos", "route", {"msg": {"text": ask}}, model=False)
        except Exception:
            return {"agent": "", "text": ""}

    def drawn_job(self, ask: str, agent: str, text: str, result: str, waiting: bool) -> dict:
        job = {"ask": ask, "agent": agent, "text": text, "touches": TOUCHES.get(agent, []), "result": result, "waiting": waiting}
        request = self.kosika.run(DESIGNS / "main.kos", "automation", {"j": job}, model=False)
        drawn = self.kosika.run(DESIGNS / "draw.kos", "draw", {"r": request}, model=False)
        return {"page": drawn["page"], "problems": drawn["problems"], "title": request["structure"]["title"]}

    def helper(self, agent: str, text: str) -> str:
        """Run a helper that only reads, and return what it said."""
        msg = {"msg": {"text": text}}
        if agent == "gatherer":
            sites = sum((["--allow-site", site] for site in TOUCHES["gatherer"]), [])
            return self.kosi.run(AGENTS / "gatherer" / "main.kos", "main", msg, model=True, extra=sites)["text"]
        if agent == "steward":
            return self.kosi.run(AGENTS / "steward" / "main.kos", "status", msg, model=False, extra=["--services", self.services])["text"]
        if agent == "desk":
            return "Desk reads only a folder you choose, and none has been chosen yet. Tell me which folder in Studio, and I can look there."
        return ""

    def kosi_job(self, ask: str, agent: str, text: str) -> dict:
        if agent == "lookout":
            plan = f"I can ask Lookout to {text[:1].lower() + text[1:]}. It will read that page every 15 minutes and tell your phone when there is news. Press Approve if you want that."
            out = {"text": plan, "approve": {"agent": agent, "text": text}, "card": True, "who": "Kosi \u00b7 a plan"}
            out.update(self.drawn_job(ask, agent, text, "", True))
            return out
        said = self.helper(agent, text)
        out = {"text": said, "card": True, "who": f"Kosi \u00b7 asked {agent.title()}"}
        out.update(self.drawn_job(ask, agent, text, said, False))
        return out

    def approve(self, agent: str, text: str) -> dict:
        """The person said yes in the chat: run the change they approved."""
        if agent != "lookout":
            return {"text": "There is nothing waiting for a yes."}
        site = TOUCHES["lookout"][0]
        said = self.kosi.run(AGENTS / "lookout" / "main.kos", "main", {"msg": {"text": text}}, model=False,
                             extra=["--memory", self.lookout_memory, "--allow-site", site])
        return {"text": said["text"]}

    def words(self, history: str, ask: str) -> dict:
        return self.kosika.run(HERE / "chat.kos", "answer", {"turn": {"history": history, "ask": ask}}, model=True)

    def canvas(self, ask: str) -> dict:
        described = self.kosika.run(DESIGNS / "main.kos", "describe", {"ask": {"text": ask}}, model=True)
        s = described["structure"]
        # The golden spiral's real example is a sunflower head, whose seed
        # counts really are Fibonacci numbers (a nautilus shell is not one).
        words = "sunflower head close-up" if s.get("figure") == "golden_spiral" else s.get("photo", "")
        described["picture"] = photo_for(words)
        # The parts Kosika judged a real photo would help with, found on Commons.
        wanted = self.kosika.run(DESIGNS / "draw.kos", "photos_wanted", {"r": described}, model=False)["looks"]
        found = list(self.pool.map(lambda look: {"part": look["part"], "picture": photo_for(look["photo"])}, wanted))
        described["photos"] = [f for f in found if f["picture"]["image"]]
        drawn = self.kosika.run(DESIGNS / "draw.kos", "draw", {"r": described}, model=False)
        return {"page": drawn["page"], "problems": drawn["problems"], "title": s.get("title", "")}

    def ask(self, history: str, ask: str) -> dict:
        hand = self.route(ask)
        if hand.get("agent"):
            try:
                return self.kosi_job(ask, hand["agent"], hand["text"])
            except Exception as error:
                return {"text": f"Kosi handed this to {hand['agent'].title()}, and it could not finish: {str(error)[:200]}", "page": "", "problems": [], "title": ""}
        # Mathematics to work comes back as a paper, every line checked by
        # Kosika (designs/paper.kos).
        try:
            if self.kosika.run(DESIGNS / "paper.kos", "is_maths", {"ask": {"text": ask}}, model=False)["yes"]:
                paper = self.kosika.run(DESIGNS / "paper.kos", "derive", {"ask": {"text": ask}}, model=True)
                return {"page": paper["page"], "problems": [], "title": "Checked working" if paper["checked"] else "Working, corrected by Kosika", "text": "", "card": False}
        except Exception:
            pass  # a drawing is still tried
        # The drawing is the answer, its explanation written inside it. Words
        # alone are asked for only when no drawing could be made.
        try:
            out = self.canvas(ask)
        except Exception as error:
            out = {"page": "", "problems": [str(error)[:300]], "title": ""}
        if out["page"]:
            return {**out, "text": "", "card": False}
        try:
            text = self.words(history, ask)["text"]
        except Exception as error:
            text = f"I could not answer that just now: {str(error)[:200]}"
        return {**out, "text": text, "card": True, "who": "Kosika"}


def handler(chat: Chat):
    page = (HERE / "index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith("/fonts/"):
                # Studio's own fonts (both under the SIL Open Font Licence), read in place.
                name = Path(self.path).name
                font = FONTS / name
                if name.endswith(".ttf") and font.is_file():
                    data = font.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "font/ttf")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def do_POST(self):
            if self.path not in ("/ask", "/approve"):
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/approve":
                answer = chat.approve(str(body.get("agent", "")), str(body.get("text", ""))[:500])
            else:
                ask = str(body.get("ask", ""))[:2000]
                history = str(body.get("history", ""))[-6000:]
                answer = chat.ask(history, ask)
            data = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8793)
    parser.add_argument("--kosika", default=os.environ.get("KOSIKA_BIN", "kosika"))
    parser.add_argument("--model", default="openai/gpt-oss-120b")
    parser.add_argument("--model-url", default="https://api.groq.com/openai/v1")
    parser.add_argument("--kosi", default="kosika", help="The kosika command Kosi and its helpers run on.")
    bench = HERE.parents[4] / "kosika-bench"
    parser.add_argument("--services", default=str(bench / "aios" / "aios.services"))
    parser.add_argument("--lookout-memory", default=str(bench / "lookout" / "memory"))
    args = parser.parse_args()
    if not os.environ.get("KOSIKA_MODEL_API_KEY"):
        os.environ["KOSIKA_MODEL_API_KEY"] = os.environ.get("GROQ_API_KEY", "local")
    chat = Chat(Kosika(args.kosika, args.model, args.model_url), Kosika(args.kosi, args.model, args.model_url),
                args.services, args.lookout_memory)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(chat))
    print(f"Kosika AI Chat on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
