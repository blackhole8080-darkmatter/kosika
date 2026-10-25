A dependency-light first vertical slice of the Kosika AI systems language. Synapse was the earlier working codename.

**New here? Read [GETTING_STARTED.md](GETTING_STARTED.md).** Install, type
`kosika start`, and in fifteen minutes you will have looked at an agent,
tested it, run it and changed it.

## Status: prototype, not a released language

Read this before evaluating anything below.

Kosika is an **early prototype**. It is published so the design can be
reviewed and argued with, not because it is ready to depend on.

- **What works today.** A real parser, type checker, and runtime; typed
  records, functions, bounded loops and recursion, modules, and workflows;
  declared models, bounded agents, tools, and capabilities; approval-gated
  execution; deterministic mindmaps; stable structured diagnostics across
  local module graphs; a versioned machine-readable development interface; an
  LSP/editor backend; installable `kosika` and `kosika-lsp` commands; a local
  `.kos` workbench whose fixture-backed first-use path is browser-exercised;
  offline validation/replay of recorded traces; and a pinned local
  cross-language component contract.
- **What remains incomplete.** A demonstrated live-model setup, published
  releases and remote delivery, swarm messaging and consensus, component
  sandboxing, expression-precise checker spans, and the full AI
  author/check/repair/run loop. Several features in the tree are declared,
  checked, rendered, or exposed as host experiments but are not integrated
  into supported end-to-end behavior.
- **[KOSIKA_VISION.md](KOSIKA_VISION.md) is a design target, not a
  description of this codebase.** It describes a much larger language —
  tensors, memory bubbles, personality policies, an extension ecosystem, and
  multi-agent consensus. Most of those targets remain incomplete. The vision is
  included to show intent and to argue about, not to claim delivery.
- **The version is `0.1.0` and the API is unstable.** Interfaces will change.

If you are evaluating Kosika for real use today, the honest answer is that it
is not there yet. If you are evaluating the *idea* — that an AI system's
authority, evidence, and replayability should be enforced by the language
rather than by convention — the rest of this file is the substance.

See [KOSIKA.md](KOSIKA.md) for the versioned `kosika-language/0.1.0`
grammar, typing, execution, capability, replay, and support-status contract.

See [KOSIKA_PLAN.md](KOSIKA_PLAN.md) for the reconciled capability matrix,
verification record, and prioritized K01–K12 backlog.

## Repository layout

| Path | What it holds |
| --- | --- |
| `kosika/` | The language: lexer, parser, checker, runtime, and tools |
| `kosika_lsp/`, `editors/` | Editor support (language server, VS Code extension) |
| `examples/` | Example `.kos` programs and their fixtures |
| `benchmarks/` | The write-from-spec benchmark and its results |
| `tests/` | The test suite |
| `network/`, `ui/`, `hive/` | Network manifests, UI styles, and agent notes |
| `archive/` | Historical documents, not used by the code |
| `TUTORIAL.md`, `AGENT_GUIDE.md`, `KOSIKA.md`, `KOSIKA_PLAN.md` | Tutorial, guide for AI authors, language contract, and plan |
| `DEVELOPMENT.md`, `KOSIKA_VISION.md` | Developer guide and long-term vision |

## Start here

New to Kosika? [TUTORIAL.md](TUTORIAL.md) goes from nothing to a running,
replayable model workflow in about ten minutes:

```text
python -m pip install .
kosika new hello
cd hello
kosika run main.kos --input input.json --model-fixture responses.json
kosika run main.kos --input input.json --model YOUR_LOCAL_MODEL
```

## Can a model write Kosika?

[AGENT_GUIDE.md](AGENT_GUIDE.md) is the language guide written for AI authors.
`spec-eval` measures whether a model can write whole programs from it: 20
plain-language specs in `benchmarks/spec`, each judged by Kosika itself (the
checker, required declarations, and an exact result from a fixture-backed run).

```text
kosika spec-eval                                   # stored reference programs
kosika spec-eval --solver model --base-url URL --model NAME
kosika spec-eval --solver command --solver-command gemini -p ""
```

`--solver command` drives any command-line model that reads a prompt on standard
input. A failed attempt is shown what Kosika reported and may try again
(`--attempts`). Candidates run only with the task's fixtures, in a temporary
folder. Measured results are recorded in [KOSIKA_PLAN.md](KOSIKA_PLAN.md).

## Thinking steps

Four model tasks are single workflow steps with built-in, typed answers:

```kosika
model assistant

workflow main(ticket: Ticket) -> Routing {
  label   = classify ticket.text into ["billing", "bug", "other"]
  invoice = extract Invoice from ticket.text
  brief   = summarize ticket.text
  refund  = decide "Is this refund within policy?" given ticket.text
  routing = route(label, refund, brief, invoice)
}
```

`classify` always returns one of the listed labels with a confidence; `extract`
fills a record you declared; `decide` returns yes or no, a confidence, and its
reason. An answer that breaks one of those guarantees fails the run. Each step
is an ordinary typed model call underneath, so it is budgeted, traced, and
replayable, and it still needs the model granted at run time with `--model` or
`--model-fixture`. See [KOSIKA.md](KOSIKA.md) for the exact rules.

## How Kosika compares with Python

`kosika compare-python` runs six tasks written three ways: Kosika, short
"plain" Python, and "checked" Python that also rejects wrongly shaped data.
All three return the same answers. Measured on these six tasks:

| | Kosika | Python, plain | Python, checked |
| --- | --- | --- | --- |
| Lines of code, all six tasks | 84 | 81 | 108, plus 35 shared helper lines |
| Lines, four data tasks | 50 | 38 | 47 |
| Lines, two model tasks | 34 | 43 | 61 |
| Tokens, all six tasks | 817 | 633 | 899, plus 286 in helpers |
| Bad-data cases refused, of 20 | 20 | 9 | 20 |

Kosika is about the size of plain Python overall: somewhat longer for
ordinary data handling, shorter once a model is involved. It still uses more
tokens than plain Python, mostly for record declarations. Its advantage is
not size: it refuses every bad case without extra code, where plain Python
silently accepted 11 of 20, such as a label the model invented or `true` where
a number belongs. The bad cases were chosen by the author of the comparison,
so read that row as "what Kosika checks for free", not as an unbiased error
rate. Details and limits are in [KOSIKA_PLAN.md](KOSIKA_PLAN.md).

## Shell and file access, approved every time

A program can run a command or touch files only through a declared `system`
tool, and only when you grant it and approve each call:

```kosika
tool save_note {
  source system "files"
  operation: "write"
  access: execute
  input: FileWrite
  output: FileWritten
}
```

```text
kosika run main.kos --input input.json --allow-folder ./workspace --approve
```

`--allow-folder` grants one folder to the program's files tools; add
`--allow-shell` to grant its shell tools too. `--approve` stops before every
such call, shows the exact tool and arguments, and asks `Approve this one
action? [y/N]`. Anything but yes stops the run with nothing done for that call.
With no terminal to ask, the run pauses instead of proceeding. See
[KOSIKA.md](KOSIKA.md) for the limits that hold even after approval.

## Use Kosika from an AI assistant (MCP server)

`kosika-mcp` is a Model Context Protocol server. Connected to an assistant such
as Claude Code or the Gemini CLI, it lets the assistant read the language
guide, then write, check, run, replay, and map Kosika programs by itself.

| Tool | What it does |
| --- | --- |
| `kosika_guide` | Returns `AGENT_GUIDE.md` |
| `kosika_check` | Checks a program; returns codes, messages, and lines |
| `kosika_run` | Runs a workflow with the inputs and scripted answers supplied |
| `kosika_replay` | Validates a trace from `kosika_run` and returns its result |
| `kosika_map` | Describes what a checked program declares |

Connect it by adding a stdio server whose command is `kosika-mcp` (or
`python -m kosika.mcp_server`). For Claude Code:

```text
claude mcp add kosika -- kosika-mcp
```

The server has no power of its own. Programs arrive as text and are checked in
a temporary folder; no tool takes a file path, address, or command.
`kosika_run` never calls a real model or tool: answers come only from the
`model_answers` and `tool_answers` the assistant supplies, and a program that
needs one it was not given fails. Runs are bounded to 16 model calls, 16 tool
calls, and 10 seconds.

## Recovering from a failed step

A step can name what to do if its call fails:

```kosika
label = classify ticket.text into ["billing", "bug", "other"] otherwise keyword_label(ticket)
```

If the model's answer is unusable (not one of the labels, the wrong shape, not
JSON at all) the fallback call runs instead, and the trace records why. The
fallback must return the same type. Running out of budget, a missing approval,
or a missing model binding still stops the run, and `otherwise` is not allowed
around a tool that changes the outside world.

When the fallback is just a fixed value, write the value:

```kosika
hits = execute hands.search_files(".", query) otherwise {matches: []}
```

It is checked against the type the call would have returned. The same works
as a branch of a step's `match`: `case _ { {answer: "", quote: ""} }`.

The value may use the workflow's inputs and the steps before it:

```kosika
reply = use_model helper(msg.text) otherwise {text: "I could not answer: " + msg.text}
```

If such a value is wrong, the error names the step and where the value
stands, for example "Step 'reply' in workflow 'main': the value after
`otherwise` ...".

## How good is it? The benchmark

`benchmarks/researcher` asks the researcher agent forty questions about a
folder of notes, with nothing scripted, and keeps every answer. On the
last run, `qwen2.5:7b` with search by meaning was right 39 times of 40 and
showed no false answer; the much smaller `qwen2.5:1.5b` was right 32
times and showed 8 false answers. The first run found four faults in the
agent and the search, which were fixed. Its README has the full tables,
the faults, and what the numbers do not show.

```
python benchmarks/researcher/run.py --model qwen2.5:7b --embed-model nomic-embed-text
```

## An experiment: animation described in Kosika

Kosika does not draw. As an experiment, a program can *describe* a scene
as plain data, and one command turns that into a web file any browser
plays:

```
kosika motion examples/motion/logo.kos --input ask.json --out logo.html
```

The workflow returns one record: `width`, `height`, `seconds`,
`background`, a list of shapes (`id`, `kind`, `x`, `y`, `size`, `color`,
`text`; kind is circle, square, ring or text) and a list of moves
(`shape`, `what`, `to`, `start`, `seconds`, `ease`, and if wanted `repeat`
and `color`). What is x, y, size, turn, fade or color. Ease is linear, in,
out, both, `bounce` (overshoots a little and settles) or `back` (goes
there and returns). `repeat` is how many times, with 0 meaning without
end. Because the scene is built by ordinary Kosika code, a loop
can place twelve dots on a circle and start each a little after the last
(`Number` has `sin()`, `cos()` in degrees, and `sqrt()` for this), and a
test can assert that nothing is still moving when the scene ends.

Before anything is written the scene is checked, and every mistake is
said in words: a move that names no shape, a kind that does not exist, a
move that ends after the scene does. The file it writes holds one SVG
picture, fetches nothing, and runs no script.

In Studio, a program whose `main` gives back shapes and moves gets a Scene
tab: the animation plays there, a slider drags through time to look at any
moment, the inputs can be changed and the scene made again, and "Save as a
web file" gives the same file the command writes. For a program of plain
code nothing else is needed. For one that asks a model, such as the
`animator` agent, the tab has a box to describe the motion; the run is an
ordinary run, and the scene it gives back is checked and drawn the same
way.

It is deliberately small, four shapes and six kinds of move, to find out
whether describing motion this way is worth growing.

## Python SDK

`kosika.sdk` builds a Kosika program from Python instead of a `.kos` file:

```python
from kosika.sdk import App, ref

app = App()
question = app.record("Question", text="Text")
answer = app.record("Answer", text="Text")
assistant = app.model("assistant", input=question, output=answer)
main = app.workflow("main", output=answer, question=question)
main.use_model("respond", assistant, text=ref("question.text"))

program = app.build()              # parsed and checked like any .kos file
app.save("main.kos")               # the same program as source
app.run(inputs={...}, model_bindings={"assistant": provider})
```

It is not a second language. The builder renders ordinary `.kos` text and
`build()` sends it through the same parser and checker, so the result is the
same checked program, with the same limits, approvals, and replayable traces.
`App` also has `tool`, `agent`, `swarm`, and `source` (for hand-written `fn`
declarations); a workflow has `use_model`, `ask`, `execute`, `call`, and
`function` steps. The SDK grants nothing: `run` takes explicit bindings, as
`kosika.runtime.run` does. Run `python examples/sdk_hello.py` for a complete
offline example.

## Language and runtime expansion

Kosika includes local imports with explicit exports, typed workflow calls, pure
functions, closures, higher-order values, bounded loops and recursion, nested
replay, model budgets/telemetry, durable checkpoints and approval pauses, a
bounded read-only DAG API, host-granted JSON/HTTP/SQLite adapters, development
commands, an LSP/editor backend, and evaluation fixtures.

See [DEVELOPMENT.md](DEVELOPMENT.md) for runnable examples, APIs, security
boundaries, and current limitations. A working local model endpoint has been
exercised end to end, but model quality and hosted availability remain
environment-dependent and are not release guarantees. Parallel DAG execution is a
host API rather than language swarm execution: a `swarm` declaration is checked,
mapped, and required to state `require approval`. `run` does not accept one; it
runs only through `kosika swarm` under one approval (see
[Approved swarm runs](#approved-swarm-runs)). Editor/LSP integration is implemented
but not equivalent to a packaged, production-tested IDE experience.

### Hello World

The smallest complete Kosika program returns a typed greeting:

```kosika
record Greeting {
  text: Text
}

fn greeting() -> Greeting {
  return {text: "Hello, world!"}
}

workflow main() -> Greeting {
  step greet {
    let result: Greeting = greeting()
    return result
  }
}
```

Run and replay it locally:

```text
python -m kosika.cli check examples/hello_world.kos
python -m kosika.cli run examples/hello_world.kos --trace hello_world.jsonl
python -m kosika.cli replay hello_world.jsonl
```

The run prints `{'text': 'Hello, world!'}` and records a replayable trace.

### Typed input/output

`examples/typed_input_output.kos` accepts a typed `EvaluationInput`, applies a
pure scoring function, and returns a validated `EvaluationResult`:

```text
python -m kosika.cli check examples/typed_input_output.kos
python -m kosika.cli run examples/typed_input_output.kos --input examples/typed_input.json --trace typed_input.jsonl
python -m kosika.cli replay typed_input.jsonl
```

The example returns `{'score': 0.87, 'accepted': True}`. Invalid input is rejected
before workflow execution.

## Pure typed functions

Kosika functions provide deterministic computation without model, tool, network,
or host authority. The function slice supports top-level declarations with typed
parameters, immutable typed local bindings, and a final return expression:

```kosika
fn person_name(person: Person) -> Text {
  return if person.name == "" {"Anonymous"} else {person.name}
}

fn make_greeting(person: Person) -> Greeting {
  return {text: person_name(person: person)}
}
```

Workflows invoke them with ordinary function-call syntax. Function arguments and
results are checked statically, validated at runtime, and recorded in version 5
traces for offline replay. Pure functions may call other pure functions in return
expressions and workflow arguments. Unannotated recursion and over-deep call graphs
are rejected; explicitly bounded recursion uses `recurse N`.

Function locals use `let name = expression` or `let name: Type = expression`. The
annotation is optional: when it is omitted the checker infers the type from the
initializer and then checks every later use exactly as it would an explicit one.
Initializers run in order; later locals and the return expression can refer to
earlier bindings. Names cannot be rebound or shadowed.

Use `var` for a binding that is assigned later. A `let` binding is immutable, and
the checker rejects an assignment to it, to a parameter, or to an unknown name —
and rejects an assignment whose value type does not match the binding's type.
Assignment is a statement, so it may not appear in `return` position. A loop body
may assign to a `var` declared outside it, which is how an accumulator is written:

```kosika
fn total(box: Box) -> Number {
  var sum = 0
  for value in box.values {
    sum = sum + value
  }
  return sum
}
```

A function body may also contain loops, conditionals, and assignments as
statements. The body runs in the order it is written, so a `let` placed after
a loop sees the loop's result.
A braced body with no trailing expression evaluates to `Null`, so
`repeat 3 times { sum = sum + 1 }` needs no filler value.

See `examples/local_bindings.kos` for a runnable example
(`python -m kosika.cli run examples/local_bindings.kos`).

Pure expressions support precedence-aware numeric arithmetic, numeric comparisons,
strict same-type equality, Boolean `and`/`or`/`not`, unary negation, parentheses,
and typed `if condition {value} else {value}` expressions. Boolean operators and
conditionals evaluate only the selected branch.

```text
python -m kosika.cli check examples/pure_function.kos
python -m kosika.cli run examples/pure_function.kos --input examples/person.json
```

### Function values and escaping closures

Functions are first-class values. A function type is written `fn(parameter: Type, ...) -> Output`,
and a function can return another function, accept one as an argument, or be bound
to a local with an explicit function type. A nested function that captures an outer
binding is returned with `ref function_name`; the capture is lexical and immutable.

```kosika
fn make_adder(base: Number) -> fn(value: Number) -> Number {
  fn add(value: Number) -> Number { return base + value }
  return ref add
}

fn apply(operation: fn(value: Number) -> Number, value: Number) -> Result {
  return {value: operation(value: value)}
}

workflow main() -> Result {
  step create {
    let add_ten: fn(value: Number) -> Number = make_adder(base: 10)
    return add_ten
  }
  step use {
    let result = apply(operation: add_ten, value: 5)
    return result
  }
}
```

Top-level functions can be passed directly with `ref function_name`. Bound function
values can be invoked directly as expressions or as workflow step calls. The checker
rejects signature mismatches, records that try to hold function values, and references
to functions that are not visible at the call site. Runtime invocation checks the
closure signature and capture set, and replay rejects tampered closure payloads.

Try the complete example offline from this repository:

```text
python -m kosika.cli check examples/closures.kos
python -m kosika.cli run examples/closures.kos --workflow main --trace closures.jsonl
python -m kosika.cli replay closures.jsonl
python -m kosika.cli mindmap examples/closures.kos --format json
```

Nested declarations, recursion, mutation, and loops remain separate language-core
slices.

## Typed model workflows

`model` declares a typed interface. `use_model` makes one model request and accepts
only a complete JSON record matching its output type. Providers and credentials are
configured by the host; declaring a model does not grant network access.

```kosika
record Question { text: Text }
record Answer { text: Text confidence: Number }
model writer { input: Question output: Answer }

workflow answer(question: Question) -> Answer {
  step respond {
    let result: Answer = use_model writer(text: question.text)
    return result
  }
}
```

Model deployments and request parameters can be declared without putting provider
credentials in source. `temperature` is bounded to `0..2`, `top_p` to `0..1`,
and both penalties to `-2..2`; output tokens are bounded to `1..1000000`.
`stop` accepts up to four unique non-empty strings, each at most 256 characters.

```kosika
model research_writer {
  input: Question
  output: Answer
  deployment: "local-research-model"
  parameters: {
    temperature: 0.2
    max_output_tokens: 2048
    top_p: 0.9
    frequency_penalty: -0.1
    presence_penalty: 0.1
    stop: ["END"]
  }
}
```

Try the complete example offline from this repository:

```text
python -m kosika.cli check examples/model_answer.kos
python -m kosika.cli run examples/model_answer.kos --workflow answer --input examples/question.json --model-fixture examples/model_answers.json --trace answer.jsonl
python -m kosika.cli replay answer.jsonl
```

The input file maps workflow parameter names to records. The fixture maps declared
model names to ordered lists of response records. These are deterministic test
responses, not live inference. To use a local model, replace `--model-fixture ...`
with `--model YOUR_MODEL`. That one flag grants the model to every model the
program declares, and Kosika prints the grant. The endpoint is `--model-url`, then
`KOSIKA_MODEL_URL`, then `http://localhost:11434/v1`; `--model-protocol ollama`
selects Ollama's native API. To grant a single declared model instead, use
`--model-binding writer --model YOUR_MODEL --model-url URL`. The provider uses
`KOSIKA_MODEL_API_KEY` if supplied.

A model that is reached through a program instead of a URL, such as a vendor's
signed-in command-line tool, is granted with `--model-command`, which must be
the last option:

```
kosika run main.kos --input input.json --model-command gemini -m gemini-3.5-flash-lite -p ""
```

### Starting an agent, and seeing what a run did

`kosika new` can start from a complete, tested agent instead of hello-world:

```
kosika new my-desk --kind support     # an agent that remembers customers
kosika new my-claims --kind expense   # a model reads a receipt, code decides
kosika new my-notes --kind notes      # a model finds tasks, code organises them
```

Each project has its own tests (`kosika test main.kos`, no model needed) and
a README with the command to run it on a model.

After a run that wrote a trace, `kosika story run.jsonl` tells what happened
in plain words: every step, what was asked and answered, the quotes that
backed an answer, what was rejected and asked again, how a vote went, what
was recalled or remembered, when a fallback took over, and what the run cost
in model calls, tokens, and seconds. It reads only the trace. It first checks
that the trace replays and says so, or warns when it does not.

### Kosika Studio

```
kosika studio FOLDER --model MODEL --allow-folder FOLDER --allow-site SITE --allow-shell
```

opens Kosika Studio in a window of its own (through Edge or Chrome when one
is installed, otherwise as a browser tab; `kosika app` always uses a tab).
It is served from your own machine and shows every agent under FOLDER.

It opens on the Assistant: the first agent that can be talked to, preferring
one in a folder named `assistant`. While an agent works you watch it. Each
step lights up as it starts and turns green when it finishes, a step that
calls another workflow unfolds to show that workflow's steps, the branch
that was taken is marked, and a line under the step says what it is doing
right now: asking the model, looking in memory, using a tool. A running
count shows steps, model calls, tool calls, tokens and seconds.

After a run, "What it did" tells it in plain words and "Requests" shows
exactly what was sent to the model and exactly what came back. "Memory"
shows what the agent remembers, entry by entry. The other tabs show every
workflow, the parts the agent is made of, its records and how they refer to
each other, its tests, its past runs, and its code. The sidebar also lists
all agents, all runs, and the permissions this Studio was started with.

The Code tab helps while you write. Press Edit and, as you type, a bar
above the text offers words that begin with what you typed: the
language's own words (each with a one-line reminder of how it is used),
the built-in methods, and the names declared in the file. Tab takes the
first one; after a dot only fields and methods are offered. Under the
text, the first problem the checker finds is shown a moment after you stop
typing, with its line; click it to jump to the word at fault. This comes
from the checker, not from a model, and nothing is saved until you press
Save.

The Project page, in the sidebar, is the whole folder in one place: every
agent, workflow, model, tool, memory and data record, each with the program
it belongs to and its line, and one search box over all of them. Above the
list are suggestions, such as "it has no tests", "this model step has no
`otherwise`", "this tool is declared but never used" or "nothing says what
it is for". Each suggestion comes from a rule applied to the checked
program, names its rule, and never comes from a model.

The Map tab is the place to start with an agent you do not know. It says
what the agent is for, what you give it and get back, what it only looks
at, what it may change (always after asking you), which models it asks,
what it remembers, and what it does in order; a step that runs another
workflow opens to show that workflow's steps. Every item says where it
comes from: "from the program" with its line, "from the comment at the
top of the file", or "written by a person". Kosika never guesses a
purpose: if there is no comment, the map says none is written, and you can
save one sentence of your own, kept in `purpose.txt` beside the program.
Where the agent reaches something Kosika cannot look inside, such as a
program it starts or an outside server, the map says so. No model writes
any part of the map.

An answer appears word by word while the model writes it, in the chat and
under the step that asked.

Above the steps, a run is also drawn as a moving diagram. The program
sits in the middle, you are on the left, and on the right are the parts it
uses: each model, your files, the web, programs it may start, outside
tools, its memory, and its plain code. Each time the run reaches for one
of them a light travels along the line, the part lights up, and its count
goes up. When the run waits for your yes the light comes to you; when it
ends, the program turns green, or red if it failed.

A past run can be watched again. Choose it under Runs and press Play: the
same diagram plays the recording step by step, at one, two or four times
the speed, with a line saying what each step did. Nothing runs and no
model is asked.

Ctrl+K, or "Jump to" at the foot of the sidebar, opens one box over every
page, agent, workflow, model, tool, memory and record; type a few letters
and press Enter to go there. "Theme" beside it switches between your
computer's setting, light and dark, and the choice is kept in your
browser.

Studio's own moving mark, in the sidebar and beside page titles, is not a
stored picture. Studio carries a Kosika program, `app_assets/brand.kos`,
runs it when it starts, and plays the scene it describes: ten dots fly
out into a ring, the tile drops in with a bounce, the K fades up, and a
slow wave of light goes round. Pages, cards and panels ease in one after
another. If your computer is set to reduce motion, all of it stays still
and the mark is shown finished.

### Sessions: let an agent work freely, behind a wall

Normally an agent's file and shell tools work in the folder you granted,
and every change waits for your yes. With `--in-session` each agent gets
its own *copy* of that folder instead, and the programs it starts run
behind a wall:

```
kosika studio my_agents --model qwen2.5:7b --model-protocol ollama --allow-folder PROJECT --allow-shell --in-session
```

`kosika desktop` makes Studio an app: a shortcut on the Desktop and in the
Start menu that opens Studio in its own window with no console, and stops
when the window is closed. It runs `kosika start`, so it grants nothing.
What would have been printed goes to a log file, and a failure to start is
shown in a message box. `kosika desktop --remove` takes the shortcuts away.
Windows only for now.

`kosika pill` shows Kosi, the assistant, as a small translucent pill that
stays against the right-hand edge of the screen: click it or press its key combination
(Ctrl+Alt+K when free), type, and the answer appears above it.
`kosika desktop --kosi` makes its shortcut.

On the Assistant page, the box "Not sure which agent?" takes a request
and passes it to the agent that fits. The agents' own descriptions (the
comment at the top of each program) are shown to the model, which names
one; it can only name an agent that exists. If no model can be asked, the
agent whose description shares the most words with the request is used.
The page says which agent was chosen and why, then that agent runs exactly
as if you had opened it yourself, with the same grants and the same plan to
approve.

The folder, starting programs and sessions can also be chosen inside
Studio, on the Permissions page, with no command line. The folder is picked
in the computer's own folder window, which Kosika opens; the page can only
confirm what was picked there, so nothing but a person at the computer can
grant a folder. A whole drive, the user folder or a system folder is
refused. A change is refused while an agent is working or a session is open.

- A program started in a session sees the copy and nothing else of your
  files, on a read-only system, with no network, for at most 60 seconds.
- Because nothing it does can reach the real folder, its file and shell
  steps are not stopped one by one. The Live view says "allowed inside the
  session".
- The Session tab lists every file that is new, changed or removed in
  the copy, and each one opens to show the lines taken out and put in.
  "Bring these into the real folder" is your yes; "Throw the session away"
  leaves the real folder untouched. Either one ends the session, and the
  next run starts a fresh copy.
- Each file has a tick. Untick the ones you do not want: only the ticked
  files are brought back, and the rest go when the session ends.
- Tools that reach elsewhere (an MCP server, the web) are not walled and
  still ask you each time.

On Windows, `--session-system NAME` chooses which WSL system the sessions
run in, for example `--session-system kali-linux`, so an agent has that
system's programs. It must be a system WSL already has, with bubblewrap in
it. The wall is the same: the copy only, read-only system, no network.

The wall is bubblewrap, which on Windows runs inside WSL. A session has
only the programs the Linux side has, so a tool that starts `python` or
`pytest` needs them installed there (on Ubuntu: `python-is-python3` and
`python3-pytest`). It is a strong wall, not a perfect one: it
relies on the Linux kernel, and what you bring back without reading is as
real as anything you wrote yourself.

When an agent reaches an action that changes something, it stops and a card
shows that one action and its exact arguments, with Allow once and Refuse.
Nothing happens until you answer; no answer in ten minutes is a refusal.

With `--allow-shell` an agent may also start the programs its shell tools
name, such as git or python, inside `--allow-folder`. Each start is one of
those cards and shows the exact command. A program is given its arguments
as a list, never through a shell, and is stopped after 60 seconds.

A program that declares a swarm gets a Swarm tab. After you allow the run,
you watch its agents take turns: the one being asked lights up, what each
posts to their board appears as a conversation, and the round is counted.

The Code tab edits the program. Check tells you whether the text is a valid
program without saving it; Save writes the file. The Agents page creates a
new agent from a working example, in a new folder, never over an old one.

Studio can do only what the command that started it allows. Without
`--model` it shows and tests but cannot run anything. A run uses the agent's
own `memory` folder, may look inside `--allow-folder`, may read only the
sites given with `--allow-site`, and saves its trace under the agent's
`runs` folder. Shell tools are bound only with `--allow-shell`. The server
listens on 127.0.0.1 only and requires a random token on every request.

### An agent that chooses its own tools

Most steps say exactly what happens. An `ask` step hands a goal to an agent
and lets its model choose:

```
agent scout {
  model: thinker
  can: [list_folder, look_at]
  max_steps: 6
}

workflow main(msg: Msg) -> Said {
  found = ask scout(msg.text)
  said  = tell(found)
}
```

The model is told what each tool in `can` is for, picks one, reads what it
gave back, and picks again, for at most `max_steps` turns. Then it must
answer with the model's output record. Every choice, its arguments and its
result are recorded, `kosika story` lists them, and Studio shows each one as
it is made.

It can reach only the tools in `can`, and only as far as the person running
it granted. A look that fails, such as a file that is not there, is told to
the model so it can try something else. An answer given in plain words is
asked for once more as the record. A tool that changes something never
runs without your yes; how an agent asks for it is the next section. Asking for a tool it was never given ends the run.

This works with `--model-protocol ollama` and `openai`, on a model that was
trained to choose tools. A files tool with `operation: "peek"` reads only
the first 4000 characters, which suits a model reading for itself.

### An agent that plans before it changes anything

```
agent builder {
  model: maker
  can: [list_folder, create_file]
  max_steps: 5
}
```

`create_file` is a tool with `access: execute`: it changes something. An
agent that has a model, such a tool, and at least two turns plans first, and
you do not have to say so. It looks around as before, then asks for every
change it wants in one reply. Nothing has happened yet. That list is its
plan: Studio shows each change in order with its exact details, and
`kosika run --approve` prints the same list. One yes covers exactly that
list, in that order, and nothing else. A no means none of it happens.

After the yes the changes are carried out one by one. If one fails, the ones
after it do not happen. The agent is then shown what each change gave back
and writes its answer. If it then asks for more changes, they do not happen
and it is told so: only what you saw is done.

The yes is tied to the plan that was shown: the model is not asked again
after you answer, so it cannot swap in a different plan. Inside a session
(`--in-session`) the plan is still shown and asked about once, and the
changes then happen in the session's copy.

Write `plan: off` in the agent to go back to one change per step, each
asked about on its own. `plan: first` says the same as the default, and the
checker then insists the agent has what planning needs.

### What an agent really changed

A model will sometimes answer "I created greet.py" without having asked for
a single change. So an agent's answer record may have a field named `did`:

```
record Work { summary: Text did: Text[] }
```

Kosika fills `did` in itself, from the changes that actually ran, one line
each, such as `write_file calc.py`. Whatever the model put there is thrown
away. If nothing ran, the list is empty, and the program can say so instead
of repeating the model's claim. Replaying a trace works the list out again
from the recorded tool calls, so a trace cannot claim a change its calls do
not show.

### Skills an agent picks before it begins

A skill is a short note of know-how. Put notes in a folder named `skills`
beside the agent's `main.kos`, one file each:

```
# What a good readme has
When: readme, explain the folder

Start with a title line that begins with "# " and names the folder.
The next line is one sentence saying what the folder is for.
```

The first line says what the skill is. The `When:` line lists the words or
phrases that mean it fits. The rest is given to the model.

Before an `ask` step begins, skills are picked in two ways, at most three
in all. First by words: a skill fits when one of its `When:` words is in
what the agent was given. Then the agent's own model is shown the list of
skills, one line each, and asked which fit; this finds a skill when the
request means it without saying its words. That is one short extra model
call, and it counts toward the workflow's budget. The model can only name
skills that exist. If it cannot be asked or its answer is not a list, the
words alone decide.

The picked skills are given to the model with its instructions, written
into the run's trace with why each was picked, and shown in Studio as the
agent starts. A step that begins again after you answer a plan keeps the
skills it first had.

A skill can also say what the work must look like, with two more kinds of
line at the top:

```
# How a list for a person is written
When: todo, checklist, shopping list
Files: *.txt
Check: every line starts with "- [ ] "
```

`Files:` names which files the checks are for. A check is one of
`starts with "..."`, `contains "..."` or `every line starts with "..."`.

Before a plan is shown to you, every file it would write is checked: it must
not be empty, must not just repeat your request, must not be written twice,
and must keep the checks of the picked skill that fits its name. A plan that
fails goes back to the model once, with what is wrong. If the next plan
still fails, you are shown it with the problems listed above the button, so
the decision is yours and you make it knowing.

A skill is advice. It gives an agent no tool and no permission it did not
have. `kosika studio` and `kosika run` read the folder; nothing has to be
declared in the program. A note that cannot be used, such as one with no
`When:` line, stops the run and says which file and why.

### Vectors, matrices, relations and graphs

A vector is a `Number[]` and a matrix is a `Number[][]`. They are ordinary
values, so they go into traces and replay like anything else.

```
let a = [[1, 2], [3, 4]]
let c = a.times([[5, 6], [7, 8]])        // [[19, 22], [43, 50]]
let x = [[2, 1], [1, 3]].solve([3, 5])   // [0.8, 1.4]
let d = a.determinant()                  // -2
let i = 3.identity()                     // the 3 by 3 identity
let n = [3, 4].norm()                    // 5
```

| On | Methods |
| --- | --- |
| a vector | `plus`, `minus`, `scaled(by)`, `dot`, `cross`, `norm`, `mean`, and `sum`, `min`, `max` as before |
| a matrix | `rows`, `columns`, `transpose`, `plus`, `minus`, `scaled(by)`, `times`, `apply(vector)`, `power(n)`, `trace`, `determinant`, `inverse`, `solve(right)` |
| a number | `identity()`, `zeros(columns)` |
| a relation: a square matrix of 0s and 1s | `is_reflexive`, `is_symmetric`, `is_antisymmetric`, `is_transitive`, `bool_times`, `closure` (Warshall) |
| a graph | `edges.adjacency(nodes)` and `edges.directed_adjacency(nodes)` build the matrix from pairs of node numbers starting at 0; then `degrees`, `neighbours(node)`, `shortest_path(start, end)`, `components`, and `power(n)` to count walks of length n |

Whole numbers stay whole and exact, however large: `[[2]].power(200)` is
exactly 2 to the 200. Anything with a decimal is rounded to 9 places, like
`sqrt()`, so the same program gives the same answer on every computer.

Decimal work is done by NumPy when it is installed, and by plain Python when
it is not, with the same answers. On this project's laptop a 500 by 500
product took 0.26 s with NumPy; plain Python took 1.1 s for 200 by 200.
Kosika's part is the notation and the checking. The speed is NumPy's.

A shape that does not fit stops the run and says what the shapes were: "a 2
by 2 matrix cannot be multiplied by a 3 by 1 one: the first has 2 columns
and the second has 3 rows, and those must be equal". A matrix with no
inverse says so instead of returning nonsense.
`examples/discrete_maths.kos` shows relations and graphs with tests.

### Text with values in it, the time, and chance

```
let line = f"{order.count} x {order.item} is {order.price * order.count}"
```

`f"..."` writes the value of each `{expression}` into the text. A value
must be Text or a Number. Write `{{` and `}}` for braces. An expression
inside the braces cannot itself contain a quotation mark.

```
workflow main(msg: Msg) -> Out {
  today = now                // Moment: date, time, weekday, year, month, day, hour, minute, epoch
  roll  = random 1 to 6      // RandomNumber: value, both ends included
  out   = build(today, roll)
}
```

`now` and `random` are workflow steps, not functions, because a function
must give the same result every time. What the clock said and what was
drawn are written in the trace, so replaying a run sees the same moment
and the same number. Neither needs a grant. In a test, script them as
`answers clock_now: [...]` and `answers random_number: [{value: 4}]`.

### Failing and recovering

```
fn share(amount: Number, people: Number) -> Number {
  if people <= 0 { fail "there must be at least one person" }
  return amount / people
}

let each = try { share(total, people) } otherwise problem { return {text: f"Could not split it: {problem}"} }
let safe = try { items[7] } otherwise { 0 }
```

`fail "reason"` stops with a reason. `try { } otherwise { }` gives the
second value when the first fails; `otherwise NAME { }` also names the
reason, as Text. Both sides must give the same type. A failure that no `try`
takes travels out to the workflow step, where the step's own `otherwise`
can take it; if nothing does, the run fails with that reason. A spent budget
or a cancelled run cannot be caught.

### Records with functions of their own

```
record Item {
  name: Text
  price: Number
  count: Number

  fn total(self) -> Number {
    return self.price * self.count
  }

  fn discounted(self, percent: Number) -> Item {
    return {name: self.name, price: self.price * (100 - percent) / 100, count: self.count}
  }
}

let cost = item.discounted(10).total()
```

`{..item, price: 3}` is a copy of `item` with those fields changed. The
record it copies is not changed.

A function written inside a record takes the record as `self` and is called
on a value of that record. It is an ordinary pure function underneath. There
is no inheritance, and a method cannot change the record: it returns a new
one. A method may not be named like a field of its record or like a map
method (`get`, `has`, `set`, `remove`, `keys`, `values`, `length`).

### Files and pages in one line

```
workflow main(msg: Msg) -> Out {
  note  = read file "notes/todo.txt"                 // note.text
  names = list files "notes"                          // names.names
  page  = fetch "https://en.wikipedia.org/wiki/Tea"    // page.title, page.text
  saved = write file "out/report.txt" with report.text // saved.bytes
  out   = build(note, page)
  require approval
}
```

No tool has to be declared. The access is the same as for declared tools:
the folder and the site are granted by whoever runs the program, a path
outside the folder is refused, and `write file` waits for a yes that shows
the path and the text.

### Steps at the same time

```
workflow main(q: Question) -> Out {
  parallel {
    wiki  = fetch q.first
    other = fetch q.second
    kind  = classify q.text into ["fact", "opinion"]
  }
  out = build(wiki, other, kind)
}
```

The steps in a `parallel` block do not wait for each other: asking a model
and using a tool that only looks are started together. A step in the block
may not use another's result, and may not be a workflow call, an `ask`, a
`match`, a memory or board step, or anything that changes the world; those
happen in order for good reason. The trace is written in the order the
steps appear, exactly as if they had run one after another, so it replays
the same way.

How much time it saves depends on what is behind the steps. Three web
pages or three different hosted models overlap fully. Two steps that ask
the same local model do not, because one model on one computer answers one
question at a time.

### Checking a model's answer with your own code

A model step can name a function that judges its answer:

```
fn proven(found: Found, hits: Hits) -> Text {
  if proof(hits, found.quote).is_empty() { return "Your quote is not in any of the lines." }
  return ""
}

found = use_model reader(prompt.text) verify proven(found, hits) retry 1 otherwise {answer: "", quote: ""}
```

The function is given the answer under the step's own name, beside anything
earlier steps produced. It returns what is wrong, or `""` when the answer
may stand. A complaint refuses the answer, and with `retry` the model is
shown its answer and your complaint and asked again. The check that passed
is recorded in the trace and replay looks at it again.

### Fast model first, strong model when needed

```
found = use_model reader(prompt.text) verify proven(found, hits) escalate strong otherwise {answer: "", quote: ""}
```

The step asks `reader`. If its answer is refused, by your `verify` or
because it is not a valid record, the same question goes to the model the
program calls `strong`, held to the same check. Say which real model
stands behind each name when you run:

```
kosika studio FOLDER --model-protocol ollama --model qwen2.5:3b,strong=qwen2.5:7b
```

Every declared model is answered by the first one named, except those
given a model of their own with `NAME=MODEL`. The same form works for
`kosika run`, `chat` and `measure`.

### Measuring a program on real models

An `eval` is written like a `test`, but the models answer for themselves.
Tools can still be scripted, so every model is given the same thing to work
with:

```
eval "it copies a number exactly" {
  answers search_files: [{matches: ["notes/home.md:3: Rent is 900 euros a month."]}]
  a = main({text: "How much is the rent?"})
  expect a.text.contains("900")
}
```

`kosika test` skips evals. `kosika measure` runs them:

```
kosika measure main.kos --model qwen2.5:3b --model qwen2.5:7b --model-protocol ollama --repeat 2
```

It reports, for each model, how many runs passed, the seconds taken and the
model calls made, then each eval by model, and the first failure of any
eval that did not always pass.

### Example agents

`examples/agents` holds eleven agents written in Kosika, each with its own
tests. Point Studio at the folder to try them:

```
kosika studio examples/agents --model MODEL --allow-folder YOUR_FOLDER --allow-shell
```

- `researcher` answers questions from the files in the granted folder. Code
  searches for the key words, the model answers from the lines found and
  must give an exact quote, and code checks the quote is really there and
  names the file and line. An answer it cannot prove is not shown.
- `doctor` measures this computer's disk, memory, battery and biggest files
  with small read-only Python programs you approve, and reads the numbers
  in code. Written for Windows.
- `coach` quizzes you on any subject. The model writes the question; code
  shuffles the choices, marks the answer, and re-asks what you missed.
- `workbench` starts git and python for a code project and explains what
  they print.
- `planner` keeps tasks, plans a day into focus blocks, and writes a
  standup note.
- `scout` is given a question and two tools and chooses for itself which
  files to open.
- `builder` makes the files you ask for, after showing you its whole plan:
  every file with its full text, and one yes or no for the lot.
- `assistant` is Kosi, the one you talk to by typing. It greets you by the
  clock, does sums exactly in code, keeps reminders with or without a time,
  remembers what you tell it and your name, and answers questions in a few
  sentences meant to be heard. It touches nothing on the computer, and says
  so when asked to. Studio opens on it with an orb that turns while it
  thinks and pulses while it speaks, can speak its answers aloud, shows a
  reminder when it falls due, and offers things to tap next.
- `coder` does one small task in a Python project. It reads the files it
  needs, shows you every file it will write, writes them, runs the tests
  and reports. Run it with `--in-session` so it works in a copy. Say `fix`
  after failing tests and it tries again with what they printed.
- `author` writes Kosika programs. The model writes a program with its
  own tests, Kosika checks it and runs the tests, and what is wrong goes
  back to the model up to two more times. You can run a test of your own
  against what it wrote, and save it after approving.
- `animator` makes motion designs from a description. The model fills in
  a short plan (a pattern, how many shapes, colours, a title, how long)
  and code builds the whole scene from it, every shape and every timed
  move: a ring, a grid, a wave or a burst. Start the request with
  `draw:` and the model writes the scene itself, with each mistake sent
  back to it. Studio plays the result in its Scene tab.

A tool with `source system "kosika"` and `operation: "check"` takes a
program as text, `{source}`, and gives back `{ok, problem, tests, passed}`:
whether it is valid Kosika, how many of the tests written in it pass, and
the first thing wrong. It needs no grant, because it reaches nothing: the
text is checked in a folder made for the purpose, can import nothing, and
its tests use scripted answers only.

A files tool with `operation: "search"` takes `{path, query}` and gives
back the lines under that folder holding the words, as `path:line: text`,
best first. A word is matched where a word begins, so `rent` finds
`rental` and not `current`, and a word of three letters or fewer must be
the whole word. It only reads, skips hidden folders, and never leaves the
granted folder.

`operation: "find"` takes and gives the same, but ranks lines by meaning
instead of shared words, so "what do I pay my landlord" finds "Rent is 900
euros a month". It needs an embedding model, which you grant when you
start, exactly as you grant a folder:

```
ollama pull nomic-embed-text
kosika studio my_agents --model qwen2.5:3b --model-protocol ollama --allow-folder NOTES --embed-model nomic-embed-text
```

Without `--embed-model` a `find` tool cannot be reached, and a step that
uses it falls to its `otherwise`. The researcher uses both: key words
first, then the lines nearest in meaning. What the embedding model says
about each line is kept in `.kosika/embeddings.sqlite3` in your home
folder, never in the folder being searched, so each line is only sent to
the model once.

The same grant gives a tool that compares two texts: `source system
"meaning"`, `operation: "near"`, taking `{a, b}` and giving `{score}`
from -1 to 1. The researcher uses it as a second check on an answer. A
quote can be real and still not answer the question, so the question and
the line that proves the answer are compared, and an answer whose line is
about something else is not shown. `kosika measure` takes `--embed-model`
too, so evals can use the real comparison.

### Tools from MCP servers

An MCP server is a separate program that offers tools. A Kosika program
asks for one of them by the server's name:

```
tool search_notes {
  source mcp "notes"
  operation: "search_notes"
  access: observe
  input: SearchNotesIn
  output: SearchNotesOut
}
```

Naming a server reaches nothing. The person starting Studio says what each
name stands for:

```
kosika studio FOLDER --model MODEL --mcp notes="python examples/mcp/notes_server.py notes.json"
```

Kosika starts that command itself, never through a shell, and keeps the
server running while Studio is open. The Tools page lists every granted
server with the tools it offers, and for each tool the Kosika text to paste
into a program. An agent given such tools in its `can` list is told what
they do in the server's own words and chooses among them.

The Skills page lists the skills kept beside each agent, in its `skills`
folder: what each is for, the words that pick it, and the rules it holds
files to. Type a request there to see which skills it would pick, and
why; no model is asked. A skill can be changed or a new one written on
the page. One that Kosika cannot read is not saved, and the page says why.

## Environments: what a project may reach, in one file

A project can keep a file named `kosika.env` beside its programs. Each
`environment` in it says which models answer, which MCP servers its tools
reach, what folder, sites and shell it may touch, its budgets, and the
folder where its traces and journals are kept:

```
environment test {
  models answer from "fixtures/answers.json"
  keep "./.kosika/test"
}

environment live {
  model writer: "qwen2.5:7b" protocol ollama
  mcp notes: "python servers/notes_server.py" pinned "servers/notes.manifest.json"
  allow folder "./notes"
  budget 20 model calls, 50 tool calls, 300 seconds
  keep "./.kosika/live"
}
```

```
kosika run main.kos --env test
```

A run given an environment takes nothing else: `--model`, `--mcp-server`,
`--allow-folder`, `--journal` and the other options the file decides are
refused beside `--env`, so what a run may reach is what the file says. The
file is found beside the program or in the nearest folder above it, and
its paths are read from its own folder. Budgets only ever tighten a
workflow's own. Each run writes its trace to `keep/traces`, and keeps a
journal named by its program, workflow and input in `keep/journals`, so a
run paused for an approval is found again when run again; the journal is
let go once the run finishes. `kosika-native run --env` does the same.

## Kosika without Python

`native/` holds a second engine, written in Go, that builds to one program
file and runs Kosika on a machine with no Python: the checker, workflows,
tools, agents, real models, pinned MCP servers, traces and replay,
approvals and environments. `conformance/` holds the programs both engines
must agree on, with what each must print. See `native/README.md` for what
it has and what it still lacks.

A tool may be `access: observe` only when the server itself marks it
read-only. Any other tool must be `access: execute`, so each use waits for
your Allow once. A tool that answers in plain text needs an output record
with one Text field. `examples/mcp` has a small notes server and an agent,
`librarian.kos`, that uses it.

A granted server runs with your authority. Grant only servers you trust.

### Talking to a program

```
kosika chat main.kos --model MODEL --memory FOLDER
```

Each line you type is one ordinary run of the program's `main` workflow,
which must take one record with a `text: Text` field. The program keeps the
conversation itself, in a memory, so a chat has no powers a single run lacks.
`--trace FILE` keeps the last turn's trace for `kosika story`.
`--allow-folder FOLDER` lets the program's file tools work inside one folder.
`--allow-site SITE`, which may be repeated, lets its web tool read pages on
that site and nowhere else. A fetch is one `https` GET that sends no cookies
and no form, and returns the page's readable text with the address it came
from.
With `--approve`, a turn that reaches a tool which changes something stops,
shows the exact action and its arguments, and runs it only if you answer yes.
The answer covers that one action. Without `--approve` such a turn fails and
nothing is changed.
`my_examples/assistant` is a laptop assistant built this way.

### Sharing a program with another device

A pack is one file holding every module of a program and the program's hash.

```
kosika pack make main.kos --out helper.kpack     # where the program was written
kosika pack check helper.kpack --test            # on the other device
kosika pack unpack helper.kpack helper/
```

`check` proves the files are the ones that were packed and that they load to
the recorded program, runs the pack's own tests if asked, and lists what the
program would need: which models, and which tools, marking any that change
the world. Nothing else in the pack is run. A pack carries code only. It has
no model, tool binding, approval, or credential in it, so the receiving
device grants its own. A pack is not signed: it proves what the program is,
not who sent it, so compare the program hash with one you got from the
author.

To give each declared model its own real model, write a small file and pass
it with `--models`:

```json
{
  "cheap":   {"model": "qwen3:1.7b"},
  "careful": {"model": "qwen2.5:7b", "url": "http://localhost:11434/v1"},
  "strict":  {"command": ["gemini", "-m", "gemini-3.8-flash", "-p", ""]}
}
```

```
kosika run main.kos --input input.json --models models.json
```

A route is either a `model` name, with an optional `url` and `protocol`, or a
`command`. A declared model the file leaves out has no binding, and a step
that needs it is refused before anything runs.

Kosika writes each request to the program's standard input and reads the reply
from its output. The program runs without a shell in an empty temporary
folder, and it answers for every declared model. It cannot be used by an agent
that calls tools. `kosika agent` takes the same `--models` file, so an agent's
own model can be a command such as a Langflow flow; that agent then declares no
tools, and `kosika replay` still reproduces its answer with the program off.
If the reply wraps its JSON in one Markdown code fence, the
fence is removed; nothing else is repaired.
The Python API accepts `run(..., inputs={...}, model_bindings={"writer": provider})`.
A flow drawn in Langflow can be the command, and a project's flows can be MCP tools
an agent calls: [`examples/langflow`](examples/langflow) has both bridges, the flows,
the routes file, and the pinned manifest.

Missing bindings or invalid inputs are rejected before any workflow call. Model
steps expose no tools, reject tool-call responses, and fail on malformed, truncated,
or schema-invalid output. There are no automatic model retries in this slice.

Successful runs record inputs, model response text, validated outcomes, token usage
when provided, and an AST hash in a versioned JSONL trace. Treat trace files as
private application data. Offline replay validates event order and outcome
consistency and returns cached results without invoking tools or providers. It also
accepts complete legacy tool traces. Validation detects inconsistent records; it
does not authenticate a trace against malicious rewriting. Failed runs do not yet
produce a complete replayable failure trace. Optional durable sessions retain
checkpoint state and support explicit workflow mutation approval; see the development guide.

## Deterministic evaluations

`kosika.evaluation` runs fresh workflow cases with fixture-backed providers,
writes one replayable JSONL trace per case, and produces a machine-readable
`report.json`. Assertions are data: cases support exact output matching or
required text fragments, never executable test code.

The research assistant evaluation uses the existing local MCP document fixture:

```powershell
$env:KOSIKA_DOCUMENTS_DIR = "examples/research_documents"
python -m kosika.devtools eval examples/research_assistant.kos `
  examples/research_evaluations.json `
  --workflow answer `
  --mcp-server examples/documents_mcp_server.py `
  --mcp-manifest examples/research_documents_manifest.json `
  --mcp-source research_documents `
  --output research-evaluation
python -m kosika.cli replay research-evaluation/auditable_answer.jsonl
```

The command returns exit code `0` only when every case passes, `1` when an
assertion fails, and `2` for invalid fixture or host setup. The output directory
must be new so previous traces cannot be mistaken for a fresh evaluation.


## Local document assistant

An `ask researcher(text: question.text)` step runs a bounded agent and returns its
declared model output record. The agent declares `model: reader` and `max_steps: 4`.
This first workflow integration permits only read-only tools. The host must grant
both the model and tool bindings before execution.

Try the included sample from this repository:

```text
python -m kosika.cli check examples/document_assistant.kos
python -m examples.document_assistant approval --trace document_trace.jsonl
python -m kosika.cli replay document_trace.jsonl
```

The default model is a deterministic fixture, not AI inference. Its search tool
reads real files from `examples/documents`. The host binds the declared tool
directly in process; this demonstration does not start an MCP server. Use
`--docs-root PATH` to grant a different folder. Only direct UTF-8 `.md` and `.txt`
files are read, with limits of 32 files and 64 KiB per file; symlinks are rejected.
The adapter snapshots file contents before the agent runs and returns at most five
matching line excerpts per search. It is not an operating-system sandbox.

For an explicitly configured model, add `--model YOUR_MODEL --model-url URL`.
That provider receives the question and retrieved excerpts. No live provider is
needed for the fixture demo. Answers contain typed `text` and `sources` fields;
cited filenames must have been retrieved. This checks provenance, not whether the
answer is factually supported by those documents.

Successful agent traces include the conversation and validated tool outcomes.
Offline replay validates the transcript and returns cached results without reading
documents or calling a model. The same trace privacy, authenticity, and failed-run
limitations described above apply.

### Research assistant example

`examples/research_assistant.kos` adds a bounded, read-only document search to the
agent workflow. The host grants only the declared `search_docs` tool, and the
deterministic model fixture must cite files returned by that search.

```text
python -m kosika.cli check examples/research_assistant.kos
python -m examples.research_assistant --trace research_trace.jsonl
python -m kosika.devtools inspect research_trace.jsonl
python -m kosika.cli replay research_trace.jsonl
```

The example uses `examples/research_input.json`,
`examples/research_model_answers.json`, and the bounded documents in
`examples/research_documents`.

## Included

- Small `.kos` lexer and parser
- AST dataclasses
- Static capability checker
- Read-only mock MCP-style tool boundary
- Dependency-free local MCP stdio adapter
- Pinned MCP server/tool manifests
- Input and output JSON-schema validation at the live tool boundary
- JSONL event trace
- Exact replay from recorded tool output
- Unit and integration tests using pytest

## Install and run

From a source checkout, build and install Kosika into the active environment:

```text
python -m pip install .
kosika check examples/test_host.kos
kosika run examples/test_host.kos --trace run_trace.jsonl
kosika replay run_trace.jsonl
python -m pytest -v
```

`python -m kosika.cli` remains available for source-checkout development.

## Manifest generation and validation

Kosika can discover a live MCP stdio server and write a deterministic pinned manifest without
invoking any tool. It can also validate an existing manifest against a server without executing a
tool:

```text
python -m kosika.cli manifest generate `
  --mcp-server examples/mcp_host_server.py `
  --source local_stdio `
  --output examples/mcp_manifest.generated.json
python -m kosika.cli manifest validate examples/mcp_manifest.json `
  --mcp-server examples/mcp_host_server.py
```

`manifest generate` records the negotiated protocol, server identity, exact discovered tool set,
input/output schemas, and canonical schema digests. The generated JSON is stable for the same
server discovery response.

To review drift without invoking any MCP tool:

```text
python -m kosika.cli manifest diff examples/mcp_manifest.json `
  --mcp-server examples/mcp_host_server.py
python -m kosika.cli manifest diff examples/mcp_manifest.json `
  --mcp-server examples/mcp_host_server.py `
  --json
```

The human-readable form summarizes changed server identity, added or removed tools, and schema
changes. `--json` emits a structured diff for CI or later approval workflows. The review command
returns success even when drift is found. Use `manifest check` when drift must fail automation:

```text
python -m kosika.cli manifest check examples/mcp_manifest.json `
  --mcp-server examples/mcp_host_server.py
```

`manifest check` returns exit code `0` when the live server matches and exit code `1` when drift is
detected. It also supports `--json`.

A ready-to-copy GitHub Actions example is included at
`.github/workflows/kosika-manifest-check.yml`. It runs on pushes and pull requests that touch the
Kosika runtime, tests, examples, source files, or manifest-check workflow. The workflow uses the
local deterministic MCP fixture, installs pinned Ruff and MyPy for Python quality checks, fails
the job when the pinned manifest drifts, and runs the complete unittest suite with resource warnings treated as errors.
The workflow reports branch coverage in the GitHub Actions job summary, uploads `coverage.xml` as
the `kosika-coverage` artifact, and fails when total branch-aware coverage falls below 80%.

## Real local MCP stdio adapter

Kosika v0.1 can launch a local MCP-compatible server, perform the initialize handshake, discover
its advertised tools, validate the declared operation, call the tool, and record the transport in
the trace. Live execution requires a pinned manifest. The manifest pins the source, negotiated
protocol version, server name/version, exact advertised tool set, input schema, output schema, and
a canonical SHA-256 schema digest.

```text
python -m kosika.cli run examples/test_host_live.kos `
  --mcp-server examples/mcp_host_server.py `
  --mcp-manifest examples/mcp_manifest.json `
  --trace live_trace.jsonl
python -m kosika.cli replay live_trace.jsonl
```

Validation happens in three places:

1. Manifest loading rejects a missing or tampered schema digest.
2. Startup discovery rejects server identity, protocol, tool-set, or advertised schema drift.
3. Invocation validates arguments before sending `tools/call` and validates structured results before
   the workflow receives them.

The runtime also derives the expected output schema from the Kosika record returned by the tool and
requires that the pinned manifest agree with that record. The language does not grant shell, filesystem, credential, or network access by
itself. Hosts explicitly supply adapters and provider bindings; mutations require
matching approval through a durable session. The local test
server is protocol-compatible but deterministic. This is the first language/runtime slice, not a
production executor.

## Approval-protected agent factory

`examples/agent_factory.kos` demonstrates the first builder workflow. A typed request
goes to a declared architect model, the resulting `AgentSpec` is passed to an
`execute` source-writing tool, and the durable session pauses before the tool call.
The local MCP server only renders reviewable Kosika text; it never writes a file.

Generate and validate the pinned tool manifest:

```powershell
python -m kosika.cli manifest generate `
  --mcp-server examples/agent_factory_mcp_server.py `
  --source agent_factory `
  --output examples/agent_factory_manifest.json
python -m kosika.cli check examples/agent_factory.kos
```

Run with deterministic model and MCP fixtures. The first run prints the exact
`plan_hash` and `action_hash` and exits with approval required:

```powershell
python -m kosika.cli run examples/agent_factory.kos `
  --workflow build_agent `
  --input examples/agent_factory_input.json `
  --model-fixture examples/agent_factory_models.json `
  --mcp-server examples/agent_factory_mcp_server.py `
  --mcp-manifest examples/agent_factory_manifest.json `
  --mcp-source agent_factory `
  --journal agent_factory.sqlite `
  --binding-id agent-factory-v1 `
  --trace agent_factory.jsonl
```

Review those hashes and the proposed source, then rerun with
`--approval-record examples/agent_factory_approval.json`. The approved result is
recorded in the trace and can be replayed offline:

```powershell
python -m kosika.cli replay agent_factory.jsonl
```

The example intentionally stops at a proposed declaration. It does not silently
write generated source, grant capabilities, or execute arbitrary shell commands.

## Approval-protected mutation

`examples/approval_mutation.kos` is the staged mutation workflow. It declares one
typed `execute` tool and requires approval before the mutation. The local MCP
fixture writes one deterministic JSON result to the sink named by
`KOSIKA_MUTATION_SINK`; it accepts only a relative `.json` filename.

Generate and validate its pinned manifest:

```powershell
python -m kosika.cli manifest generate `
  --mcp-server examples/approval_mutation_mcp_server.py `
  --source approval_mutation `
  --output examples/approval_mutation_manifest.json
python -m kosika.cli manifest check `
  examples/approval_mutation_manifest.json `
  --mcp-server examples/approval_mutation_mcp_server.py
python -m kosika.cli check examples/approval_mutation.kos
```

Run once with a durable journal. The command pauses before the effect and prints
the exact plan and action hashes:

```powershell
$env:KOSIKA_MUTATION_SINK = ".tmp-approval-mutation-sink.json"
python -m kosika.cli run examples/approval_mutation.kos `
  --workflow apply_change `
  --input examples/approval_mutation_input.json `
  --mcp-server examples/approval_mutation_mcp_server.py `
  --mcp-manifest examples/approval_mutation_manifest.json `
  --mcp-source approval_mutation `
  --journal approval_mutation.sqlite `
  --binding-id approval-mutation-v1 `
  --trace approval_mutation.jsonl
```

Review the pending change, then resume with
`--approval-record examples/approval_mutation_approval.json`. The sink is written
only after the exact action is claimed. Re-running the completed journal
replays the checkpoint without invoking the mutation again:

```powershell
python -m kosika.cli run examples/approval_mutation.kos `
  --workflow apply_change `
  --input examples/approval_mutation_input.json `
  --mcp-server examples/approval_mutation_mcp_server.py `
  --mcp-manifest examples/approval_mutation_manifest.json `
  --mcp-source approval_mutation `
  --journal approval_mutation.sqlite `
  --binding-id approval-mutation-v1 `
  --approval-record examples/approval_mutation_approval.json `
  --trace approval_mutation.jsonl
python -m kosika.cli replay approval_mutation.jsonl
```

The approval record is bound to this source, input, binding identity, workflow
step, tool, and arguments. A changed action hash remains pending and cannot
invoke the host effect.


## Approved swarm runs

A `swarm` names agents, one workflow, a round cap, and `require approval`.
`kosika swarm` runs that workflow once per round. It stops when two consecutive
rounds return the same result (`fixed_point`) or at `max_rounds`. One approval
authorizes one run: it names the plan hash, which covers the swarm and the whole
checked program, and it is claimed in the ledger before any member acts.

Review the plan, then approve exactly that hash:

```powershell
python -m kosika.cli swarm examples/swarm_review.kos --swarm EditorialBoard --plan
python -m kosika.cli swarm examples/swarm_review.kos `
  --swarm EditorialBoard `
  --approved-by "your-name" `
  --plan-hash PLAN_HASH_FROM_ABOVE `
  --approval-ledger swarm-approvals.sqlite `
  --model-fixture examples/swarm_review_models.json `
  --tool-fixture examples/swarm_review_tools.json
```

The fixtures are deterministic stand-ins for the model and the tool. Use
`--mcp-server` with `--mcp-manifest` for a pinned MCP tool source instead of
`--tool-fixture`. All rounds share one execution budget. A failed round still
spends the approval, because members may already have acted, and the ledger
records the failure. Rerunning with the same ledger is refused.

This is not consensus. Members exchange no messages and share no state between
rounds, and the approver name is a label, not an authenticated identity.

## Local model provider and agent loop

The first v0.4-oriented agent seam is available as a Python API in `kosika.model` and `kosika.agent`.
It is intentionally provider-neutral and keeps model output separate from tool execution:

Agent declarations bind the model, instructions, and bounded execution directly in `.kos`:

```kosika
agent researcher {
  model: research_writer
  instructions: "Use only retrieved evidence and keep the answer concise."
  can: [search_documents]
  cannot: [shell]
  max_steps: 8
  max_tool_calls: 12
  max_model_calls: 8
  deadline_seconds: 60
}
```

Declared instructions are added as system context before host-provided system
prompts. Instructions and execution limits are included in the approval plan hash.
The declared deployment is used when `--model` is omitted. CLI `--model`,
`--max-steps`, and `--deadline-seconds` remain explicit overrides. Deadlines and
cancellation are cooperative; they do not interrupt an in-flight provider call.

```python
from kosika.agent import AgentLoop
from kosika.model import OpenAICompatibleProvider

provider = OpenAICompatibleProvider(
    "http://localhost:11434/v1",
    "your-local-model",
)
loop = AgentLoop(program, provider, execute_tool)
result = loop.run("monitor", "Check the disk and summarize the result.")
```

The loop exposes only tools declared in the selected Kosika agent, validates model arguments and tool
results against schemas, re-runs the static capability checker, limits model steps, and rejects
`execute` tools unless an approval record matches both the plan and concrete action hashes. `ScriptedModelProvider`
is included for deterministic tests and future exact-replay fixtures. The OpenAI-compatible adapter
only performs model HTTP requests; it never executes model-provided commands or tools itself.

The same loop is available from the terminal through `kosika agent`. Configure the local model without
putting credentials in source code:

```powershell
$env:KOSIKA_MODEL_URL = "http://localhost:11434/v1"
$env:KOSIKA_MODEL = "your-local-model"
$env:KOSIKA_MODEL_API_KEY = "optional-local-key"
python -m kosika.cli agent examples/test_host.kos `
  --agent monitor `
  --task "Check the disk and summarize the result."
```

For an agent whose declared model has typed input and output records, use
structured mode:

```json
{"prompt": "Summarize the retrieved evidence"}
```

```powershell
python -m kosika.cli agent research.kos `
  --agent researcher `
  --input request.json
```

The input JSON must match the model input record. The final model response must
be a JSON object matching the model output record; otherwise the run fails closed.
The Python API uses `loop.run("researcher", input_value={"prompt": "..."})`.
The repository includes a complete typed example in
`examples/research_agent.kos`, including a read-only `search_notes` tool,
bounded model settings, and structured `ResearchAnswer` output. Validate its
declarations with:

```powershell
python -m kosika.cli check examples/research_agent.kos
```
Run the complete typed agent offline with deterministic model and tool fixtures:

```powershell
python -m kosika.cli agent examples/research_agent.kos `
  --agent researcher `
  --input examples/research_agent_input.json `
  --model-fixture examples/research_agent_models.json `
  --tool-fixture examples/research_agent_tools.json
```

Model fixtures map the declared deployment to response objects. Tool fixtures map
the declared tool name or operation to one output record, or to a sequence of
output records for repeated calls.



Completed direct-agent runs can be recorded and replayed offline:

```powershell
python -m kosika.cli agent research.kos `
  --agent researcher `
  --input request.json `
  --trace researcher.jsonl
python -m kosika.cli replay researcher.jsonl
```

The trace records model requests/responses, tool calls/results, schemas,
configuration, structured input/output, and the plan hash. Replay validates the
recorded interaction without invoking the model or tools.
Direct agents can also use a durable SQLite journal:

```powershell
python -m kosika.cli agent examples/research_agent.kos `
  --agent researcher `
  --input examples/research_agent_input.json `
  --journal research-agent.sqlite `
  --binding-id local-research-v1 `
  --trace research-agent.jsonl

python -m kosika.cli agent examples/research_agent.kos `
  --agent researcher `
  --input examples/research_agent_input.json `
  --journal research-agent.sqlite `
  --binding-id local-research-v1 `
  --resume
```

The binding identity covers the host model, tools, and policy configuration.
Completed runs return from the journal without another model call. An
interrupted in-flight model or tool call is marked uncertain and is never
automatically repeated. `deadline_seconds` applies to each process invocation;
resuming a journal starts a new cooperative deadline window.
Inspect a journal without exposing prompts, tool arguments, or model output:

```powershell
python -m kosika.cli journal research-agent.sqlite
python -m kosika.cli journal research-agent.sqlite --format json
```



### Skill catalogs for `kosika agent`

This is the older form of skills, used only by the `kosika agent` command.
For agents in workflows and in Studio, see "Skills an agent picks before it
begins" above.

Kosika can select multiple relevant local skills when a task does not name them
explicitly. Store each skill in a directory containing `skill.json` and
`SKILL.md`, then inspect the deterministic selection:

```powershell
python -m kosika.cli skills `
  --skill-dir .kosika/skills `
  --task "Review this Rust workflow for capability security" `
  --format text
```

Use the same directory with an agent run:

```powershell
python -m kosika.cli agent examples/test_host.kos `
  --agent monitor `
  --task "Review this workflow for capability security" `
  --skill-dir .kosika/skills `
  --skill-policy hybrid
```

Selected skills are advisory context only. They are included in the plan hash
for reproducibility but cannot grant tools, capabilities, or approvals.


The command prints the deterministic plan hash before execution. For a live MCP source, a pinned
manifest and server are required:

```powershell
python -m kosika.cli agent examples/test_host_live.kos `
  --agent monitor `
  --task "Check the disk and summarize the result." `
  --mcp-server examples/mcp_host_server.py `
  --mcp-manifest examples/mcp_manifest.json
```

The CLI does not accept an API key argument, so secrets do not enter shell history through this
command. Observe-only tools can run without approval. Execute-capability tools require the matching
plan hash through `--approval-plan-hash`, the exact tool-call hash through
`--approval-action-hash`, and the approval actor with `--approved-by`. An unapproved
execute call prints the tool, arguments, and both hashes. Review those arguments
before rerunning; a changed model call will produce a different action hash and be refused.
Pass `--approval-ledger PATH` for execute calls. Kosika commits the claim to
that SQLite file before invoking the tool, then records its outcome hash or failure type.
A completed, failed, or interrupted claim cannot be reused with the same ledger.
Keep the ledger at a stable path; removing or replacing it removes this local replay guard.
The actor name is a label, not authenticated identity.
One record authorizes one execute call per run.

### Local VS Code commands

The repository includes a local extension backed by a long-running Kosika LSP
server. It provides syntax highlighting, snippets, debounced live diagnostics,
live workspace indexing for `.kos` files, document symbols, hover,
go-to-definition, references, completion, and commands for checking the active
file, explaining it, rendering Kosika or unified Mermaid mindmaps, and
inspecting selected skills:

```powershell
code --extensionDevelopmentPath=editors/vscode
```

Set `kosika.pythonPath` if the extension should use a specific Python
interpreter. Set `kosika.skillDirectories` to one or more local skill roots.
Set `kosika.modelName` to enable a local OpenAI-compatible model for the Explain
command; leave it empty for deterministic static explanations.

Diagnostics use the versioned `kosika.diagnostics/v1` JSON contract. Run
`python -m kosika.cli check PATH --format json` for a module-aware report. Every
diagnostic has a stable code and originating path; parser spans cover the
offending token, while checker spans identify the unique owning declaration or
step. If no truthful owner is available, JSON reports `range: null` and
`data.anchor_missing: true` instead of inventing a source position. Imported
file failures are published against the imported file URI by the LSP.

### Machine-readable development interface

`kosika.development/v1` exposes checked project inspection, K04 diagnostics,
bounded execution, validated trace inspection, and deterministic maps without a
second parser or runtime. Python hosts can call `inspect_project`,
`check_project`, `run_project`, `inspect_trace`, and `map_project` from
`kosika.development`.

The CLI accepts one strict JSON request from a file or `-` for standard input:

```json
{
  "schema": "kosika.development/v1",
  "operation": "inspect",
  "source": "examples/composed/main.kos",
  "root": "examples/composed"
}
```

```powershell
python -m kosika.cli development request.json
```

Operations are `inspect`, `check`, `run`, `trace`, and `map`. Every response
uses the same schema and includes `operation`, `ok`, `project`, `result`,
`diagnostics`, and `error`. JSON requests cannot grant model or tool authority;
an embedding host must inject explicit bindings into the Python API. Therefore
CLI `run` requests support workflows requiring no external binding and otherwise
fail closed.

### Cross-language components

`kosika.component/v1` binds one declared Kosika tool to one pinned local process
artifact. The contract versions the tool input/output schemas, required access,
effects and capabilities, declared errors, termination deadline, operation, and
artifact SHA-256. The host must grant every required effect and capability
explicitly before the process starts.

The included JavaScript example uses the language-neutral `json-stdio/v1`
request/response protocol:

```text
python -m kosika.cli component check examples/components/uppercase.component.json --json
python -m kosika.cli run examples/component_uppercase.kos \
  --input examples/component_uppercase_input.json \
  --component-manifest examples/components/uppercase.component.json \
  --component-runtime node \
  --allow-component-effect cpu:compute \
  --allow-component-capability text:transform
```

The host chooses the runtime executable; Kosika appends and re-hashes the pinned
artifact before every call, invokes no shell, validates both JSON boundaries,
accepts only declared component errors, bounds messages to 1 MiB, and kills the
process at the contract deadline. This is a process contract, not an OS sandbox:
the digest identifies bytes but does not prove their behavior.

## Typed tool inputs

Tools may declare an input record. The record becomes the model-facing JSON schema and is also used to
check literal workflow arguments before any tool runs:

```kosika
record DiskRequest {
  mount: Text
}

tool check_disk {
  source mcp "local_mock"
  input: DiskRequest
  operation: "get_disk_info"
  access: observe
  output: DiskStatus
}

workflow main() -> DiskStatus {
  step inspect {
    let status: DiskStatus = execute monitor.check_disk(mount: "/")
    return status
  }
  step recheck {
    let final: DiskStatus = execute monitor.check_disk(mount: status.mount)
    return final
  }
}
```

Each step binds its validated record result with `let`. Later steps may reference that record or one
of its fields, and the checker resolves references in declaration order before runtime execution.
Arguments currently support named string, number, boolean, `null`, variable, and field-access
expressions. Existing tools without an `input` declaration remain zero-argument tools for backward
compatibility. See `examples/typed_input.kos` for a complete runnable example.

Structured record fields may contain nested records or arrays. Workflow arguments can construct these
values directly and later steps can access fields or index arrays:

```kosika
record Metadata {
  name: Text
}

record Request {
  tags: Text[]
  metadata: Metadata
}

// Inside a workflow call:
inspect(tags: ["disk", "local"], metadata: {name: "root-disk"})
// Later, after binding the result:
inspect(tags: status.tags, metadata: status.metadata)
```

Structured expressions support arrays, nested record literals, field access,
integer indexing, arithmetic, Boolean operators, conditionals, pattern matching,
assignment, and bounded loops. See `examples/structured_values.kos`,
`examples/local_bindings.kos`, and `examples/recursion_match.kos`.

## Code mindmaps

Kosika can explain a checked program without asking a model to infer its structure. The `mindmap`
command derives deterministic module, record, field, tool, model, agent, workflow, step, and call-graph
nodes from the parser/AST. It can emit structured JSON for programs and tooling, Mermaid text for a
diagram renderer, or a compact terminal summary:

```text
python -m kosika.cli mindmap examples/composed/main.kos --format json --output code-map.json
python -m kosika.cli mindmap examples/composed/main.kos --format mermaid --output code-map.mmd
python -m kosika.cli mindmap examples/composed/main.kos --format text
```

Imported modules retain their relative paths, content hashes, declarations, and direct import edges.
The map is read-only and is generated only after static checking succeeds. It is an explanation of
Kosika's checked structure, not an execution trace or a model-generated claim about intent.

## Code in other languages

The same reader works on Python, JavaScript, TypeScript, and Rust, so a folder that mixes them can be
explained without a model and without running anything. Three commands answer three questions:

| Command | Answers |
| --- | --- |
| `kosika analyze FILE` | What does this one file declare, and what does it reach? |
| `kosika sourcemap DIR` | What is in this folder in every supported language, and what connects it? |
| `kosika project DIR` | What is this project made of, including the imports between its Python files? |

```text
python -m kosika.cli analyze kosika/source_map.py
python -m kosika.cli sourcemap kosika --format text
python -m kosika.cli sourcemap kosika_lsp --format mermaid > code-map.mmd
python -m kosika.cli sourcemap kosika_lsp --output code-map.json
python -m kosika.cli project kosika_lsp --output project.json
```

`--output` always writes the JSON report; `--format text` and `--format mermaid` are what appear on
the screen or in a redirect.

`sourcemap --format text` leads with what a reader needs to know first. For a folder holding one
JavaScript file and one Python file:

```text
Source map for smdemo
Files: 2  declarations: 6  connections: 0
Languages: javascript (1), python (1)

load.js
  function  load (line 1)

store.py
  class     Store (line 3)
  function  put (line 4)
  function  load (line 7)

Structure only: this map reports what is declared, not what runs.
```

All three are syntax-backed and bounded: nothing is imported, executed, or transpiled. A call is
linked only when a single declaration shares its name; imports link only when they resolve to one
discovered file, and everything else is reported as ambiguous or unresolved rather than guessed.
`sourcemap` stops at 2,000 files, 20,000 nodes, and 32 MiB of source, and `project` analyzes at most
40 files with import edges for Python only. A map describes structure, not behavior or intent.

Studio's **Project** page runs the same reading over the folder it was opened in and draws it: a
box for each file, placed in the column the reading reaches it in, an arrow for every import and a
brighter one for every call between two files, with the declarations listed beside the drawing and
the three commands to go further. Clicking a box opens that file beside the map, read-only, without
leaving the page: the text is exactly what the reading read, and nothing there can be changed. A
folder with more files than fit is counted, not crowded: the boxes are chosen by how much they
connect, and the rest are reported as not drawn.

## Networks

Kosika also includes a declarative network directory for agent workflows,
model boundaries, MCP nodes, devices, memory graphs, and virtual spaces.
Network manifests are validated before use and can be indexed without
connecting to external systems:

```text
python -m kosika.cli network validate network/example-network.json
python -m kosika.cli network show network/example-network.json --format mermaid
python -m kosika.cli network index network --recursive --format text
```
