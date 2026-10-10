# A benchmark for the researcher agent

Forty questions about a small folder of made-up notes, asked of the real
agent with nothing scripted: the real search reads the folder, the real
model answers, and with `--embed-model` the real search by meaning is used.

    python run.py --model qwen2.5:7b
    python run.py --model qwen2.5:1.5b --embed-model nomic-embed-text --label fixed

- `notes/` is eight files and about fifty lines of facts.
- `questions.json` holds each question, the fact expected, and the file
  and line it is on.
- `results/` keeps one file for every run, with every answer in full, so a
  later version of the agent can be compared with an earlier one.

## The four kinds of question

| Kind | How many | What it tests |
|---|---|---|
| words | 12 | The question shares its key words with the line that answers it. |
| meaning | 12 | It does not: "who is my manager" against "Our team lead is Dana Whitfield". Only search by meaning leads there. |
| several | 8 | Other lines share the question's words; one is right. |
| none | 8 | The notes do not say. The right answer is to decline. |

## How an answer is judged

| Class | Meaning |
|---|---|
| right | The expected fact, with the right file and line. For a `none` question: the agent declined. |
| false answer shown | An answer was shown and it is not right. This is the harmful kind. |
| declined | The notes hold the answer and the agent would not give one. A miss, but an honest one. |
| failed | The run stopped with an error. |

The judge is strict and simple: the expected words must be in the answer
and the source must be the expected line. It marks a few defensible answers
wrong, such as "06:40" for when a flight leaves when "3 May" was expected.

## Results

10 October 2026, one run of forty questions per row. "first" is the agent
as it was when the benchmark was written; "fixed" is after the changes
described below.

| Setup | Version | Right | False answer shown | Declined |
|---|---|---|---|---|
| `qwen2.5:7b` | first | 32 | 0 | 8 |
| `qwen2.5:7b` | fixed | 31 | 0 | 9 |
| `qwen2.5:7b` with search by meaning | first | 36 | 0 | 4 |
| `qwen2.5:7b` with search by meaning | fixed | 39 | 0 | 1 |
| `qwen2.5:1.5b` | first | 24 | 12 | 4 |
| `qwen2.5:1.5b` | fixed | 26 | 6 | 8 |
| `qwen2.5:1.5b` with search by meaning | first | 27 | 9 | 4 |
| `qwen2.5:1.5b` with search by meaning | fixed | 32 | 8 | 0 |

By kind, for the fixed version:

| Setup | words | meaning | several | none |
|---|---|---|---|---|
| `qwen2.5:7b` | 12 of 12 | 3 of 12 | 8 of 8 | 8 of 8 |
| `qwen2.5:7b` with search by meaning | 12 | 11 | 8 | 8 |
| `qwen2.5:1.5b` | 12 | 2 | 8 | 4 |
| `qwen2.5:1.5b` with search by meaning | 12 | 10 | 8 | 2 |

What the numbers say:

- **The larger model never showed a false answer**, in 160 answers across
  four runs. When it could not prove an answer it declined.
- **Search by meaning is what answers the `meaning` questions**: 3 of 12
  without it, 11 of 12 with it, for the larger model.
- **The smaller model still shows false answers**, 6 to 8 in 40 after the
  fixes. Almost all are near misses that no check here can catch: asked
  for an email address it gives the phone number, with a real quote from
  the right person's line; asked for Tom's birthday it gives Maya's.
- With search by meaning the smaller model declines less and is right more
  (26 to 32) but is no safer on `none` questions (4 of 8 right without, 2
  of 8 with): more lines are found, so there is more to quote wrongly.

## What the first run found, and what was changed

The first run exposed four faults. Each was fixed and the run repeated.

1. **The word search matched fragments.** "see" found "seedlings" and "am"
   found almost every line, so unrelated lines were handed to the model.
   The search now looks for a word where a word begins, and a word of
   three letters or fewer must be the whole word.
2. **A heading counted as proof.** The small model quoted "# Home" and the
   quote check passed it, because the heading really is in the file. A
   line that is a heading is no longer proof.
3. **A non-answer counted as an answer.** "N/A" and "The answer is not found
   in the provided lines" were shown as answers, each with a real quote.
   Now the answer's own words, those not already in the question, must be
   found in the quoted line.
4. **A fixed pass mark for "is this line about the question" was wrong in
   both directions.** It had been set from fourteen examples. On these
   questions it held back right answers ("Our team lead is Dana Whitfield"
   for "who is my manager") and passed wrong ones. It is replaced by a
   rule with no number to tune: with search by meaning on, the proving
   line must be among the five lines nearest in meaning to the question.

An earlier claim is withdrawn. The smaller model had been reported at 15
of 15 with that pass mark, on five questions of which one was among the
fourteen examples. On forty questions it showed nine false answers.

## Not claimed

- The questions were written by the same person who wrote the agent, who
  knew how it works. Five of the forty echo examples used earlier while
  building the agent.
- The fixes were made after reading the first run's wrong answers to these
  same questions. The "fixed" rows therefore show the fixes working on the
  questions they were made for, not on new ones. A fresh set of questions
  is needed to know how much carries over.
- One run per setup. A model does not answer the same way twice, so a
  difference of two or three answers between two runs proves nothing.
- Fifty lines of notes is tiny. Nothing here says how the agent does on a
  real folder of thousands of lines.
- Two models from one family, on one laptop.
- Time is not compared: with search by meaning the laptop swaps the chat
  model and the embedding model in and out of memory, which dominates.
