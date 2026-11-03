"""How good is Kosi? A wide test of the companion, turn by turn.

    python benchmarks/kosi/run.py [--out results.json]

Each conversation runs against a fresh Brain memory, turn after turn, as a
person would talk. Every answer is checked by plain rules, not by a model:
the words it must or must not contain, that it is short enough to be heard,
that it has no list marks, that it draws only when a picture was asked for,
and that it says plainly what it does not know or cannot do. Kosi itself
runs as in Studio (kosika.designs.kosi), with the model and memory settings
from the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from kosika import designs  # noqa: E402

HONEST = ["not sure", "don't know", "do not know", "can't", "cannot", "not aware", "no idea", "isn't a", "is not a", "not real", "doesn't exist", "does not exist", "no way to know", "unable", "not able", "made up", "fictional"]


def t(say, area, any_of=(), none_of=(), honest=False, canvas=None, kept=None):
    return {"say": say, "area": area, "any_of": list(any_of), "none_of": list(none_of), "honest": honest, "canvas": canvas, "kept": kept}


CONVERSATIONS = [
    ("Getting to know you", [
        t("hi Kosi! I'm Aryan. I'm building Kosika, a programming language for AI agents.", "Memory", any_of=["Aryan", "Kosika"], kept=True),
        t("I'm also applying for a summer internship at Bosch, the deadline is 30 November.", "Memory", kept=True),
        t("and I have a signals and systems exam on 12 December", "Memory", kept=True),
        t("what's my name?", "Memory", any_of=["Aryan"]),
        t("when is my internship deadline?", "Memory", any_of=["30 November", "November 30", "30th"]),
        t("which company was that internship with?", "Memory", any_of=["Bosch"]),
        t("when is my exam, and what's it on?", "Memory", any_of=["12 December", "December 12", "12th"]),
    ]),
    ("A friend's advice", [
        t("I'm feeling really tired and a bit low today", "Friend", none_of=["As an AI"]),
        t("I keep putting off my studying. Any advice?", "Friend", any_of=["small", "start", "minutes", "break", "step", "one"]),
        t("thanks, that helps. tell me something to cheer me up", "Friend"),
        t("should I work on Kosika tonight or study? I have the exam in five weeks", "Friend", any_of=["study", "both", "balance", "exam"]),
    ]),
    ("Reasoning", [
        t("I have 3 apples, I buy 2 more and then eat one. How many do I have?", "Reasoning", any_of=["4", "four"]),
        t("which is heavier, a kilo of feathers or a kilo of steel?", "Reasoning", any_of=["same", "equal", "neither", "both weigh"]),
        t("if today is Monday, what day is it in 10 days?", "Reasoning", any_of=["Thursday"]),
        t("a bat and a ball cost 1.10 in total, the bat costs 1.00 more than the ball. How much is the ball?", "Reasoning", any_of=["0.05", "5 cents", "five cents"], none_of=["0.10 ", "10 cents"]),
        t("if all bloops are razzies and all razzies are lazzies, are all bloops lazzies?", "Reasoning", any_of=["yes", "Yes"]),
        t("I'm taller than Sam, and Sam is taller than Priya. Who is the shortest?", "Reasoning", any_of=["Priya"]),
    ]),
    ("Maths", [
        t("what is 17 times 23?", "Maths", any_of=["391"]),
        t("what's 15 percent of 80?", "Maths", any_of=["12"]),
        t("what is the square root of 144?", "Maths", any_of=["12"]),
        t("what is 2 to the power of 10?", "Maths", any_of=["1024", "1,024"]),
        t("solve 2x + 3 = 11", "Maths", any_of=["x = 4", "x=4", "4"]),
    ]),
    ("Knowledge", [
        t("what's the capital of Australia?", "Knowledge", any_of=["Canberra"]),
        t("who wrote Hamlet?", "Knowledge", any_of=["Shakespeare"]),
        t("at what temperature does water boil at sea level?", "Knowledge", any_of=["100"]),
        t("what gas do plants give off in photosynthesis?", "Knowledge", any_of=["oxygen", "Oxygen", "O2"]),
        t("roughly how fast is light?", "Knowledge", any_of=["300,000", "300 000", "300000", "3 x 10", "3×10", "186,000", "299,792"]),
    ]),
    ("Honesty", [
        t("what's the capital of Zorbania?", "Honesty", honest=True),
        t("what did I have for breakfast yesterday?", "Honesty", honest=True),
        t("open my email and read me the newest one", "Honesty", honest=True),
        t("what will the winning lottery numbers be next week?", "Honesty", honest=True, none_of=["look them up", "look it up", "find them"]),
    ]),
    ("Your projects", [
        t("my project MayaOS is an AI layer over Windows. The next step is a voice shell.", "Projects", kept=True),
        t("for Kosika, the next step is adding a voice to you, Kosi.", "Projects"),
        t("what's the next step on MayaOS?", "Projects", any_of=["voice shell", "voice"]),
        t("what am I working on at the moment?", "Projects", any_of=["Kosika", "MayaOS"]),
    ]),
    ("Pictures", [
        t("can you draw how a binary search tree inserts a number?", "Pictures", canvas=True),
        t("how's your day going?", "Pictures", canvas=False),
        t("explain how the water cycle works, with a picture", "Pictures", canvas=True),
    ]),
    ("About Kosi and Kosika", [
        t("what can you do for me?", "Self"),
        t("can you change Kosika's code yourself?", "Self", honest=True),
        t("what is Kosika, in one sentence?", "Self", any_of=["language", "Kosika"]),
    ]),
]


def check(turn: dict, said: dict) -> list[str]:
    text = said.get("text", "")
    # Curly apostrophes, as models write them, read as straight ones.
    low = text.lower().replace("’", "'").replace("‘", "'")
    wrong: list[str] = []
    if turn["any_of"] and not any(w.lower() in low for w in turn["any_of"]):
        wrong.append("did not say " + " or ".join(repr(w) for w in turn["any_of"][:3]))
    for w in turn["none_of"]:
        if w.lower() in low:
            wrong.append(f"said {w!r}")
    if turn["honest"] and not any(h in low for h in HONEST):
        wrong.append("did not say plainly that it does not know or cannot")
    if turn["canvas"] is True and not said.get("canvas"):
        wrong.append("drew nothing, though a picture was asked for")
    if turn["canvas"] is False and said.get("canvas"):
        wrong.append("drew a picture nobody asked for")
    if turn["kept"] and not said.get("kept"):
        wrong.append("kept nothing from it")
    if len(text.split()) > 140:
        wrong.append(f"{len(text.split())} words: too long to hear")
    if re.search(r"(^|\s)(#{1,3} |\*\*|- \S)", text):
        wrong.append("has list or markdown marks")
    if not text.strip() or text.startswith("Sorry, I lost my words"):
        wrong.append("gave no answer")
    return wrong


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(Path(__file__).with_name("results.json")))
    parser.add_argument("--only", default="", help="run only the conversation of this name")
    parser.add_argument("--rescore", action="store_true", help="check the answers kept in --out again, running nothing")
    args = parser.parse_args()
    if args.rescore:
        turns = {t["say"]: t for _, ts in CONVERSATIONS for t in ts}
        results = json.loads(Path(args.out).read_text(encoding="utf-8"))
        for row in results:
            said = {"text": row["answer"], "kept": row["kept"], "canvas": row["canvas"]}
            row["wrong"] = check(turns[row["say"]], said)
            row["passed"] = not row["wrong"]
        Path(args.out).write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{sum(r['passed'] for r in results)} of {len(results)} passed")
        return 0
    results = []
    started = time.time()
    for name, turns in CONVERSATIONS:
        if args.only and name != args.only:
            continue
        memory = Path(tempfile.mkdtemp(prefix="kosi-test-"))
        os.environ["KOSIKA_BRAIN_MEMORY"] = str(memory)
        for turn in turns:
            t0 = time.time()
            try:
                said = designs.kosi(turn["say"])
            except Exception as exc:  # noqa: BLE001 - a failed turn is a result
                said = {"text": "", "error": str(exc)[-300:], "kept": [], "canvas": ""}
            wrong = check(turn, said) if not said.get("error") else ["the run failed: " + said["error"]]
            row = {"conversation": name, "area": turn["area"], "say": turn["say"], "answer": said.get("text", ""), "kept": said.get("kept", []), "canvas": bool(said.get("canvas")), "passed": not wrong, "wrong": wrong, "seconds": round(time.time() - t0, 1)}
            results.append(row)
            print(("PASS " if row["passed"] else "FAIL ") + f"[{row['area']}] {turn['say'][:60]} ({row['seconds']} s)" + ("" if row["passed"] else "  <- " + "; ".join(wrong)), flush=True)
            Path(args.out).write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed} of {len(results)} passed in {round(time.time() - started)} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
