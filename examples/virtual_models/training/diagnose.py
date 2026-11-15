"""Why a small Kosika model gets a change wrong: each held-out change, the
model's answer next to the real one, sorted into what went wrong.

    python diagnose.py --adapter PATH [--set trial15.jsonl] [--count 15]

Kinds of failure, in the order they are checked:

- no_edit:     the answer has no FIND/REPLACE block at all;
- not_found:   a FIND is not in the lines shown (made up, or misquoted);
- ambiguous:   a FIND is in the lines shown more than once;
- wrong_place: it applies, but changes other lines than the real change;
- wrong_words: it applies at the right lines, but writes something else;
- exact:       it gives the very lines the real change gave.

The answers and the verdicts are written to diagnose.jsonl beside the set.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

import score

BASE = "Qwen/Qwen2.5-Coder-0.5B-Instruct"


def touched(before: str, after: str) -> set[int]:
    """The numbers of the lines of `before` a change replaces."""
    import difflib

    lines = set()
    for tag, a1, a2, _b1, _b2 in difflib.SequenceMatcher(None, before.split("\n"), after.split("\n")).get_opcodes():
        if tag != "equal":
            lines.update(range(a1, max(a2, a1 + 1)))
    return lines


def verdict(example: dict, answer: str) -> str:
    asked = example["messages"][1]["content"]
    wanted = example["messages"][2]["content"]
    shown = score.SHOWN.findall(asked)
    text = shown[-1] if shown else ""
    edits = score.BLOCK.findall(answer)
    if not edits:
        return "no_edit"
    for find, _put in edits:
        if text.count(find) == 0:
            return "not_found"
        if text.count(find) > 1:
            return "ambiguous"
    got = score.apply(text, answer)
    real = score.apply(text, wanted)
    if got is None:
        return "not_found"
    if real is not None and got == real:
        return "exact"
    if real is not None and touched(text, got) and not (touched(text, got) & touched(text, real)):
        return "wrong_place"
    return "wrong_words"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--set", default=str(score.DATA / "trial15.jsonl"))
    parser.add_argument("--count", type=int, default=15)
    parser.add_argument("--most", type=int, default=1024, help="longest answer, in tokens")
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.set).read_text(encoding="utf-8").splitlines() if line][: args.count]
    tokenizer = AutoTokenizer.from_pretrained(BASE)
    model = PeftModel.from_pretrained(AutoModelForCausalLM.from_pretrained(BASE, dtype=torch.float32), args.adapter)
    model.eval()
    kinds: Counter[str] = Counter()
    out = Path(args.set).with_name("diagnose.jsonl")
    with out.open("w", encoding="utf-8") as sink:
        for at, row in enumerate(rows, 1):
            prompt = tokenizer.apply_chat_template(row["messages"][:2], tokenize=False, add_generation_prompt=True)
            ids = tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
            started = time.monotonic()
            with torch.no_grad():
                made = model.generate(**ids, max_new_tokens=args.most, do_sample=False)
            used = made.shape[1] - ids["input_ids"].shape[1]
            answer = tokenizer.decode(made[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
            kind = verdict(row, answer)
            kinds[kind] += 1
            sink.write(json.dumps({"at": at, "kind": kind, "tokens": int(used), "cut": used >= args.most,
                                   "prompt_tokens": int(ids["input_ids"].shape[1]),
                                   "task": row["messages"][1]["content"][:300],
                                   "answer": answer, "wanted": row["messages"][2]["content"]}, ensure_ascii=False) + "\n")
            sink.flush()
            print(f"{at:2}/{len(rows)} {kind:12} {used:4} tokens {time.monotonic() - started:5.1f} s", flush=True)
    print(json.dumps(dict(kinds)), "->", out)


if __name__ == "__main__":
    main()
