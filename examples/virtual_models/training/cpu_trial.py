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
    args = parser.parse_args()
    torch.manual_seed(7)
    tokenizer = AutoTokenizer.from_pretrained(BASE)
    model = AutoModelForCausalLM.from_pretrained(BASE, torch_dtype=torch.float32)
    held = shortest(DATA / "kosika-coder.eval.jsonl", args.eval, 1200)
    taught = [e for e in (encode(tokenizer, r) for r in shortest(DATA / "kosika-coder.train.jsonl", args.train, 1500)) if e]
    print(f"{len(taught)} examples to learn from, {len(held)} held-out changes to judge", flush=True)

    before = answers(model, tokenizer, held)
    print(f"base model:    applies {before[0]}/{len(held)}, exact {before[1]}/{len(held)}, {before[2]:.1f} s each", flush=True)

    model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, lora_dropout=0.0, task_type="CAUSAL_LM",
                                             target_modules=["q_proj", "k_proj", "v_proj", "o_proj"]))
    model.train()
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=3e-4)
    started = time.monotonic()
    for epoch in range(args.epochs):
        total = 0.0
        for example in taught:
            loss = model(**example).loss
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
            total += loss.item()
        print(f"epoch {epoch + 1}: mean loss {total / len(taught):.3f}, {time.monotonic() - started:.0f} s so far", flush=True)

    after = answers(model, tokenizer, held)
    print(f"trained model: applies {after[0]}/{len(held)}, exact {after[1]}/{len(held)}, {after[2]:.1f} s each", flush=True)
    result = {"trial": BASE, "learned_from": len(taught), "judged": len(held),
              "base": {"applies": before[0], "exact": before[1]}, "trained": {"applies": after[0], "exact": after[1]},
              "train_seconds": round(time.monotonic() - started), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with (DATA / "scores.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(result) + "\n")
    model.save_pretrained(DATA / "trial-adapter")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
