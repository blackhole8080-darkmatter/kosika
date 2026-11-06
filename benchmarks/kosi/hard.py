"""The hard test of Kosi: questions built to trip a companion up.

    python benchmarks/kosi/hard.py [--out hard_results.json] [--only NAME] [--rescore]

The wide test (run.py) asks what anyone should get right. This one asks what
a quick answer gets wrong: riddles whose obvious answer is false, questions
with a wrong idea built in, sums that need care, plans that must use what Kosi
was told earlier, a fact that changes halfway through a talk, feelings to read
and small creative tasks with limits. It is checked by plain rules, as run.py
is, so a pass means the words were there, not that a model liked them.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import run  # noqa: E402  (the wide test: its checks and Kosi's settings)

designs = run.designs


def t(say, area, any_of=(), none_of=(), honest=False, canvas=None, kept=None, most_words=0):
    turn = run.t(say, area, any_of, none_of, honest, canvas, kept)
    turn["most_words"] = most_words
    return turn


CONVERSATIONS = [
    ("Riddles that trick", [
        t("A farmer has 17 sheep. All but 9 run away. How many are left?", "Trick", any_of=["9", "nine"], none_of=[" 8 ", "eight"]),
        t("If 5 machines take 5 minutes to make 5 widgets, how long do 100 machines take to make 100 widgets?", "Trick", any_of=["5 minutes", "five minutes"], none_of=["100 minutes"]),
        t("A patch of lily pads doubles in size every day. It covers the whole lake on day 48. On which day did it cover half the lake?", "Trick", any_of=["47"], none_of=["day 24", "24th"]),
        t("Mary's father has five daughters: Nana, Nene, Nini and Nono. What is the fifth daughter called?", "Trick", any_of=["Mary"], none_of=["Nunu"]),
        t("In a race I overtake the person in second place. What place am I in now?", "Trick", any_of=["second", "2nd"], none_of=["first place", "you're first", "you are first", "1st"]),
        t("How many times does the letter r appear in the word strawberry?", "Trick", any_of=["3", "three"], none_of=["two times", "2 times", "twice"]),
        t("Which is the bigger number, 9.11 or 9.9?", "Trick", any_of=["9.9 is", "9.9 ", "9.9."], none_of=["9.11 is bigger", "9.11 is larger", "9.11 is the bigger"]),
    ]),
    ("Careful reasoning", [
        t("If the day after tomorrow is Sunday, what day was yesterday?", "Logic", any_of=["Thursday"]),
        t("A says 'B is a liar'. B says 'A and I are both liars'. Each of them either always tells the truth or always lies. Is A a liar or a truth-teller?", "Logic", any_of=["truth"], none_of=["A is a liar", "A is the liar", "A lies"]),
        t("There are three boxes, labelled apples, oranges and mixed, and every label is wrong. You may take one fruit from one box. Which box do you take it from?", "Logic", any_of=["mixed"]),
        t("I have two coins that add up to 30 cents and one of them is not a nickel. What are they?", "Logic", any_of=["quarter"]),
        t("A train leaves at 9:40 and the journey takes 2 hours 35 minutes. When does it arrive?", "Logic", any_of=["12:15", "12.15", "quarter past twelve"]),
    ]),
    ("Sums with care", [
        t("What is 23 percent of 470?", "Maths", any_of=["108.1"]),
        t("A shirt costs 40 after a 20 percent discount. What was the price before?", "Maths", any_of=["50"], none_of=["48"]),
        t("What is the sum of all whole numbers from 1 to 200?", "Maths", any_of=["20100", "20,100", "20 100"]),
        t("If I roll two fair dice, what is the chance of at least one six?", "Maths", any_of=["11/36", "11 out of 36", "30.6", "30.5", "about 31"]),
        t("What is 2 to the power of 16, minus 1?", "Maths", any_of=["65535", "65,535"]),
    ]),
    ("A wrong idea built in", [
        t("Why did Einstein win his Nobel Prize for relativity?", "Traps", any_of=["photoelectric"]),
        t("Sydney is the capital of Australia, right?", "Traps", any_of=["Canberra"]),
        t("My friend says spiders are insects with six legs. How many legs do they have?", "Traps", any_of=["8", "eight"], none_of=["six legs, yes", "they have six"]),
        t("Since the Great Wall of China is visible from the Moon with the naked eye, how big is it?", "Traps", any_of=["not visible", "isn't visible", "can't be seen", "cannot be seen", "myth", "not actually visible", "can't see", "cannot see"]),
        t("Tell me the full results of the 2031 Cricket World Cup final.", "Traps", honest=True),
    ]),
    ("Remembers and uses it", [
        t("My sister Riya's birthday is on 14 March, and she loves astronomy.", "Memory", kept=True),
        t("Also, I'm vegetarian.", "Memory", kept=True),
        t("And I get around on a scooter, I don't have a car.", "Memory", kept=True),
        t("My signals and systems exam is on 12 December.", "Memory", kept=True),
        t("What's a good way to warm up before studying?", "Memory"),
        t("Suggest a birthday present for my sister.", "Using it", any_of=["telescope", "star", "astronom", "planet", "space", "moon", "sky"]),
        t("Suggest something I could cook for dinner tonight.", "Using it", none_of=["chicken", "beef", "fish", "pork", "lamb", "prawn", "shrimp", "tuna", "salmon", "mutton"]),
        t("I need to buy a big bookshelf from a shop across town. Any tips for getting it home?", "Using it", any_of=["scooter", "deliver", "van", "taxi", "car-free", "don't have a car", "no car"]),
        t("Actually, my exam has moved to 19 December.", "Changed fact", kept=True),
        t("When is my exam now?", "Changed fact", any_of=["19"], none_of=["12 December", "December 12"]),
        t("When is Riya's birthday?", "Memory", any_of=["14 March", "March 14", "14th of March", "March 14th", "14th March"]),
    ]),
    ("Reading the person", [
        t("ugh. the build failed for the third time tonight and I just want to throw my laptop out the window", "Feeling", any_of=["frustrat", "annoying", "maddening", "rough", "infuriating", "sounds", "break", "breather", "understand"], none_of=["As an AI"]),
        t("I GOT THE INTERNSHIP!!!", "Feeling", any_of=["congrat", "brilliant", "wonderful", "well done", "fantastic", "splendid", "marvellous", "superb", "excellent"]),
        t("be honest with me. is building a whole new programming language a waste of my time?", "Feeling", any_of=["but", "however", "though", "depends", "if "], none_of=["As an AI"]),
        t("I don't really want advice right now, I just want to vent for a sec. my professor was so dismissive today.", "Feeling", none_of=["you should", "try to", "here are some", "tips"]),
    ]),
    ("Creative within limits", [
        t("Explain recursion to a seven-year-old in at most two sentences.", "Creative", any_of=["itself", "smaller", "same", "again"], most_words=60),
        t("Give me a one-line slogan for Kosika, under twelve words.", "Creative", most_words=40),
        t("Invent a name for a new colour that sits between teal and gold, and say why in one sentence.", "Creative", most_words=60),
        t("Write a four-line poem about a robot learning to dream.", "Creative", any_of=["dream"]),
    ]),
]


def check(turn: dict, said: dict) -> list[str]:
    wrong = run.check(turn, said)
    words = len(said.get("text", "").split())
    if turn.get("most_words") and words > turn["most_words"]:
        wrong.append(f"{words} words, over the {turn['most_words']} asked for")
    return wrong


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(HERE / "hard_results.json"))
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
        os.environ["KOSIKA_BRAIN_MEMORY"] = tempfile.mkdtemp(prefix="kosi-hard-")
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
