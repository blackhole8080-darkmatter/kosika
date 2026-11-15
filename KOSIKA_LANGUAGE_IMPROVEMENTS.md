# Kosika language improvements: making AI software and its components expressible

Written 21 October 2026. Scope: the language surface in `kosika-language/0.1.0`
(`kosika/parser.py`, `kosika/checker.py`, `kosika/runtime.py`, `kosika/agent.py`,
`kosika/model.py`), the 246 `.kos` files in the tree, and the 15 agents in
`examples/agents/`. This is a design argument, not a claim that anything below
is implemented.

The question it answers: **what is missing from Kosika such that an AI system
and its components can be designed in Kosika, instead of being assembled from
Python around a Kosika workflow?**

## Executive summary

Kosika already has the hard, differentiating half of an AI language: typed
boundaries, declared capabilities, approvals, budgets, traces and replay. What
it lacks is the half that makes a *system* out of those pieces: a way to name
alternatives, reuse a component, pass one around, iterate over work, and state
what it costs.

The five moves that unlock the most, in order:

1. **Tagged unions** (`choice`) with exhaustive match and narrowing. AI
   components are alternatives — succeeded, refused, failed, needs-approval.
   Today the only honest encoding is a record of `Bool` flags the checker
   cannot narrow.
2. **Generics** on records, functions and workflows, bounded by `shape`.
   Without them no collection, pipeline, result or retriever can be written
   once and reused. They are the most expensive item here across two engines,
   so the schedule ships a small closed set of built-in generic-shaped types
   first and user-defined generics last.
3. **An effectful composable unit** — `task` — between pure `fn` and top-level
   `workflow`: it may call models and tools, declares its effects, can be
   called from a workflow and from another task, and nests in the trace like a
   workflow.
4. **Workflow-level control flow**: bounded `for` over steps, `gather`/fan-out,
   early `return`, and `parallel` that may contain `execute` calls under one
   grouped approval. Today a workflow is a straight line of steps.
5. **A Kosika standard library and a real package import**, with capability
   manifests. Eight agents currently define `fn kind_of(msg: Msg) -> Text`
   with eight different bodies and none import anything.

Two supporting moves make those five usable in production: **context/token/cost
as checked resources**, and **retrieval, memory and channels as typed
declarations** rather than host adapters glued together by hand.

One constraint governs the schedule and is easy to miss from the docs: Kosika
has **two engines** (Python reference and Go native) held to one conformance
suite, so a syntax feature costs two implementations plus cases, docs, schema
and replay. The plan below is written around that, and starts by making the
suite capability-aware so one engine can lead.

Everything below respects the existing thesis — *unlimited expression, bounded
authority* — and the existing contract discipline: parser, checker, runtime,
schema, trace and replay change together, or the feature does not ship.

## What I checked, and what it showed

Commands run against this checkout:

```text
python -m kosika.cli check examples/agents/assistant/main.kos   -> OK
python -m pytest --collect-only -q                              -> 1747 tests, 1 collection error
```

The collection error is in `tests/test_learning_evaluation.py`:
`kosika.learning_evaluation` imports `census` from `kosika.lessons`, which does
not define it. That is a worktree-health issue, not a language finding, but it
means "the suite is green" cannot currently be claimed; it should be fixed
before any contract change lands.

Structural observations that drive the recommendations:

| Observation | Number | Why it matters |
| --- | --- | --- |
| Agent programs in `examples/agents/` | 15 | The real workload the language is designed for |
| Total lines of those programs | 8,530 | Largest is `assistant` at 1,634, `coder` at 1,784 |
| Agents that import another `.kos` module | 0 | No reuse path exists between programs |
| Copies of `fn kind_of(msg: Msg) -> Text` | 8 | Same name and signature, eight different bodies |
| Tools declared in `assistant` | 3 | Authority is already the easy part |
| Workflow steps in `assistant` | 106 | Orchestration is done by hand |
| Language-level tensor/neural types | 0 | `kosika/tensors.py` is a host API, not syntax |
| Package registry / remote imports | none | `kosika/pack.py` moves one program, not a library |

The duplication is the important number. It is not that the authors were
careless; it is that the language gives them no place to put a shared
component. Every agent is a sealed program, so every agent re-derives the same
text classification, date handling, and result shaping.

## What is already strong and must not regress

- **Declarations are requests, never authority.** Models, tools, memory
  folders, agent `can` allow-lists and approvals all fail closed. Any new
  feature below must keep that property.
- **Typed boundaries at every trust edge.** Model answers, tool results,
  workflow inputs and outputs are schema-validated. New constructs must
  generate schemas, not bypass them.
- **Replay as validated outcomes.** Trace version 5 plus offline replay is the
  moat. Every new step kind must lower to something the existing replay can
  check, or the trace format must be versioned with it.
- **Thinking steps** (`classify`, `extract`, `summarize`, `decide`), `verify`,
  `vote`, `escalate`, `retry` and `otherwise` are a genuinely novel surface.
  They are the right direction: model behaviour made checkable.
- **Budgets, approvals and grants are language-level.** Very few languages can
  say "this workflow may make six model calls and nothing may touch the disk
  without a yes".

The rest of this document adds expressiveness without adding authority.

## The organising idea: a component is five contracts

A reusable AI component in Kosika should be describable by five things, and
each should be checkable:

| Contract | What it fixes | Current state |
| --- | --- | --- |
| **Data** | input/output types, alternatives, collections | records and `T?` only |
| **Behaviour** | what it promises, what it may return | function signatures, `verify`, `otherwise` |
| **Effect** | models, tools, approvals, memory it reaches | per-tool and per-agent, not per-component |
| **Cost** | calls, tokens, money, time | call/second budgets only |
| **Evidence** | what the trace proves, what replay checks | events and replay, unsigned |

Kosika has behaviour, effect and evidence partly. Data and cost are the gaps,
and there is no first-class *name* for a component that can be passed,
selected, imported or replaced. The proposals below fill those holes in that
order.

## A. Data contracts: unions, generics, recursion, refinements

### A1. Tagged unions (`choice`)

The single highest-leverage addition.

Today a component that can refuse must return something like:

```kosika
record Outcome { ok: Bool text: Text code: Number }
```

The checker cannot prove that `text` is meaningful when `ok` is true, or that
`code` is zero when it is false. Every consumer re-checks fields at runtime,
and a wrong field access is a *run* failure rather than a *check* failure. In
the 15 agents this pattern appears repeatedly as `{ok: Bool, ...}` records.

Proposed shape:

```kosika
choice Filing {
  filed(invoice: Invoice)
  refused(reason: Text)
  needs_review(invoice: Invoice why: Text)
}

fn describe(filing: Filing) -> Text {
  return match filing {
    case filed(invoice) { "filed " + invoice.id }
    case refused(reason) { "refused: " + reason }
    case needs_review(invoice, why) { "review " + invoice.id + ": " + why }
  }
}
```

Rules to define in `KOSIKA.md`:

- A `choice` has at least one alternative, each with a unique name; an
  alternative may carry zero or more named fields of declared types.
- Construction is `filed(invoice)` in expression position; the constructor is
  not a first-class value unless nullary.
- `match` is exhaustive over alternatives with no `case _` required; a missing
  alternative is `check/exhaustiveness`, and a redundant one is
  `check/unreachable-arm`.
- Matching narrows: inside `case filed(invoice)`, `invoice` has type `Invoice`.
  This extends the existing `exists()` narrowing rather than inventing a second
  mechanism.
- JSON encoding uses a single discriminator field (`{"$case": "filed",
  "invoice": {...}}`), generated as `anyOf` closed objects at every trust
  boundary, so an outside value is checked against the same alternatives the
  program matches on.
- `T?` becomes sugar for a built-in two-alternative choice (`some(T)` /
  `none`), preserving every existing program and hash.
- Replay validates the discriminator and the selected alternative's schema;
  nothing else changes.

What it unlocks: tool results that distinguish rejection from failure, model
answers that distinguish "I don't know" from "I refuse", agent plans that
distinguish approval-required from unsafe, and state machines encoded as data.

### A2. Generics

Without generics, `Result`, `Page`, `Batch`, `Candidate`, `Citation` and every
pipeline must be redeclared per payload type. That is why the agents cannot
share code even when the logic is identical.

Proposed shape:

```kosika
record Page<T> { items: T[] next: Text? }

fn choose<T>(items: T[], keep: fn(T) -> Bool) -> T[] {
  return for item in items where keep(item) { item }
}

workflow rank<T: Scored>(items: T[], limit: Number) -> T[] {
  top = items.sort_by(field: "score").reverse().take(count: limit)
}
```

Rules:

- Type parameters on `record`, `fn`, `task` and `workflow`; instantiation is
  explicit at call sites or inferred from arguments.
- Bounds are existing `shape` names (`T: Scored`), because shapes already mean
  "has these functions". No new subtyping model is needed.
- The checker monomorphises. The program hash covers declarations, not
  instantiations, so adding a use does not change the hash; trace schemas
  record the instantiated types.
- Generic records cannot be used as model/tool schemas until instantiated.
- Recursive generic types are still refused until A3 lands.
- Error messages must name the instantiation (`Page<Invoice>`), not `T`.

What it unlocks: one `map`/`filter`/`batch`/`rank`/`retrieve` component over
any record; one `Result<T>`; one `Pipeline<In, Out>`.

### A3. Recursive record schemas

`KOSIKA.md` explicitly refuses recursive records. AI data is recursive: trees
of document sections, nested tool arguments, conversation turns, agent plans.
The workaround — flattening to paths and indices — pushes structure into text
and loses checking.

Allow recursion with the existing bounds discipline: a depth limit (say 32)
enforced at schema validation, a size limit on serialised values, and a
checker cycle check that only rejects unguarded recursion through `T?` or
`T[]` without a depth bound. The trace already validates nested JSON; this is
a schema-generation change, not a new runtime.

### A4. Refinements and contracts (small, high value)

Confidence scores, percentages and non-empty text are everywhere. A cheap
version of refinement types removes repeated clamps:

```kosika
fn confidence(raw: Number) -> Number in 0..1 {
  return raw.clamp(low: 0, high: 1)
}
```

Start with three forms — `Number in a..b`, `Text where length > 0`, and
`Array where length <= N` — checked at boundaries and at `return`. Full
dependent types are out of scope. A `requires`/`ensures` pair on `task` and
`workflow` is the natural next step and can be lowered to runtime checks plus
trace events, like `verify` already is.

## B. Behaviour composition: `task` and first-class components

### B1. `task`: the missing middle

`fn` is pure by design and cannot call a model or a tool. `workflow` is a
top-level, named, traced unit that cannot be called as a value, cannot take a
function argument, and always requires at least one step. There is nothing in
between, so any reusable *effectful* behaviour has to be copy-pasted into each
workflow.

Proposed shape:

```kosika
task triage(ticket: Ticket) -> Routing
  uses reader
  can: [inbox_read]
{
  label = classify ticket.text into ["billing", "bug", "other"]
  page  = execute inbox_read(inbox_read: {id: ticket.id})
  return route(label: label, page: page)
}
```

Semantics:

- A `task` has typed parameters and one typed result, like a function.
- It may contain model calls, thinking steps, read-only tool calls, memory and
  board steps, and calls to other tasks. It may not contain `ask` or a
  workflow call (those stay workflow-level) and it may not declare budgets
  (the enclosing workflow owns the budget).
- Every effect is declared: `uses` names models, `can` names tools. The checker
  rejects an undeclared use, exactly as it does for agents today.
- An `execute` tool inside a task is allowed only when the calling workflow
  says `require approval`; the approval is still per exact call.
- Its events nest in the trace under the calling step, with a stable id, the
  way nested workflow events already do; replay validates the nested list.
- It can be called from `fn`? No. Tasks are effectful; functions stay pure.
- It can be tested with `answers` in `test` blocks, like a workflow.

This one construct removes most orchestration duplication: prompt assembly,
retrieval, classification, post-processing and fallbacks become named, typed,
independently testable pieces.

### B2. First-class component values

Agents, tasks and workflows should be referable as values with checked
signatures, so strategy and supervisor patterns stop being string lookups or
static `match` ladders.

```kosika
agent cheap_reader { model: small ... }
agent strong_reader { model: large ... }

fn pick(risk: Number) -> agent(Ticket) -> Verdict {
  return if risk > 0.7 { strong_reader } else { cheap_reader }
}

verdict = ask pick(ticket.risk)(ticket)
```

Rules:

- `agent(In) -> Out` is a type; the value carries the agent's declared
  capabilities in its type or in a checked effect set (see D1), so a host can
  refuse to bind it before the run.
- A component value may be stored in a record, passed to a `task`, returned,
  and selected by `if`/`match`.
- Calling a component value is an ordinary step; the trace records both the
  value's declaration name and how it was selected, so replay is deterministic.
- Workflow values follow the same rule with `workflow(In) -> Out`; calling one
  obeys the existing acyclic depth-32 rule.
- `system "agents"` remains for cross-folder handoff, but in-program
  composition no longer needs a string and a host folder.

What it unlocks: supervisors, routers, ensembles and evaluator loops written
as data, not as duplicated `match` steps.

## C. Workflow control flow

A workflow is currently a straight line: one step per call, branching only via
a `match` over step calls, no loops, no early exit, no fan-out over a list.
Real AI software is map-reduce over documents, retry-until-acceptable,
iterative refinement, and early refusal.

Proposed additions, all bounded by the existing loop and step budgets:

```kosika
workflow review(docs: Document[]) -> Report {
  budget 20 model calls, 120 seconds
  summaries = for doc in docs limit 32 { summarize doc.text }
  verdicts  = for doc in docs limit 32 { decide "Is this policy compliant?" given doc.text }
  if any_refused(verdicts) { return refused_report(verdicts) }
  report = merge(summaries, verdicts)
}
```

Rules:

- `for` over a list of steps is a bounded map: every iteration counts against
  the step and model budgets; `limit` is required or defaults to a host cap.
- `gather` (or `parallel for`) evaluates independent iterations concurrently
  where the host allows it, with the same trace as serial order, exactly as
  the existing `parallel` block does.
- `if` at workflow level chooses between step sequences, not just calls; both
  branches type-check to the workflow result.
- `return` ends the workflow early with the result type. A workflow with one
  exit keeps today's shape.
- `parallel` gains **execute** calls when the block declares `approve as one`:
  the host is shown the ordered list, approves once, and each call's
  completion is journaled. This is the same bargain as plan-first agents, and
  it removes the main reason parallel blocks cannot do real work.
- Cancellation of a failed branch is cooperative and recorded; the trace stays
  the serial one.

What it unlocks: chunk-and-summarize over a folder, multi-document extraction,
N-of-M selection, iterative draft/critic loops, and batch tools.

## D. Effects, cost and evidence as component contracts

### D1. Effect sets

Tools have `access`, agents have `can`, but a `task` or workflow does not have
a checkable summary of what it reaches. Add a declared effect set:

```kosika
task summarize(doc: Document) -> Summary
  uses reader
  can: [files_read]
  effects: {model, files.read}
```

- The checker computes the transitive effect set and rejects a declared set
  that is smaller than the actual one.
- A host may bind or deny a whole component by its effect set, not only
  call-by-call.
- `fn` has the empty effect set and remains pure; that becomes a checked
  property rather than a convention.
- A package's manifest publishes its effect set, and `import` fails closed if
  the host's grants do not cover it.

This is the feature that makes "components" meaningful to a security reviewer:
one line tells them what a component can reach.

### D2. Cost as a resource: tokens and money

Budgets count calls, steps and seconds. They do not count tokens, and a model
declaration cannot state a context window, price, or fallback chain. For AI
software, token and money budgets are the resource that actually constrains
design.

```kosika
model reader {
  input: Question
  output: Answer
  deployment: "local-model"
  context_window: 32768
  price: { input_per_1k: 0.0, output_per_1k: 0.0 }
  fallback: [strong_reader]
}

workflow main(ticket: Ticket) -> Routing {
  budget 6 model calls, 40k tokens, 0.05 money, 30 seconds
}
```

Rules:

- Token budgets require a declared tokenizer or a host-supplied counter; the
  checker records which one, so replay can validate the count.
- A model call whose prompt would exceed `context_window` fails before the
  provider is called, with a checkable error, instead of relying on the
  provider to truncate.
- A fallback chain is explicit and ordered; `escalate` becomes the step-level
  form of it. A fallback that is not granted is refused before the run.
- Money is decimal, never float.
- The trace records token and cost per call; the run result reports totals.

### D3. Signed / chained traces

Replay proves internal consistency, not origin. For AI software that makes
decisions, evidence should be portable:

- Each trace event carries the hash of the previous event and the program hash.
- `kosika verify-trace` checks the chain and reports the signing key or
  "unsigned".
- Signing is a host key, never a language credential; the language only
  defines the shape.

This is small and it turns "we have a trace" into "we can show this exact run
to someone else".

## E. Knowledge: context, retrieval, memory, channels

### E1. Context as a typed, budgeted value

Prompt assembly is hand-written string concatenation in every agent, and the
agent loop appends messages until `max_steps` with no window management. Add a
declared context:

```kosika
context conversation of Message {
  window 8000 tokens
  keep last 6
  when full: summarize oldest 20
}

step reply {
  answer = use_model voice(context: conversation, prompt: msg.text)
}
```

- The selected messages and their token count are recorded in the trace.
- `when full` is a checked policy, not host behaviour; the summarisation it
  triggers is an ordinary model call with its own budget and trace event.
- A model step that takes a context is a normal typed call underneath, so
  replay works unchanged.

### E2. Retrieval and indexing as declarations

`system "meaning"` and `find` exist but require manual glue and a host
embedding grant. RAG should be a component with a typed result and a citation:

```kosika
index manuals of Section from folder "./docs" using embedder

step look_up {
  hits = search manuals for ticket.text limit 5
}
```

- `index` declares the source folder, the record type, and the embedding
  model; both the folder and the model are host grants.
- `search` returns `Hit[]` where `Hit { id, text, score, path, line }`.
- Index contents are journaled per run; replay uses recorded hits and never
  re-embeds.
- A citation is a value, so `with evidence` can check it against the retrieved
  text instead of a free-form quote.

### E3. Memory with a lifecycle; boards as channels

`memory NAME of Record` is key-value with replace-only semantics. That is not
enough for knowledge an AI system accumulates. Add, without breaking the
existing form:

- append-only history per key, with `since` and `at` queries;
- `forget key` and `expire after N days`, both recorded events;
- a status field: `unverified`, `supported`, `trusted`, `contradicted`,
  `archived`, promoted only by evidence the trace can re-check;
- typed queries: `recall customers where plan == "pro" limit 10`.

Boards are per-run messages with one record type and no reply correlation.
Add `channel Request -> Response` with a request id, so agent A can await agent
B's answer, and `quorum 3 of 5` over checkable answers for actual consensus —
the piece `swarm` currently lacks by design.

### E4. Streaming

The provider streams internally and reassembles. Users and UIs need partial
output, and long generations need cancellation:

```kosika
for piece in stream voice(prompt: p.text) limit 4096 {
  append(piece.text)
}
```

The trace records the assembled answer plus chunk boundaries; replay validates
the assembly and does not re-stream. A stream that is abandoned is a normal
cancellation and is recorded as such.

## F. Ecosystem and tooling

### F1. A Kosika standard library

There is no `.kos` library in the tree. The first one should be small and
written in Kosika, tested by conformance: `text` (classification helpers,
normalisation), `time`, `result` (once unions land), `page`, `retrieval`,
`prompt` (context assembly), `eval` (scoring helpers), `agent` (routing and
supervision patterns). The eight `kind_of` bodies are the acceptance test:
after the library lands, the agents should import one implementation.

### F2. Packages, not just packs

`kosika/pack.py` moves one program and proves its bytes. That is not a library.
Add:

```kosika
import package "kosika/retrieval@1.2" as retrieval
```

with a lockfile, a signed index, and a capability/effect manifest per package.
Local relative imports stay for source. Fail closed when a package's declared
effects are not covered by the host's grants. Do not build a registry before
the standard library exists; do build the manifest format first, because it is
the same format as D1.

### F3. Tooling that makes design visible

- `kosika inspect --effects --cost` prints the component graph with effect
  sets and worst-case call/token bounds.
- The LSP shows the same as inlay hints, and a "capability diff" when a
  declaration changes.
- `kosika simulate --trace` runs a program against recorded answers to test a
  changed prompt without a model.
- `test` gains `property` blocks with generated records and shrinking, and
  `golden` blocks that pin a trace's result and event shapes.
- The MCP server exposes the same inspection tools, so an authoring model can
  see the effect and cost budget it is designing within.

## G. Smaller language hygiene (do these alongside)

- **Finish or remove parsed-but-unexecuted forms.** `try { ... } retry N` and
  inline anonymous `fn` parse but do not run; `swarm` parses but `run` refuses
  it. A language with advertised syntax that fails at runtime erodes trust.
  Either implement them or reject them at parse time.
- **Record literal inference.** `let ticket = {text: "..."}` still needs a type
  context. Infer from the nearest declared parameter or return, or require
  `Ticket(...)`; the current `check/record-context` error is a common paper cut.
- **Module aliases and re-exports.** Imports are flat and one-level. Add
  `import "a.kos" as a` and `export from` to make libraries composable.
- **Operator overloading for numeric types.** `Number[][]` uses methods
  (`times`, `plus`) while scalars use `*`, `+`. AI maths reads badly. Allow
  `@` for matrix multiply and `+`/`*` on vectors/matrices with shape checks.
- **Method extension for library types.** With a standard library, users will
  want `Text.slug()` without forking the type. A checked `extend Text { fn
  slug(self) -> Text }` is a contained version of type classes.
- **Better spans.** `KOSIKA_PLAN.md` already lists expression-precise spans as
  incomplete; do it before adding new syntax, because every new construct
  multiplies the cost of imprecise errors.
- **One contract version bump.** Everything in A–E changes the language, so
  `kosika-language/0.2.0` with a migration note, updated
  `tests/test_language_contract.py`, and a compatibility table is the right
  vehicle. Do not ship these as silent additions.

## What not to build (and why)

- **Tensor and neural-network primitives as language syntax.** `tensors.py`
  exists as a host module and that is the right place for it. Kosika's
  advantage is not beating NumPy; it is making the *system* around a model
  checkable. A `tensor` type would add a second numeric tower, a second replay
  surface and a hardware story, and would not make a single agent shorter.
  Bind PyTorch or a Rust kernel as a `component` instead.
- **`memory_bubble`, `assumption`, `judgment`, `personality` as four new
  declaration families.** `COGNITIVE_LAYER_DESIGN.md` proposes exactly that.
  These are records with a status, a source and a confidence; with unions
  (A1), memory lifecycle (E3) and refinements (A4) they are expressible with
  ordinary declarations, without four keyword families that each need parser,
  checker, schema, trace and replay support. Add *one* knowledge mechanism,
  not four.
- **A Python-to-Kosika translator as a priority.** It is expensive, it cannot
  preserve the guarantees that make Kosika interesting, and the honest interop
  path already exists: `kosika.component/v1`. Revisit after packages.
- **Self-modification and self-hosting now.** The authoring loop is
  experimental and the language still lacks unions and generics. A language
  that cannot express `Result` should not be rewriting itself.
- **Dynamic typing escape hatches.** Every gap above will tempt a `dynamic`
  type. Refuse it; the whole product is the checked boundary.
- **Distributed execution.** Keep `sessions.py` and the local sandbox. Multi-
  device agents are a platform problem that would stall the language work.

## The constraint the plan must respect: two engines, one suite

Kosika is not one implementation. `kosika/` is the Python reference engine
(about 52k lines) and `native/` is a Go engine (about 15k lines excluding
vendor). `conformance/` holds both to the same printed output for every case:
`tests/test_conformance.py` builds the Go program and compares it line for
line, and `ENGINE tokens` must agree on every `.kos` file in the repository.

The native engine is not a stub. It already has records, functions, closures,
bounded recursion, workflows, models, agents, tools, memories, boards, imports,
enums, shapes, `try`/`fail`, and tests. It lacks some system tools, swarms,
vectors and plan checks, and the suite currently avoids those. Anything new
widens that list unless both engines land together.

This changes the arithmetic of every proposal above:

- A syntax feature is not one implementation. It is two, plus conformance
  cases, `KOSIKA.md`, schema, replay and the examples.
- Generics are the worst case: inference, instantiation and exhaustiveness
  must agree between a Python checker and a Go checker, and refused-program
  messages are part of the conformance contract.
- The suite has no capability negotiation today. Adding a case that only the
  reference passes makes the native half of CI red, so features are forced
  into lockstep implementation.

**Make the suite capability-aware before adding features.** Add an
`ENGINE capabilities` command and a `requires:` tag per conformance case, so
the suite checks a case only on engines that claim it. The reference can then
lead by weeks without breaking CI, and each case still becomes law once both
engines pass it. This is a two-to-three-week investment that removes the
biggest schedule risk in the whole plan.

Two consequences for the order:

- Prefer features whose Go implementation is mechanical: unions, `task`
  lowering, workflow loops. They reuse existing structures rather than
  introducing inference.
- Defer user-defined generics until there is a second contributor, and ship
  a small closed set of built-in concrete types (`Result`, `Page`, `Citation`,
  `ToolError`) long before them. That covers most of the reuse benefit at a
  fraction of the dual-engine cost.

## Priority and acceptance criteria

Each phase is independently shippable and measurable. "Done" means the
contract, both engines where the feature is syntax, conformance cases and docs
change together. Effort assumes one focused engineer with AI assistance and
both engines; two contributors roughly halve it, though the decision load does
not halve.

### Phase 0 — make the suite able to lead (2–3 weeks)

- Fix `tests/test_learning_evaluation.py` so the suite collects.
- Add `ENGINE capabilities` and `requires:` tags; check a case only on engines
  that claim it.
- Add a refused case that names an unsupported feature, so an old engine says
  so clearly instead of failing obscurely.

Done when the reference passes a new `choice` case while the native engine is
skipped by capability, and CI is green.

Done 21 October 2026: both engines print `capabilities`, a suite program may
say `// requires: choice`, and the native engine claims and passes `choice`.
The gate still permits a future case to be skipped by an engine that does not
claim its surface.

### Phase 1 — data you can model (8–12 weeks)

`choice`, recursive records, boundary refinements, and the built-in concrete
`Result`, `Page`, `Citation` and `ToolError` types. User-defined generics are
deferred to Phase 5.

Done when:

- A tool can return a union of success/failure/refusal, a model can be asked
  for one, and a trace of each replays.
- Conformance covers exhaustive match, narrowing, schema generation, and a
  deliberate wrong-alternative answer, in both engines.
- At least three agents use `Result`/`Page` instead of their own `ok: Bool`
  records.

### Phase 2 — components you can reuse (10–14 weeks)

`task`, effect sets, module aliases, and a module-root grant so a shared
library can live outside one agent's folder.

Done when:

- `triage`, `retrieve` and `summarise` exist once as tasks and are called by
  at least five agents.
- `kosika inspect --effects` prints each agent's transitive effect set, and a
  host refuses a component whose effects exceed its grants.
- The 15 agents lose at least 20% of their total lines with all tests green.

### Phase 3 — orchestration and component values (8–12 weeks)

Workflow `for`/`gather`/`if`/`return`, parallel execute with grouped approval,
cancellation, and first-class agent/task values.

Done when:

- A workflow summarizes a 20-document folder in one bounded parallel block,
  produces a trace identical in shape to the serial run, and replays.
- A fan-out that exceeds its budget stops with `budget`, not a partial result.
- An execute call inside a parallel block runs only after one approval bound
  to the exact ordered list, and a second run with the same approval is
  refused.
- A supervisor selects an agent by value and the trace records the selection.

### Phase 4 — knowledge and cost (10–14 weeks)

Context budgets, retrieval/index, memory lifecycle, channels/quorum, token and
money budgets, streaming.

Done when:

- A program answers from an index with a citation that `with evidence` checks
  against the retrieved text, and replays without an embedding model.
- A conversation exceeding its window triggers the declared summarisation, and
  the trace records which messages were dropped and why.
- A run stops at its token or money budget with `budget`, and reports totals
  that match the per-call trace entries.
- Two agents exchange a typed request and response, and a quorum of three
  produces a checkable answer with every ballot in the trace.

### Phase 5 — ecosystem and generics (ongoing)

Standard library, package manifest and import, user-defined generics, LSP
effect/cost views, property tests, trace signing.

Done when:

- Two independent programs import the same versioned component and the
  checker proves the same effect set for both.
- A changed component that widens its effects is refused by a host that has
  not granted them.
- A trace can be verified by a third party without the program, the model or
  the host.
- One generic `Page<T>` and one generic `rank<T: Scored>` are used by at
  least three agents without redeclaration.

### The cheap half: what ships without a grammar change

Some of the "other half" does not need a language version bump, and should be
done first because it buys real capability in weeks rather than quarters:

- **A module root and a `.kos` standard library.** `load_program` already takes
  a `root`, but the CLI passes only the entry file's folder, so an agent cannot
  import `lib/text.kos` today. A `--module-root` grant plus a standard library
  written in Kosika removes the eight `kind_of` copies without new syntax. The
  loader (both engines) changes, not the grammar.
- **Cost accounting in the host.** Count tokens and money in the Python host,
  record them in the raw trace, and report totals. Keep it outside the
  conformance trace contract until the native engine can emit the same fields.
- **Retrieval through existing tools.** `system "meaning"` and the `search`/
  `find` operations already exist; a `.kos` library of typed wrappers delivers
  a usable RAG component with no parser work, for the Python engine.
- **Trace signing in the host.** Add an optional signature and a verifier;
  gate it out of cross-engine conformance until both engines agree.
- **Inspection in the CLI and LSP.** `--effects` and `--cost` can be computed
  from the checked AST before either engine executes anything.

None of these relax authority; they are the cheapest way to make Kosika
useful while the grammar work proceeds.

## Risks and invariants

- **Every addition must lower to existing trace primitives**, or the trace
  version changes with a documented migration. The team has been disciplined
  about this; keep it.
- **New syntax is authority.** `task`, `parallel` execute groups and packages
  all widen what a program can reach if implemented carelessly. The invariant
  stays: a declaration grants nothing; a host binds it; a person approves
  effects.
- **Generics and unions strain the checker.** Budget for checker work, not
  parser work: exhaustiveness, narrowing, instantiation, and error messages
  are where the time goes, and each must agree in two languages.
- **Lockstep is the schedule risk.** Without a capability-aware suite, every
  feature waits for the slower engine. Phase 0 exists to remove that.
- **The agents are the benchmark.** Every phase should be justified by a
  measured reduction in their lines, or by a capability they could not
  express before. If a feature does not move either number, it is decoration.
- **Fix the suite first.** `tests/test_learning_evaluation.py` does not
  collect today. No contract change should land on top of a red collection.

## The shortest path

If only three things can be done this quarter, do these:

1. Capability-aware conformance, plus a module root and a `.kos` standard
   library. This is mostly loader and tooling work, and it starts reducing
   duplication immediately.
2. `choice` + exhaustive narrowing + the built-in `Result`.
3. `task` + effect sets.

Those three turn the 15 sealed agent programs into a system of components,
which is the thing "designing AI software in Kosika" actually means. Generics,
workflow loops and retrieval follow naturally once components have somewhere
to live.

## Built so far

- **21 October 2026 — `choice`, in both engines.** Declarations, constructors,
  exhaustive match with field bindings, JSON schema at every trust boundary,
  trace and replay, and module import/export. The native engine builds the same
  values (`{"$case": ..., ...}`), checks the same errors, validates the same
  boundary values with the same messages, and writes the same trace events.
  Evidence: `examples/choices.kos`, `tests/test_choices.py`, the contract test,
  `conformance/cases/unions.kos`,
  `conformance/refused/choice_not_exhaustive.kos`, and the native
  trace/replay/boundary test in `tests/test_conformance.py`. Capability tags
  (`// requires: choice`, `ENGINE capabilities`) make the suite's gate explicit
  for surfaces still to come.
- **21 October 2026 — a model or tool answers with a choice.** The declared
  output may be a `choice`; the answer is validated against exactly one
  alternative, its step has the choice's type, and the trace records the
  choice schema. `with evidence` and `as text` stay record-only. Evidence:
  `conformance/cases/choice_outputs.kos` covers a model answer and a tool
  answer in both engines.
- **21 October 2026 — choices in the tooling views.** The mindmap draws a
  choice as a type with its alternatives, their fields and the model/tool
  edges that answer with it, and the project index lists each choice with its
  alternatives. Evidence: `tests/test_mindmap.py` and
  `tests/test_project_index.py`.
