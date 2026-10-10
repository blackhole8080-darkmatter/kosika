# Kosika blueprint: from working prototype to the vision

Written 10 October 2026, after inspecting the repository at commit
`47f5ae7` on `master`. Labels used throughout:

- **Fact**: seen in the repository or measured on this laptop.
- **Assumption**: taken as true without checking.
- **Recommendation**: what I advise.
- **Hypothesis**: believed but unproven.

One correction to the brief: the suite is 1,617 tests at the last commit,
not 1,612.

## 1. Executive view

**One sentence.** Kosika is a language for AI programs in which models,
tools, permissions, steps and their inputs and outputs are declared, checked
before running, and recorded while running.

**The owner's sentence for the whole aim.** Kosika is an AI software
development language and platform, starting with agents and growing toward
accessible, shareable, networked AI applications and systems. (The sentence
above says what is built; this one says where it is going.)

**Vision.** A person at any level can describe an AI system in Kosika,
see what it will touch before it runs, test it without a model, watch and
replay what it did, and get a map of it that a non-programmer can read. A
team that works in another language can still use Kosika as the place where
the AI part is declared and controlled.

**Users and their problems.**

| User | Problem Kosika should solve |
|---|---|
| Beginner | "I want an agent that does X and I do not know what a framework is." Needs starters, plain errors, and a picture of what the agent does. |
| Intermediate developer | "My agent works but I cannot tell why it answered that, and I am afraid to give it file access." Needs permissions, checked answers, traces, tests. |
| Advanced developer or architect | "I have several agents, several models and a budget." Needs routing, budgets, evals, maps of the whole system, and a way to connect existing code in other languages. |
| Non-technical reviewer | "What does this thing do and what can it change?" Needs a truthful map and a permissions summary. |

**What it is now (fact).** A working prototype with one user, on one
laptop, on local models, run by a Python runtime. It is broad: about 49,700
lines of Python in 101 modules and 38 commands.

**What it is not now (fact).** Not installed or used by anyone else, not
tried on hosted models, not shown to generalize beyond one compared agent,
and not fast or slow in any measured sense relative to Python.

**What the long-term product should promise.** Declared structure,
enforced permissions inside its own runtime, validated shapes, replayable
records, and maps that say where each claim comes from.

**What it should never promise.** Correct model answers, perfect
understanding of a project, safety of code it does not run, or
understanding of arbitrary source in any language.

**The main finding of the inspection.** Kosika's risk is not missing
features. Most of the vision already has a first version in the repository,
including maps and cross-language analysis. The risk is breadth without
users: several overlapping surfaces, a contract that has fallen behind the
code in places, and no evidence yet that a second person can use it.
**Recommendation:** narrow, connect what exists, and put it in front of
people before building more.

## 2. Current state and evidence

**What I inspected.** `KOSIKA.md`, `KOSIKA_PLAN.md`, `KOSIKA_VISION.md`,
`README.md`; the module list and the opening description of each larger
module in `kosika/`; the command list (`kosika --help`); `parser.py`,
`checker.py`, `runtime.py`, `sugar.py`, `system_tools.py`, `testing.py`,
`app.py` and `app_assets/index.html` in detail (worked on directly);
`model.py` provider classes; the test file list (132 files); and I ran
`kosika mindmap`, `kosika audience-map` and `kosika sourcemap` on real
inputs. I did **not** read every module line by line; where I rely only on
a module's own description I say so.

### Implemented (fact)

| Area | Evidence |
|---|---|
| Language, checker, runtime, trace, replay | `parser.py`, `checker.py`, `runtime.py`, `trace_replay.py`; 1,617 tests |
| Model steps, `verify`, `retry`, `vote`, `escalate`, `otherwise`, budgets, `parallel` | `tests/test_verify_and_measure.py`, `test_escalate.py`, `test_vote.py`, `test_budget.py`, `test_parallel.py` |
| Ask versus grant; approvals | `system_tools.py`, `tests/test_system_tools.py`, `test_approval_ledger.py`, `test_chat_approval.py` |
| Tests with scripted answers; evals; `measure` | `testing.py`, the seven agents' own tests |
| Studio | `app.py`, `app_assets/index.html`, `tests/test_studio.py` |
| MCP tools in, and Kosika as an MCP server out | `mcp_tools.py`, `mcp_server.py` |
| Search by meaning | `embeddings.py`, `tests/test_find_by_meaning.py` |

### Partially implemented (fact, with the limit stated)

| Area | What exists | What is missing |
|---|---|---|
| Maps | `mindmap.py` (from the checked program, no model), `audience_maps.py` (five audiences; every entry tagged checked, declared, observed or human), `explain.py` | Not in Studio: `app.py` and the page contain no reference to either. The outsider view of the researcher shows "16 of 91 entries" and lists seven lines reading "result: workflow result", which tells an outsider nothing. Purpose appears only if passed by hand. |
| Other languages, reading | `python_adapter.py`, `javascript_adapter.py`, `rust_adapter.py`, `source_map.py`: read-only structure of Python, JavaScript, TypeScript and Rust, never executed. Run on `benchmarks/python_comparison` it reported files, functions, and "filesystem" and "network" capabilities seen | By its own description the JavaScript one is "a bounded scanner, not a full parser". Not joined to the Kosika map of the same project. |
| Other languages, writing | `sdk.py`: build a program from Python; it renders ordinary `.kos` and uses the same checker | Python only. |
| Other languages, calling | `components.py`: a contract for a tool implemented as an outside program, with a digest and a bounded process | I did not verify how far it is exercised beyond its tests. |
| Machine interface | `development.py`: "versioned machine interface", `kosika development` takes one JSON request | One request per process; no long-running server for clients. |
| Multi-agent | `swarm.py`, boards, `tests/test_swarm_run.py` | Its own description: "no message protocol, no scheduling policy, and no consensus algorithm". |
| Model providers | One class, `OpenAICompatibleProvider`, with an Ollama mode and an API-key header | Never run against a hosted service here. |
| Editor support | `lsp.py`, `editor_features.py`, `editors/vscode/` | Not evaluated in this inspection. |
| Moving a program between devices | `pack.py` | Device-to-device running is not built. |

### Missing (fact)

- Any user other than the author. No install path was tested on a second
  computer (`desktop/` has build scripts; not evaluated).
- A benchmark larger than a handful of questions per agent.
- A versioned, documented program format that another language could read
  (the pieces exist: `program_payload` and the program hash in `ast.py`).
- A plan-then-act agent, long-running agents, hosted deployment.

### Risks and inconsistencies I can support

1. **Three user interfaces.** Studio (`app.py`), the older Workbench
   (`workbench.py`, `WORKBENCH.md`), and the city views (`city.py`,
   `city_live.py`, `cityscape.py`, `metropolis.py`, `three_world.py`,
   `design.py`, about 6,000 lines together). **Recommendation:** Studio is the product; decide
   which of the others to retire.
2. **Four map-like systems** (`mindmap`, `audience_maps`,
   `workbench_mindmap`, `source_map`) plus the city views, none shown in
   Studio.
3. **The contract lags the code.** Seen today in `KOSIKA.md`: the built-in
   methods section says records have no methods, while a later section
   defines record methods; the system-tools section says every system tool
   must be `access: execute`, while `system_tools.py` allows read-only file
   tools to be `observe`; the tools table omitted `peek`, `create` and
   `search`.
4. **`cli.py` is 4,027 lines and holds 38 commands.** Hard to change
   safely, and a beginner sees all 38.
5. **A check that proves less than it seems.** The researcher's quote
   check proves the quote is real, not that it answers the question;
   `qwen2.5:1.5b` passed it with an irrelevant quote in 3 of 3 runs. A
   second check is built and not yet committed or measured.
6. **Permissions are enforced by the runtime, not the operating system.**
   File tools refuse paths outside the folder; a granted shell tool starts
   a real program with the user's rights after approval. That is a
   reviewed action, not a sandbox.
7. **`intelligence.py`** describes "adaptive execution, error prediction
   ... and learning". I did not evaluate it; nothing in the plan's measured
   results depends on it. **Recommendation:** measure it or move it out of
   the core.
8. **Example drift.** `my_examples/agents` differs from `examples/agents`.

## 3. Proposed architecture

### The shape

```
   .kos text        SDKs (Python today)        Studio editor
        \                 |                         /
         v                v                        v
   +-----------------------------------------------------+
   |  CORE (stable, language-neutral in its formats)     |
   |  parse -> lower -> check -> PROGRAM FORMAT (JSON)   |
   |  runtime -> TRACE FORMAT (JSON lines) -> replay     |
   |  diagnostics codes, permission model, budgets       |
   +-----------------------------------------------------+
        |              |               |             |
   providers        tools          surfaces        maps
   (models,      (files, web,    (CLI, Studio,   (built from
   embeddings)   shell, MCP,     server, LSP)    program + trace
                 components)                     + notes)
```

**Stable core.** The language contract, the checked program as data, the
trace events, the diagnostic codes, and the rules for permissions,
approvals and budgets. Changes here are versioned and rare.

**Replaceable around it.** Model and embedding providers, tool kinds,
Studio, the editor support, the maps, the examples.

**Language-neutral versus Python.**

| Part | Should be language-neutral | Can stay Python for now |
|---|---|---|
| Program format, trace format, diagnostics, approval records | Yes: documented JSON with a version number | |
| Parser, checker, runtime | | Yes. One implementation, held to the contract by the test suite |
| Tool protocol | Yes: MCP for outside tools | The built-in file, web and shell tools |
| Studio, CLI | | Yes |

### Each part

- **Language and experience.** Keep one language. Three levels of use, not
  three languages: starters and thinking words for beginners; records,
  functions and tests for intermediates; routing, budgets, parallel and
  swarms for advanced users. **Recommendation:** the examples teach the
  style, so keep them short (today's finding).
- **Parser, checker, diagnostics.** Keep the rule that every error has a
  code and a plain sentence. Add a test that fails when `KOSIKA.md` and the
  checker disagree on a listed rule (a start exists:
  `tests/test_language_contract.py`).
- **Runtime and providers.** One provider interface with three things a
  provider must declare: structured output, tool calling, streaming. Add a
  conformance suite that any provider must pass, run first against the
  scripted provider and Ollama.
- **Tools, permissions, budgets, boundaries.** Keep ask versus grant. Say
  honestly in the docs which boundary is enforced by Kosika and which by
  nothing (item 6 above). **Recommendation:** an optional stricter mode for
  shell tools later; not now.
- **Tests, evals, traces, debugging.** Already the strongest part. Add
  saved eval results over time, and stepping through a past run in Studio.
- **Studio.** The single front door: see, run, approve, edit, map.
- **Multi-agent.** Leave as it is until single agents have users. When it
  comes, it belongs in the core as declared roles and message types, with
  the same budgets and approvals.
- **Maps.** One map model, several views (below).

### Cross-language support: four strategies

| Strategy | What it means | Benefits | Costs and risks | Enables |
|---|---|---|---|---|
| A. Local server with thin clients | `kosika serve` runs on the machine; a small client in TypeScript, Go or anything else sends "run this workflow with these inputs", receives events, and answers approval requests | One runtime, so one set of guarantees. Clients are a few hundred lines. Builds on `development.py` and Studio's existing endpoints | A process to start and keep alive; a protocol to version | Any language can use a Kosika program as its AI layer |
| B. Tools written in any language | Outside code is offered to Kosika as tools, through MCP or the existing component contract | Already half built (`mcp_tools.py`, `components.py`). MCP is a standard others already use | The outside tool runs with its own rights; Kosika controls the call, not what the tool does inside | Existing code in any language becomes usable by agents |
| C. Builders per language | A library in each language that constructs a program and emits `.kos` (as `sdk.py` does for Python) | Feels native; same checker | One library to write and maintain per language; easy for them to drift | Programs generated by other systems |
| D. A second runtime, or compiling the runtime to run inside other languages | Reimplement in Rust, or ship as WebAssembly | No Python needed; possibly faster | Very large; two implementations must agree exactly on every rule; nothing today says speed is the problem | Embedding without a server |
| (Rejected) Translating arbitrary code into Kosika | | | No tool can reliably recover what a general program means or what it is allowed to do | |

**Recommendation, staged.** B now (it exists; document MCP as the way to
connect outside code). Then A, with a TypeScript client first, because it
gives every language access through one protocol. C only where a real user
asks. D only after the program and trace formats are documented and a
conformance suite exists, and only if measurement shows a need. The
existing read-only adapters for Python, JavaScript and Rust stay what they
are: map inputs that report structure they can see, never behaviour.

### Maps

**One model.** Nodes and edges, each carrying *where the claim comes from*.
`audience_maps.py` already has four sources; I recommend six:

| Source | Meaning | Shown as |
|---|---|---|
| checked | Read from the checked program | Solid |
| declared | Stated by a component contract or tool description | Solid, labelled "says" |
| observed | Seen in a recorded run | Solid, with the run it came from |
| human | A purpose note a person wrote | Marked with the author |
| inferred | Suggested by code analysis of other languages, or by a model if the user asks | Dashed, never shown by default as fact |
| unknown | Kosika cannot see inside (an MCP server, a shell program) | A grey box that says "not visible to Kosika" |

**What the map holds.** Components (workflows, agents, models, tools,
memories, records) and their relationships; the order of steps and what
each passes to the next; every model call with its checks and fallbacks;
every tool with its access and what the runner must grant; where approval
is needed; inputs and outputs; purpose notes; assumptions; and for each
item its source line.

**Three views of the same map.**

| View | Question it answers | Content |
|---|---|---|
| Beginner | "What does this do, and what can it touch?" | Purpose in one sentence, what goes in and comes out, "it reads", "it may change (asks you first)", "it asks these models", five to ten boxes at most |
| Developer | "How does a request flow?" | Steps in order with types, branches, checks, fallbacks, called functions, links to lines |
| System designer | "What is the whole system allowed and costing?" | Permission table, budgets, model routing, which programs share agents or memories, outside tools |

**Honesty rules.** A coverage line ("23 items from the checked program, 0
from runs, 0 written by a person") stays on every map, as it already does.
A map never fills a gap with a guess; it shows the gap. A person can
correct or add a purpose note, and the note is stored beside the program
and marked as human.

## 4. Principles and guarantees

| Principle | In practice | How to test it | Who enforces it |
|---|---|---|---|
| Explicit structure over hidden behaviour | Every model, tool, memory and step is declared | The checker rejects a call to anything undeclared (exists) | Language |
| Ask versus grant | Declaring a tool gives nothing; the runner grants folder, sites, shell, embeddings | Tests that an ungranted tool is unreachable (exist) | Runtime |
| Least privilege, reviewable actions | Read-only tools need the grant only; changes need a yes each time | Approval tests (exist). **Not enforced:** what an approved program then does | Runtime for the call; the operating system and the human for the rest |
| Validate model outputs before use | Shape always; meaning only by a `verify` the author writes | Shape tests (exist). A `verify` is as good as its author: see risk 5 | Language for shape; author for meaning |
| Observable, replayable runs | Every run writes a trace that replays without the model | Replay tests (exist) | Runtime |
| Useful failure messages | Each error has a code, a sentence, a place | `diagnostics-coverage` command and its test (exist); add "a beginner fixed it unaided" in user tests | Language; quality needs humans |
| Progressive learning | Starters first, advanced words optional | Task-based user tests (section 7) | Product; unproven |
| Honest claims | Each claim in the docs names its measurement and its "not claimed" | Review rule in `KOSIKA_PLAN.md` (in use) | Process |

Guarantees Kosika cannot give, and should say so: that a model is right;
that an outside tool behaves; that a hosted provider keeps data private;
that a map of non-Kosika code is complete.

## 5. Roadmap

Order changed from the brief in one place: maps (Stage 2 here) come before
provider work, because the map code exists, costs little to surface, and is
what a first user will look at.

| Stage | Goal | Deliverables | Deferred | Depends on | Evaluation and exit | Risk and mitigation |
|---|---|---|---|---|---|---|
| 0. Validation | Learn whether someone else can use it | Install on a second computer; two or three people do one task while observed; a list of where they stopped | All new features | Nothing | Exit: a written list of the first ten obstacles, and whether each person finished | Finding it is hard to use. That is the point; better now |
| 1. Stabilize the core | One front door, a true contract | Contract brought level with the code; a test for each fixed mismatch; Studio named the product, Workbench and city views kept, archived or removed by decision; commands grouped into everyday and advanced; saved eval history | New syntax | Stage 0 list | Exit: no known contract mismatch; a new user sees under ten commands | Deleting something valued: archive first |
| 2. Grounded maps in Studio | Anyone can see what a program is and may touch | Map tab with the three views; source labels; purpose notes saved beside the program; unknown boxes for outside tools | Maps of other languages; model-written summaries | Stage 1 choice of Studio | Exit: test of section 7, "map questions"; baseline measured first | Maps that look authoritative but are thin: keep the coverage line visible |
| 3. Beginner experience | A first agent in minutes | One-command install; a guided first run in Studio; tutorial rewritten around three tasks; errors reviewed against Stage 0 findings | Hosted services | Stages 0 and 1 | Measure time to first working agent before setting any target | Writing for an imagined beginner: use the observed ones |
| 4. Providers and portability | Not tied to one laptop or one model server | Provider interface with declared abilities; conformance suite; one hosted provider when credit exists; documented, versioned program and trace formats | A second runtime | Stage 1 | Exit: the same evals run unchanged on two providers; formats documented with a version | Provider differences in structured output: the conformance suite is the mitigation |
| 5. Other languages | Kosika usable from, and with, other languages | MCP documented as the tool route; `kosika serve`; a TypeScript client; cross-language source maps joined into the Stage 2 map as "inferred" | Per-language builders; second runtime | Stage 4 formats | Exit: a TypeScript program runs a workflow, shows events, answers an approval, in a test | Protocol churn: version it from the first day |
| 6. Multi-agent and wider use | Systems of agents with the same guarantees | Declared roles and message types; plan-then-act; long-running agents with checkpoints (`durable.py` exists); budgets across a swarm | Consensus schemes | Stages 1 to 4 | Exit: a two-agent example with tests, evals and a map; measured against the single-agent version | Complexity outrunning users: do not start before Stage 0 to 3 exits |

## 6. Recommended next slice

**A Map tab in Studio, beginner view first.**

- **Problem.** A person opening an agent in Studio cannot see, in one
  picture, what it does and what it may touch. The code for a grounded map
  exists and is invisible.
- **Behaviour.** In Studio, each agent gets a Map tab. It shows: the
  purpose (from a note, or "No purpose written yet" with a field to add
  one); what it is given and gives back; "Reads", "May change (asks you
  first)", "Asks these models", "Uses outside tools (not visible to
  Kosika)"; and the workflow's steps as at most ten boxes. Every item links
  to its line in the Code tab and carries its source label. A line at the
  bottom gives the coverage counts.
- **Boundaries.** Kosika programs only. No model is asked. Read-only except
  for saving the purpose note beside the program.
- **Likely files (fact: these exist).** `kosika/audience_maps.py` (the
  outsider view; remove the "workflow result" noise), `kosika/app.py` (an
  `/api/map` endpoint, and one to save the note), `kosika/app_assets/
  index.html` (the tab), `tests/test_audience_maps.py`,
  `tests/test_studio.py`.
- **Acceptance tests.**
  1. For the researcher, the map lists exactly its declared tools, each
     under "Reads", and nothing under "May change".
  2. For the workbench agent, the shell tool appears under "May change
     (asks you first)".
  3. A program using an MCP tool shows it under "outside tools, not
     visible".
  4. With no note, the purpose reads "No purpose written yet"; after
     saving one it shows with the label "written by a person" and survives
     a restart.
  5. Every item has a line number that exists in the source.
  6. The page still makes no outside request and uses no `innerHTML`
     (existing page test).
- **Manual test.** Open Studio on `my_examples`; open each of the seven
  agents' Map tab; for each, check the picture against the code by eye;
  add a note; restart; show it to one person who has not seen Kosika and
  ask the five questions of section 7.
- **Done when.** The six tests pass, the full suite passes, the tab is in
  the README with a screenshot, and one outside person has been asked the
  questions and their answers written down.
- **Not solved.** Developer and designer views, maps of other languages,
  maps across several programs, anything a model writes.

## 7. Validation plan

| Question | Method | Measures | Keeps model quality separate by |
|---|---|---|---|
| Easier to learn? | Three to five people, at least one beginner and one experienced, each build one small agent from the tutorial while observed | Finished or not; time to first run; number and kind of stops; errors they could not fix alone | Using scripted answers during the task, so no model is involved |
| More useful than a general language? | Three more agents written twice, Kosika and plain Python, by the same person in alternating order; ideally one written by someone else | Lines and characters; time to write; time to make one specified change; guarantees each has; evals passed | Same model, same prompts, same questions for both |
| More controllable and inspectable? | Seeded-fault test: give a person a run with a wrong answer and ask them to find the step at fault, in Studio and in the Python version's logs | Found or not; time | Fault is planted in code, not the model |
| Portable across providers? | The provider conformance suite and each agent's evals on every provider | Which pass unchanged | Reporting per provider |
| Portable across languages? | The TypeScript client test of Stage 5; one outside tool in another language over MCP | Works end to end or not | Not applicable |
| Do maps explain? | Five fixed questions about an unseen agent ("what can it change?", "which model answers?", "what happens if the search fails?", ...) answered from the map alone, then from the code alone, by different people | Correct answers; time; and what the map got wrong or left out | No model in the map |

Rules for all of it: write the procedure before running it; publish every
result including failures; several runs per model; never report line count
alone; measure a baseline before naming a target.

## 8. Open decisions

| Decision | Why it matters | Cheap way to settle |
|---|---|---|
| Which interface is the product: Studio, Workbench, or the city views? | Three surfaces triple the upkeep and confuse a newcomer | Show all three to two people; keep what they use |
| Is the audience beginners first, or developers first? | Decides tutorial, defaults, and what "simple" means | Stage 0 observation; pick the group that got furthest |
| Stay local, or support hosted models soon? | Local small models fail tasks larger ones pass (5 of 12 to 12 of 12 measured), which can look like a Kosika fault | One evening with a small paid credit on one hosted model, running existing evals |
| Is the program format a public promise? | Other languages and tools will depend on it | Write it down as version 0 and try to load it from a twenty-line TypeScript script |
| How much of `KOSIKA_VISION.md` (tensors, personality, memory bubbles) is still wanted? | It pulls design toward a much larger language | Mark each item keep, later or drop in one sitting |
| What happens to `intelligence.py`, the city views, `_trash/`, `archive/`? | Unmeasured code in the core is cost without known benefit | Measure or move; ask before deleting |
| Should a second check for relevance become a language feature? | The quote check's gap will recur in every evidence-based agent | Measure the uncommitted second check on the small models first |
| One guide for the author agent, or one per task kind? | The longer guide scored 12 of 24 against 8 of 12 before | The comparison run now in progress |

## First three actions

1. Put Kosika on a second computer and watch one other person try the
   tutorial's first agent; write down every place they stop.
2. Decide that Studio is the product, and say which of Workbench and the
   city views are kept, archived or removed.
3. Build the Map tab in Studio as specified in section 6.
