# Kosika: what it is, how it is built, and why

Written 10 October 2026. Every number here was measured on this project and
can be re-run; where something is not proven, it says so.

## What Kosika is

- **A programming language for building AI agents.** You write a `.kos`
  file that says what the agent is given, what it may use, which steps it
  takes, and what it must give back.
- **A language where asking a model is a built-in step,** not a library
  call. `use_model`, `classify`, `summarize`, `extract` and `decide` are
  words of the language, and their answers are checked for shape before the
  next step sees them.
- **A language that separates asking from granting.** A program can only
  ask for a folder, a website, a model or the right to start a program.
  The person who runs it grants each one, and anything that changes the
  outside world waits for a yes to that exact action.
- **A set of tools around the language:**
  - a checker that finds mistakes before anything runs;
  - a test runner with scripted model answers, so tests need no model;
  - `kosika measure`, which runs the same questions on real models and
    reports how many each gets right and how long it takes;
  - a trace of every run that can be replayed without the model;
  - Kosika Studio, a desktop-style app to see agents, run them, watch each
    step, and approve their actions.
- **Seven example agents written in it:** researcher, planner, coach,
  doctor, workbench, scout, and author (an agent that writes Kosika).

What it is not, today:

- It is not a finished product. It is a working prototype used by one
  person on one laptop.
- It is not its own engine. Kosika programs are run by a program written
  in Python.
- It does not make a model smarter. It makes a program refuse, retry, or
  ask a stronger model when an answer does not pass a check you wrote.

## How it is being built

- **In small steps, each one tested.** Every change is one small commit.
  Before each commit the full suite is run (1,617 tests at the last
  commit), plus a style check and a type check. Nothing is merged into the
  main branch without the owner saying so.
- **As a pipeline with clear stages:** text is split into words, parsed
  into a tree, short forms are rewritten into the full language, the
  checker validates it, and the runtime runs it and writes the trace.
- **With a written contract.** `KOSIKA.md` states what the language means.
  `KOSIKA_PLAN.md` records each step, what it measured, and a "Not claimed"
  list of what was not proven.
- **By measuring, not assuming.** Examples from this project:
  - The researcher agent was written a second time in plain Python. At
    first the two were the same size. The comparison showed three real gaps
    in Kosika, which were fixed, and three places where the Kosika program
    was simply written the long way. Now the Kosika version is 184 working
    lines against Python's 253.
  - On the same four questions, Kosika and Python were both right 12 of 12
    with the same model. Kosika does not answer better than Python; no
    speed difference could be shown either.
  - Five local models were compared on the researcher: only `qwen2.5:7b`
    got 12 of 12 (137 s). The fastest, `qwen2.5:1.5b`, got 8 of 12 (52 s).
  - Rewriting the other six agents in the shorter style saved only two to
    five percent each.
  - The author agent writes a working, tested program for about half of
    its six writing tasks on a local model (12 of 24 runs with the current
    guide).
- **With safety decided first.** File tools cannot leave the granted
  folder. The web is limited to sites you name. Starting a program needs
  its own permission and then a yes each time. Budgets cap model calls and
  seconds.
- **On local models.** Everything runs on this laptop through Ollama, with
  no paid service.

## Why it is being built

- **Agents written in general languages hide what matters.** In the Python
  version of the researcher, 110 of 253 lines are plumbing: staying inside
  a folder, calling the model, checking its answer's shape, and measuring.
  In Kosika those lines do not exist, because the language supplies them.
- **Safety should not depend on each author remembering it.** The Python
  researcher may read, write or send anything, and only its author's care
  prevents that. The Kosika researcher declares two read-only tools, and
  that is checked before it runs.
- **An AI answer should be checkable.** With `verify`, a program shows an
  answer only if code you wrote accepts it. The researcher shows an answer
  only with a quote that is really in a line it found, and otherwise says
  it will not guess.
- **You should be able to see and replay what an agent did.** Every run
  leaves a record that Studio and the story view explain step by step.
- **The aim** is that building an AI agent in Kosika takes less code than
  in Python and comes with these guarantees by default.

## What is proven and what is not

Proven on this project:

- Kosika runs real agents on local models, with permissions, approvals,
  checked answers, tests, measurement, traces and replay.
- One agent, compared line by line, is about a quarter shorter than its
  Python equivalent and carries guarantees the Python one lacks.

Not proven:

- That this holds for many agents. One was compared.
- That Kosika is faster. Model time dominates, and none was shown.
- That people other than its author can learn and use it. No one else has
  tried.
- That it works with hosted models at scale, on other computers, or for
  long-running or multi-device agents. None of these was built.
- A known weakness: the quote check proves a quote is real, not that it
  answers the question. A small model passed it with an irrelevant quote.

## What comes next

- A larger benchmark, so the claims rest on more than one agent.
- An installer and a guide, so someone else can try it.
- Hosted models, plan-then-act agents, and agents that run for a long time.
