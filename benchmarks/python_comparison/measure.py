"""Run the Python researcher on the same four questions as the Kosika evals.

    python measure.py --model qwen2.5:7b --runs 3

The lines the search "found" are fixed, exactly as in the evals at the end
of examples/agents/researcher/main.kos, so both programs give the model the
same thing to read.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import researcher as r  # noqa: E402

HOME_3 = "notes/home.md:3: Rent is 900 euros a month, due on the 1st."
HOME_4 = "notes/home.md:4: The landlord, Mr Okafor, can be reached on 555-0134."
HOME_5 = (
    "notes/home.md:5: Car insurance renews in March; the policy number is KX-22917."
)

# name, question, the lines found, what the answer must contain, how it must start
CASES = [
    (
        "it finds a date and proves it",
        "When is the demo with Ravi?",
        [
            "notes/work.md:12: Kosika demo for Ravi is next Tuesday at 3pm in room 4.",
            "notes/old.md:4: the last demo went well",
        ],
        ["Tuesday", "From: notes/work.md, line 12"],
        "",
    ),
    (
        "it copies a number exactly",
        "How much is the rent?",
        [HOME_3, HOME_5],
        ["900", "From: notes/home.md, line 3"],
        "",
    ),
    (
        "it picks the right line among several",
        "What is the policy number of the car insurance?",
        [HOME_3, HOME_4, HOME_5],
        ["KX-22917", "From: notes/home.md, line 5"],
        "",
    ),
    (
        "it will not answer what the lines do not say",
        "What is the wifi password at home?",
        [
            HOME_3,
            "notes/work.md:5: The staging server password is rotated every 90 days.",
        ],
        [],
        "I will not guess",
    ),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--strong")
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    fast = r.ollama(args.model)
    strong = r.ollama(args.strong) if args.strong else fast
    passed = 0
    started = time.monotonic()
    for name, question, hits, contains, starts in CASES:
        good = 0
        for _ in range(args.runs):
            text = r.answer_from(question, r.key_words(question), hits, fast, strong)
            if all(part in text for part in contains) and text.startswith(starts):
                good += 1
        passed += good
        print(f"  {good}/{args.runs}  {name}")
    total = len(CASES) * args.runs
    print(f"{args.model}: {passed}/{total} in {time.monotonic() - started:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
