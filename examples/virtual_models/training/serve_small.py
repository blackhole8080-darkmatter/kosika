"""Kosika's own trained model, served on this computer as `kosika-small`.

    python serve_small.py            # http://127.0.0.1:8792/v1, OpenAI-style

It loads the open 0.5B coder with the adapter Kosika trained on its own
history (cpu_trial.py, or the Kosika train run on GitHub) and answers chat
requests on the processor. router.json lists it as "small", so the learning
router measures it beside the free cloud models and asks it first for the
kinds of job it wins. It answers in about a minute on a laptop processor, so
the router gives it short jobs only.
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

import score

BASE = "Qwen/Qwen2.5-Coder-0.5B-Instruct"
NAME = "kosika-small"


def load(adapter: str):
    tokenizer = AutoTokenizer.from_pretrained(BASE)
    model = AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.float32)
    model = PeftModel.from_pretrained(model, adapter)
    model.eval()
    return tokenizer, model


def handler(tokenizer, model, lock: Lock):
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
            messages = [{"role": m.get("role", "user"), "content": str(m.get("content") or "")} for m in body.get("messages") or []]
            prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            ids = tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
            most = min(int(body.get("max_tokens") or 512), 1024)
            started = time.monotonic()
            # One answer at a time: the processor is the whole machine.
            with lock, torch.no_grad():
                out = model.generate(**ids, max_new_tokens=most, do_sample=False)
            text = tokenizer.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
            used = out.shape[1] - ids["input_ids"].shape[1]
            self.send(200, {
                "id": f"ks-{int(started)}", "object": "chat.completion", "model": NAME,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                             "finish_reason": "length" if used >= most else "stop"}],
                "usage": {"prompt_tokens": ids["input_ids"].shape[1], "completion_tokens": int(used)},
            })

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", default=str(score.DATA / "trial-adapter"))
    parser.add_argument("--port", type=int, default=8792)
    args = parser.parse_args()
    tokenizer, model = load(args.adapter)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(tokenizer, model, Lock()))
    print(f"{NAME} on http://127.0.0.1:{args.port}/v1, adapter {args.adapter}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
