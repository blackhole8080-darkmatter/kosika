# Getting started with Kosika

This takes about fifteen minutes. You do not need to know how to program
to follow it, and you do not need the internet once Kosika is installed.

Kosika is a language for building AI agents. An agent is a small program
that asks an AI model for help with part of a job and does the rest with
exact code. Kosika Studio is the app where you look at agents, run them
and change them.

## What you need

- **Python 3.10 or newer.** To check, open a terminal and type
  `python --version`.
- **A model, if you want agents to run.** Without one you can still open
  Studio, read every agent, and run their tests. The simplest way to get a
  model is [Ollama](https://ollama.com): install it, then type
  `ollama pull qwen2.5:7b`. It needs about 5 GB of disk and 8 GB of memory.

## 1. Install

If you were given one file ending in `.whl`:

```
python -m pip install kosika_language_v0_1-0.1.0-py3-none-any.whl
```

If you have the Kosika folder instead, open a terminal in it and type:

```
python -m pip install .
```

Either way you now have a command called `kosika`.

## 2. Start

```
kosika start
```

That one command:

- makes a folder called `KosikaAgents` in your home folder, with three
  starter agents in it, if you do not have one yet;
- looks for a model already on your computer and uses it;
- opens Kosika Studio.

It tells you what it found. It grants nothing: no agent can read your
files, start programs or reach the web until you say so (step 7).

## 3. Look at an agent

In Studio, click **Agents**, then **support**. It is a support desk that
sorts a customer's message and remembers the customer.

Click the **Map** tab. Without reading any code you can see what it is for,
what you give it and get back, what it only looks at, what it may change,
which model it asks, and what it does, in order. Every line says where it
comes from, so nothing on the map is a guess.

## 4. Check it without a model

Click the **Tests** tab and run the tests. They pass in a moment, because a
test supplies the model's answers itself. This is how you know the agent's
own logic is right before any model is involved.

## 5. Run it

Click **Live**. The box is already filled with an example message. Press
**Run**.

Watch two things:

- **The diagram.** The agent is in the middle. A light travels to the
  model, to its memory, and to its plain code each time it uses one, and a
  number counts the calls.
- **The steps underneath.** Each lights up as it starts and turns green
  when it is done.

On a laptop a model takes from a few seconds to a minute to answer. When
it finishes, "What it did" tells the run in plain words, and "Requests"
shows exactly what the model was asked and what it said.

Run it a second time and it greets the same customer with "Welcome back",
because it remembered.

## 6. Change something

Click the **Code** tab, then **Edit**. Near the top is a comment that says
how to read the file. Find this line:

```
let greeting = if known.found { "Welcome back, " } else { "Hello, " }
```

Change `"Hello, "` to `"Good day, "`.

- As you type, a bar above the text offers words. Tab takes the first.
- If you make a mistake, a red line under the text says what is wrong and
  on which line. Click it to go there.

Press **Save**, then run the tests again. One fails: it expected "Hello".
That is the test doing its job. Change the word in the test too, or change
your greeting back.

## Kosika Studio as an app on your desktop

Run this once:

```
kosika desktop
```

It puts **Kosika Studio** on your Desktop and in the Start menu, with its
own icon. Open it like any other app: it opens in a window of its own, with
no black console window, and it stops when you close that window. To take
the shortcuts away again, run `kosika desktop --remove`.

The app starts with nothing granted, exactly like `kosika start`. Name a
folder after the command to choose where your agents live:
`kosika desktop MY_AGENTS`.

## 7. Let an agent see a folder

Agents can touch nothing until you grant it. In Studio, open
**Permissions** and press **Choose a folder**. Your computer's own folder
window opens; pick one folder, such as your notes or one project, then
press **Apply**. A whole drive or your whole user folder is refused.

Two switches sit beside it:

- **Work in a session.** Agents then work in a copy of the folder. Nothing
  reaches the real folder until you open the agent's Session tab and tick
  the files to bring back. Turn this on for any agent that changes files.
- **Let agents start programs.** Needed by agents that run things, such as
  the coder running your tests.

Reading needs only the folder. Anything that would change a file still
shows you its whole plan first and waits for your yes.

## 8. Make your own

On the **Agents** page, the last card is **New agent**. Give it a name,
choose a starting point, and press Create. You get a working agent with
its own tests, to change as you like.

## Useful to know

- **Ctrl+K** jumps to any agent, workflow, tool or record by name.
- **Project** lists everything in your folder and suggests what is
  missing, such as an agent with no tests.
- **Runs** keeps every past run. Choose one and press Play to watch it
  again on the diagram.
- **Theme**, at the bottom of the sidebar, switches light and dark.

## Where next

- `examples/agents` holds eight larger agents to read and try: a
  researcher that proves its answers with quotes, a study coach, a task
  planner and others.
- `TUTORIAL.md` teaches the language itself, by hand, from an empty file.
- `README.md` describes everything Kosika can do.
- `KOSIKA.md` is the exact definition of the language.

## If something goes wrong

| What you see | What to do |
|---|---|
| `kosika` is not recognised | Close the terminal and open it again. If it still fails, use `python -m kosika.cli` in place of `kosika`. |
| "Model: none found" | Install Ollama and a model as described at the top, then run `kosika start` again. Everything except running agents works without one. |
| A run takes minutes | Small computers are slow at this. Try a smaller model, such as `ollama pull qwen2.5:3b`; it is faster and makes more mistakes. |
| "This program does not check" | Open the agent's Code tab. The message says what is wrong and where. |

## Giving Kosika to someone else

From the Kosika folder, this makes the one file to hand over:

```
python -m pip wheel . --no-deps -w dist
```

The file appears in `dist`. The other person installs it as in step 1.
