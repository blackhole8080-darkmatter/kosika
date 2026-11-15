"""A small trial of Kosika's training on any laptop, with no graphics card.

    python cpu_trial.py --train 150 --eval 20

It trains the smallest open coder (Qwen2.5-Coder 0.5B) with LoRA on the
shortest Kosika examples, on the processor, then asks the base model and the
trained one the same held-out Kosika changes and judges both by the coder's
own rules (score.py). It is a trial, not kosika-coder: too small and too
short to be good, but enough to show the whole path works on this laptop
and whether training on Kosika moves the score at all.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import torch
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

import score

DATA = score.DATA
BASE = "Qwen/Qwen2.5-Coder-0.5B-Instruct"
LONGEST = 1024
ANSWER = 384


def shortest(path: Path, count: int, answer_chars: int) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if len(r["messages"][2]["content"]) <= answer_chars and score.SHOWN.search(r["messages"][1]["content"])]
    rows.sort(key=lambda r: len(r["messages"][1]["content"]) + len(r["messages"][2]["content"]))
    return rows[:count]


def spread(path: Path, count: int, answer_chars: int) -> list[dict]:
    """`count` examples drawn evenly from all that fit, not only the
    shortest: the shortest are mostly one-line edits, and a model taught
    only those learns little of what a real change looks like."""
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows = [r for r in rows if len(r["messages"][2]["content"]) <= answer_chars and score.SHOWN.search(r["messages"][1]["content"])]
    random.Random(7).shuffle(rows)
    return rows[:count]


def encode(tokenizer, row: dict) -> dict | None:
    """Tokens of one example, learning only from the answer."""
    prompt = tokenizer.apply_chat_template(row["messages"][:2], tokenize=False, add_generation_prompt=True)
    whole = prompt + row["messages"][2]["content"] + tokenizer.eos_token
    asked = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    ids = tokenizer(whole, add_special_tokens=False)["input_ids"]
    if len(ids) > LONGEST:
        return None
    labels = [-100] * len(asked) + ids[len(asked):]
    return {"input_ids": torch.tensor([ids]), "labels": torch.tensor([labels])}


def answers(model, tokenizer, rows: list[dict]) -> tuple[int, int, float]:
    applies = exact = 0
    started = time.monotonic()
    model.eval()
    for row in rows:
        prompt = tokenizer.apply_chat_template(row["messages"][:2], tokenize=False, add_generation_prompt=True)
        ids = tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
        with torch.no_grad():
            out = model.generate(**ids, max_new_tokens=ANSWER, do_sample=False)
        text = tokenizer.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=True)
        a, e = score.judge(row, text)
        applies += a
        exact += e
    return applies, exact, (time.monotonic() - started) / max(1, len(rows))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=int, default=150)
    parser.add_argument("--eval", type=int, default=20)
    parser.add_argument("--epochs", type=int, default=2)
    parser.add_argument("--name", default="kosika-coder", help="the data files: NAME.train.jsonl and NAME.eval.jsonl")
    parser.add_argument("--recipe", choices=["first", "better"], default="first",
                        help="first: the first trial's settings; better: what the diagnosis found (see TRAINING.md)")
    parser.add_argument("--lr", type=float, default=0.0, help="learning rate (default: 3e-4 first, 2e-4 better)")
    args = parser.parse_args()
    better = args.recipe == "better"
    if better:
        # Room for a real change, not only one-line ones.
        global LONGEST
        LONGEST = 1536
    torch.manual_seed(7)
    tokenizer = AutoTokenizer.from_pretrained(BASE)
    model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.float32)
    held = shortest(DATA / f"{args.name}.eval.jsonl", args.eval, 1200)
    pick = spread if better else shortest
    taught = [e for e in (encode(tokenizer, r) for r in pick(DATA / f"{args.name}.train.jsonl", args.train * (2 if better else 1), 2500 if better else 1500)) if e][: args.train]
    print(f"{len(taught)} examples to learn from, {len(held)} held-out changes to judge", flush=True)

    before = answers(model, tokenizer, held)
    print(f"base model:    applies {before[0]}/{len(held)}, exact {before[1]}/{len(held)}, {before[2]:.1f} s each", flush=True)

    # The better recipe also teaches the feed-forward layers, where a model
    # keeps what it knows, not only attention, which mostly learns where to
    # look; and it averages each step over ACCUMULATE examples, with a
    # warm-up and a learning rate that falls to nothing, instead of one
    # noisy step per example at a fixed rate.
    modules = ["q_proj", "k_proj", "v_proj", "o_proj"] + (["gate_proj", "up_proj", "down_proj"] if better else [])
    model = get_peft_model(model, LoraConfig(r=32 if better else 16, lora_alpha=64 if better else 32, lora_dropout=0.05 if better else 0.0,
                                             task_type="CAUSAL_LM", target_modules=modules))
    model.train()
    rate = args.lr or (2e-4 if better else 3e-4)
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=rate)
    accumulate = 8 if better else 1
    steps = max(1, math.ceil(len(taught) / accumulate) * args.epochs)
    warm = max(1, steps // 20)

    def rate_at(step: int) -> float:
        if not better:
            return rate
        if step < warm:
            return rate * (step + 1) / warm
        return rate * 0.5 * (1 + math.cos(math.pi * (step - warm) / max(1, steps - warm)))

    started = time.monotonic()
    done = 0
    for epoch in range(args.epochs):
        total = 0.0
        order = list(taught)
        if better:
            random.Random(epoch).shuffle(order)
        for at, example in enumerate(order, 1):
            loss = model(**example).loss
            (loss / accumulate).backward()
            total += loss.item()
            if at % accumulate == 0 or at == len(order):
                for group in optimizer.param_groups:
                    group["lr"] = rate_at(done)
                torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
                optimizer.step()
                optimizer.zero_grad()
                done += 1
        print(f"epoch {epoch + 1}: mean loss {total / len(taught):.3f}, {time.monotonic() - started:.0f} s so far", flush=True)

    after = answers(model, tokenizer, held)
    print(f"trained model: applies {after[0]}/{len(held)}, exact {after[1]}/{len(held)}, {after[2]:.1f} s each", flush=True)
    result = {"trial": BASE, "recipe": args.recipe, "data": args.name, "learned_from": len(taught), "judged": len(held),
              "base": {"applies": before[0], "exact": before[1]}, "trained": {"applies": after[0], "exact": after[1]},
              "train_seconds": round(time.monotonic() - started), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with (DATA / "scores.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(result) + "\n")
    model.save_pretrained(DATA / "trial-adapter")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
