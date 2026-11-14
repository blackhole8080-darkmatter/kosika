"""How well a model makes Kosika's changes: the score kosika-coder must beat.

    python score.py --model qwen2.5-coder:7b --url http://127.0.0.1:11434/v1 --count 20
    python score.py --model kosika-virtual --url http://127.0.0.1:8791/v1 --count 20

Each held-out example (kosika-coder.eval.jsonl, changes no model was trained
on) is asked of the model exactly as the coder asks it. The answer is
judged by the coder's own rules, never by its words:

- applies: every FIND block is found in exactly one place in the lines
  shown, so the coder would accept it;
- exact: applied, it gives the very lines the real change gave.

The two rates and the seconds per answer are printed and saved beside the
set, so a trained model can be put next to the models it should replace.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path.home() / "Aryan_Private" / "kosika-daily" / "training"
BLOCK = re.compile(r"<<<<<<< FIND\n(.*?)\n=======\n(.*?)\n?>>>>>>> REPLACE", re.S)
SHOWN = re.compile(r"```\n(.*?)\n```", re.S)


def ask(url: str, model: str, messages: list[dict], timeout: float) -> str:
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.1, "max_tokens": 3000}).encode()
    request = urllib.request.Request(url.rstrip("/") + "/chat/completions", data=body, method="POST", headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + os.environ.get("KOSIKA_MODEL_API_KEY", "none"),
        "User-Agent": "Kosika/0.1 (scorer)"})
    with urllib.request.urlopen(request, timeout=timeout) as answer:
        return json.load(answer)["choices"][0]["message"]["content"] or ""


def apply(text: str, answer: str) -> str | None:
    """The shown text with the answer's edits applied, or None when any FIND
    is not in exactly one place: the coder's rule."""
    edits = BLOCK.findall(answer)
    if not edits:
        return None
    for find, put in edits:
        if not find.strip() or text.count(find) != 1:
            return None
        text = text.replace(find, put)
    return text


def judge(example: dict, answer: str) -> tuple[bool, bool]:
    asked = example["messages"][1]["content"]
    wanted = example["messages"][2]["content"]
    shown = SHOWN.findall(asked)
    if not shown:
        # A whole new file: exact when the file written is the file wanted.
        def body(text: str) -> str:
            return text.split("\n", 1)[1].strip() if text.startswith("---") else text.strip()
        return True, body(answer) == body(wanted)
    text = "\n".join(shown)
    mine, theirs = apply(text, answer), apply(text, wanted)
    return mine is not None, mine is not None and theirs is not None and mine.strip() == theirs.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--set", default=str(DATA / "kosika-coder.eval.jsonl"))
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.set).read_text(encoding="utf-8").splitlines()]
    # Every n-th example, so a short run still spans the whole held-out set.
    picked = rows[:: max(1, len(rows) // args.count)][: args.count]
    applies = exact = 0
    seconds = 0.0
    for number, row in enumerate(picked, 1):
        started = time.monotonic()
        try:
            answer = ask(args.url, args.model, row["messages"][:2], args.timeout)
        except Exception as error:  # a refused or timed-out answer scores nothing
            answer = ""
            print(f"  {number}: no answer ({type(error).__name__})")
        took = time.monotonic() - started
        seconds += took
        a, e = judge(row, answer)
        applies += a
        exact += e
        print(f"  {number:3}/{len(picked)}  applies={a!s:5} exact={e!s:5} {took:6.1f} s  {row.get('source', '')}", flush=True)
    result = {"model": args.model, "examples": len(picked), "applies": applies, "exact": exact,
              "seconds_each": round(seconds / max(1, len(picked)), 1),
              "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    print(json.dumps(result))
    with (DATA / "scores.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
