# The Kosika coder: how it is built and what it can do

Written 13 October 2026. Everything here was measured or read from the
code on that day; where something is not known, it says so.

## In one paragraph

The coder is a Kosika program of about 560 lines
(`examples/agents/coder/main.kos`). You give it one small task in a Python
project. It finds out what to change, checks its own plan before you see
it, shows you every file it will write as one list, writes only after you
say yes, runs the project's tests, and tells you what really happened. It
gets its intelligence from a model. Its reliability comes from everything
around the model, and that part is written in Kosika.

## How one task goes

1. **You ask.** "add a function subtract(a, b) to calc.py, with a test".
2. **It asks a stronger helper first, if you allowed one.** An outside
   agent such as devin or omp does the task in a *copy* of your project.
   It can only edit files there. What it changed comes back as a list.
3. **The plan is checked, in Kosika.** A function called `plan_check`
   reads every file the plan would write, beside the file as it is now.
4. **If something is wrong, it goes back.** The helper is asked once more
   and told exactly what was wrong. If that fails too, or no helper is
   allowed, the coder does the task itself with the local model. Its own
   plan is checked the same way and may go back twice.
5. **You see one list and say Allow or No.** Nothing in your real folder
   has changed up to this point.
6. **The files are written and the tests are run.** Running the tests is a
   second question, because it starts a program.
7. **It reports.** What was written is taken from Kosika's own record of
   what happened, never from what the model says it did.

Say `fix` after a failing run and it tries again, shown what the tests
printed. Say `test` to only run the tests.

## What makes it trustworthy

These are the parts that do not depend on how clever the model is.

**It cannot change anything without you.** Writing files and starting
programs are tools marked `access: execute`. The language itself refuses
to run them without a recorded approval for exactly that action.

**It cannot claim work it did not do.** Its answer has a field `did`. The
model leaves it empty and Kosika fills it in from the changes that really
ran. A model that says "I created greet.py" without writing it is reported
as "No file was written."

**Its plan is checked before you are asked.** The checks, and where each
one lives:

| What is caught | Written in |
|---|---|
| A function the file has now and the plan drops | Kosika, in the coder |
| Tests put in the source file, not a test file | Kosika, in the coder |
| A task that asks for a test and writes none | Kosika, in the coder |
| The new text is not valid Python | the engine |
| A name used but never defined or imported | the engine |
| An import of something the other file does not have | the engine |
| An empty file, a file written twice, a file that only repeats the request | the engine |

The first three only read lines of text, so they are in Kosika and you can
change them. The next three need a Python parser, so they are in the
engine.

**A wrong plan that survives is shown with its faults.** If the model
still gets it wrong after being told, the plan reaches you carrying plain
warnings, such as "calc.py: it no longer has add and double, which the
file has now".

**An outside agent never touches your folder.** It works in a copy, it is
shown only folders inside the one root you named (`Aryan_Private`), and
its work passes the same check and the same Allow or No as the coder's
own.

## What it measured

Six small tasks in a scratch Python project, each run for real with its
tests. A task counts only if the tests pass, the change is there, and
everything the project had before is still there.

| Setup | Done right | Time for all six |
|---|---|---|
| Local model (qwen2.5:7b), no checks | 2 of 6 | 34 min |
| Local model, with the checks | 5 of 6 | 53 min |
| devin helping, with the checks | 6 of 6 | 12 min |

Two things in that table are worth saying out loud.

The checks, not a better model, took the local coder from 2 to 5. Its
typical mistake was to write a file again with only the new function in
it, then rewrite the tests to match. The tests passed and the project was
damaged. My first scoring missed this and reported 3 of 6; reading the
files showed the truth.

The outside agent is the big jump in speed: five of the six tasks took 25
to 48 seconds. The sixth showed a fault in my own check, which refused a
correct rename because the function's test had been renamed with it. The
coder fell back to the local model and still finished. The check was
fixed, and that task then took 30 seconds.

## How it was built, in the order it happened

1. **A plain version.** List the folder, read files, write files, run
   pytest. Two writing tools confused the model, so it became one.
2. **Plan first.** All changes are requested together and shown as one
   list. This is a feature of the language (`plans_first`), not of the
   coder.
3. **`did`.** After a run where the model claimed files it never wrote.
4. **Skills.** Short notes beside the program (`skills/python.md`,
   `skills/tests.md`) that are picked by the words of the task and added
   to what the model is told.
5. **Real measurement.** Six tasks, real files, real tests. This is where
   the weaknesses became visible.
6. **Checks in code.** First written in the Python engine.
7. **Checks in Kosika.** You asked why Python was doing what Kosika could.
   The language gained `check: NAME` on an agent, and `Text.lines()`, and
   three checks moved into the coder's own file.
8. **Outside agents.** The language gained a tool to ask one, and a way to
   write several files with one approval, so that the whole flow could be
   written in the coder's program.

## How intelligent is it, honestly

- **With an outside agent**, it is as able as that agent on small tasks,
  with Kosika's checks and your approval on top. This is the setup to use.
- **With the local 7b model alone**, it handles one small function at a
  time and needs its checks. It is slow, 4 to 13 minutes a task on this
  laptop, because the model writes every file out whole.
- **It does not understand a project.** It reads the files it thinks it
  needs. It has no map of the code and no memory beyond its last run.

## What it cannot do yet

- **Large files or many files.** It replaces whole files, and an outside
  agent's change is refused above 40 files.
- **Anything but Python.** The tests it runs are pytest. It does not write
  Kosika yet.
- **Choose tests by meaning.** It runs the tests named after the files it
  changed, and all of them if it finds none. A change that breaks a test
  named after something else is not seen until all are run.
- **Work on your real projects, proven.** The table above is from a
  scratch project of two tiny files. One real job has been done since: on
  a copy of Kosika itself, devin's change was checked, written and tested
  in 97 seconds. One job is not yet a track record.
- **Review.** Nothing reads the change for sense, only for the faults the
  checks know. A second agent reviewing the first is planned.

## How to use it

In Kosika Studio, open the coder, grant it a folder on the Permissions
page, allow starting programs, and give it a task.

From the Kosi pill: right-click, choose a folder for agents, then type
`ask the coder to add a function is_even to calc.py, with a test`.

Outside agents are on for you: devin and omp, inside `Aryan_Private` only.
`kosika outside` shows the setting and `kosika outside --off` turns it
off.
