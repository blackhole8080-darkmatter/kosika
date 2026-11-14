"""Train kosika-coder: a small open coding model taught Kosika, for free.

Run it on a free GPU notebook (Kaggle gives about 30 GPU hours a week; a
T4 is enough), not on a laptop processor:

1. New notebook, Accelerator: GPU T4, Internet: on.
2. Upload kosika-coder.train.jsonl and kosika-coder.eval.jsonl as a dataset.
3. Paste this file into a cell and run it:

       !pip install -q unsloth
       %run train_kosika_coder.py --data /kaggle/input/<your-dataset>

4. Download kosika-coder.gguf and Modelfile from the output, put them in
   one folder on the laptop, and there run:

       ollama create kosika-coder -f Modelfile

Then kosika-coder is a model like any other on the laptop. Add it to
router.json as a "local" entry and the learning router measures it against
the free cloud models and asks it first for the jobs it wins; score.py
gives its numbers on changes it never saw.

What it does: QLoRA fine-tuning of Qwen2.5-Coder (1.5B by default, which
answers in seconds on a laptop processor; --base for 7B), on the coder's
own edit format, for --epochs passes over Kosika's history, then merges
the adapter and exports a 4-bit GGUF file for Ollama. Only the assistant's
answers are learned from; the questions are context.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="folder holding kosika-coder.train.jsonl and .eval.jsonl")
    parser.add_argument("--base", default="unsloth/Qwen2.5-Coder-1.5B-Instruct")
    parser.add_argument("--epochs", type=float, default=2)
    parser.add_argument("--length", type=int, default=4096, help="longest example, in tokens")
    parser.add_argument("--out", default="kosika-coder")
    args = parser.parse_args()

    from datasets import load_dataset
    from transformers import TrainingArguments
    from trl import SFTTrainer
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import train_on_responses_only

    model, tokenizer = FastLanguageModel.from_pretrained(args.base, max_seq_length=args.length, load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(
        model, r=16, lora_alpha=32, lora_dropout=0,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth",
    )

    data = Path(args.data)
    sets = load_dataset("json", data_files={"train": str(data / "kosika-coder.train.jsonl"),
                                            "eval": str(data / "kosika-coder.eval.jsonl")})

    def as_text(rows):
        return {"text": [tokenizer.apply_chat_template(m, tokenize=False) for m in rows["messages"]]}

    sets = sets.map(as_text, batched=True, remove_columns=sets["train"].column_names)
    trainer = SFTTrainer(
        model=model, tokenizer=tokenizer,
        train_dataset=sets["train"], eval_dataset=sets["eval"],
        dataset_text_field="text", max_seq_length=args.length, packing=False,
        args=TrainingArguments(
            output_dir="checkpoints", per_device_train_batch_size=2, gradient_accumulation_steps=8,
            num_train_epochs=args.epochs, learning_rate=2e-4, warmup_ratio=0.03, lr_scheduler_type="cosine",
            logging_steps=10, eval_strategy="steps", eval_steps=100, save_strategy="no",
            fp16=True, optim="adamw_8bit", seed=7, report_to="none",
        ),
    )
    # Learn the answers only: the question is what the coder shows, not what
    # the model should say.
    trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\n",
                                      response_part="<|im_start|>assistant\n")
    trainer.train()
    print(json.dumps({"eval": trainer.evaluate()}))

    model.save_pretrained_gguf(args.out, tokenizer, quantization_method="q4_k_m")
    # Newer Unsloth writes the file to "<out>_gguf/" under the base model's name.
    made = next(f for d in (Path(args.out), Path(args.out + "_gguf")) for f in d.glob("*.gguf"))
    made.rename(Path(args.out) / "kosika-coder.gguf")
    (Path(args.out) / "Modelfile").write_text(
        "FROM ./kosika-coder.gguf\n"
        "PARAMETER temperature 0.1\n"
        "PARAMETER num_ctx 8192\n"
        'SYSTEM """You are the Kosika coder. You change Kosika\'s files with edits: each edit is '
        "<<<<<<< FIND, whole lines copied exactly from the file, =======, the lines to put in their "
        'place, >>>>>>> REPLACE. Answer with the edit blocks only."""\n',
        encoding="utf-8",
    )
    print("Made", Path(args.out) / "kosika-coder.gguf", "and", Path(args.out) / "Modelfile")


if __name__ == "__main__":
    main()
