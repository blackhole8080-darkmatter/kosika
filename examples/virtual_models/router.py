"""Kosika virtual model: one model name in front of several free ones.

    python router.py                      # serves http://127.0.0.1:8791/v1
    python router.py --config router.json --port 8791

Any Kosika program, agent or benchmark that speaks the OpenAI protocol can
use it in place of a single provider:

    kosika run main.kos --model kosika-virtual --model-url http://127.0.0.1:8791/v1 --model-protocol openai

What it adds to a single free model, and nothing else:

- routing: each request goes to the first model on its route that is not
  cooling down after a rate limit (HTTP 429) or failing
- adaptive compute: a short request with no tools and no sign of an earlier
  failure starts on a small fast model; anything else starts on the strong
  one; a reply that fails the checks below moves up the route
- verification: a reply must say something or call a tool; tool calls
  must name a tool that was offered and carry JSON arguments; a reply asked
  for as JSON must parse. A reply that fails is never passed on.
- patience: when every model refuses at once, it waits a moment and goes
  round once more
- a log: every request, the route it took and why, one JSON line each, so a
  change to the routing can be judged on what really happened (improve.py)

Keys are read from the user's environment (GROQ_API_KEY, OPENROUTER_API_KEY)
and never written anywhere. Only models that cost nothing are configured.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = "Kosika/0.1 (virtual model)"
NAME = "kosika-virtual"


def load(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    for model in config["models"].values():
        # A model on this computer, such as one Ollama serves, needs no key.
        if model.get("key") and not os.environ.get(model["key"]):
            raise SystemExit(f"{model['key']} is not set; the router reads keys from the environment only.")
    return config


class Router:
    def __init__(self, config: dict, log: Path) -> None:
        self.config = config
        self.log = log
        self.cool: dict[str, float] = {}
        self.lock = threading.Lock()

    # ------------------------------------------------------------ deciding

    def difficulty(self, body: dict) -> tuple[str, str]:
        """Which route a request starts on, and why, from the request alone."""
        messages = body.get("messages") or []
        size = sum(len(json.dumps(m.get("content") or "")) for m in messages)
        last = json.dumps(messages[-1].get("content") or "") if messages else ""
        signs = [w for w in self.config["failure_words"] if w.lower() in last.lower()]
        rule = self.config["quick_when"]
        if body.get("tools"):
            return "strong", "tools offered"
        if signs:
            return "strong", "an earlier step failed: " + ", ".join(signs[:3])
        if size > rule["max_chars"]:
            return "strong", f"{size} characters"
        return "quick", f"{size} characters, no tools"

    def route(self, start: str) -> list[str]:
        return self.config["routes"][start]

    # ------------------------------------------------------------ checking

    @staticmethod
    def problem(body: dict, reply: dict) -> str | None:
        """What is wrong with a reply, or None when it may be passed on."""
        try:
            choice = reply["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            return "no choices in the reply"
        text = message.get("content") or ""
        calls = message.get("tool_calls") or []
        if not text.strip() and not calls:
            return "empty reply" + (" (ran out of tokens)" if choice.get("finish_reason") == "length" else "")
        offered = {t.get("function", {}).get("name") for t in body.get("tools") or []}
        for call in calls:
            name = call.get("function", {}).get("name")
            if name not in offered:
                return f"called a tool that was not offered: {name}"
            try:
                json.loads(call["function"].get("arguments") or "{}")
            except (ValueError, TypeError):
                return f"tool {name} got arguments that are not JSON"
        if (body.get("response_format") or {}).get("type") in ("json_object", "json_schema") and not calls:
            try:
                json.loads(text[text.index("{"): text.rindex("}") + 1] if "{" in text else text)
            except ValueError:
                return "asked for JSON, and the reply is not JSON"
        return None

    # ------------------------------------------------------------ asking

    def ask(self, name: str, body: dict) -> dict:
        model = self.config["models"][name]
        sent = {k: v for k, v in body.items() if k not in ("model", "stream", "stream_options")}
        sent["model"] = model["id"]
        # Settings a model needs from this router, such as a lower reasoning
        # effort, as the config gives them for that model.
        sent.update(model.get("extra", {}))
        request = urllib.request.Request(
            model["url"], data=json.dumps(sent).encode(), method="POST",
            headers={**({"Authorization": "Bearer " + os.environ[model["key"]]} if model.get("key") else {}),
                     "Content-Type": "application/json", "User-Agent": AGENT})
        with urllib.request.urlopen(request, timeout=self.config["timeout_seconds"]) as answer:
            return json.load(answer)

    def answer(self, body: dict) -> tuple[int, dict]:
        start, why = self.difficulty(body)
        tried: list = []
        began = time.time()
        reply = self.attempt(body, start, tried)
        if reply is None and self.config.get("patience_seconds"):
            # Every free model refused at once, usually a burst of rate
            # limits: wait a moment, as a person would, and go round once more.
            time.sleep(self.config["patience_seconds"])
            with self.lock:
                self.cool.clear()
            reply = self.attempt(body, start, tried)
        self.write(start, why, tried, began, reply is not None)
        if reply is None:
            return 502, {"error": {"message": "No free model gave a reply that passed the checks.", "tried": tried}}
        return 200, reply

    def attempt(self, body: dict, start: str, tried: list) -> dict | None:
        """Ask down the route; the first reply that passes the checks, or None."""
        for name in self.route(start):
            with self.lock:
                if self.cool.get(name, 0) > time.time():
                    tried.append({"model": name, "skipped": "cooling down after a rate limit"})
                    continue
            t0 = time.time()
            try:
                reply = self.ask(name, body)
            except urllib.error.HTTPError as error:
                wait = float(error.headers.get("retry-after") or self.config["cool_seconds"])
                if error.code == 429:
                    with self.lock:
                        self.cool[name] = time.time() + min(wait, 120)
                tried.append({"model": name, "status": error.code, "ms": int((time.time() - t0) * 1000)})
                continue
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                tried.append({"model": name, "error": str(error)[:200], "ms": int((time.time() - t0) * 1000)})
                continue
            wrong = self.problem(body, reply)
            tried.append({"model": name, "status": 200, "ms": int((time.time() - t0) * 1000), "problem": wrong})
            if wrong is None:
                reply["model"] = NAME
                return reply
        return None

    def write(self, start: str, why: str, tried: list, began: float, ok: bool) -> None:
        line = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "start": start, "why": why, "ok": ok,
                "ms": int((time.time() - began) * 1000), "tried": tried}
        with self.lock, self.log.open("a", encoding="utf-8") as out:
            out.write(json.dumps(line) + "\n")


def handler(router: Router):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, code: int, value: dict) -> None:
            data = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/").endswith("/models"):
                return self.send(200, {"object": "list", "data": [{"id": NAME, "object": "model"}]})
            self.send(404, {"error": {"message": "not found"}})

        def do_POST(self):
            if not self.path.rstrip("/").endswith("/chat/completions"):
                return self.send(404, {"error": {"message": "not found"}})
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            code, reply = router.answer(body)
            if not body.get("stream") or code != 200:
                return self.send(code, reply)
            # A streamed request gets the checked reply as one piece: nothing
            # is shown before it has passed the checks.
            message = reply["choices"][0]["message"]
            chunk = {"id": reply.get("id", "kv"), "object": "chat.completion.chunk", "model": NAME,
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": message.get("content") or ""},
                                  "finish_reason": reply["choices"][0].get("finish_reason")}],
                     "usage": reply.get("usage")}
            data = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(HERE / "router.json"))
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--log", default=str(Path.home() / ".kosika" / "virtual-router.jsonl"))
    args = parser.parse_args()
    log = Path(args.log)
    log.parent.mkdir(parents=True, exist_ok=True)
    router = Router(load(Path(args.config)), log)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(router))
    print(f"{NAME} on http://127.0.0.1:{args.port}/v1, log {log}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
