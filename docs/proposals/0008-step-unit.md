# Proposal 8: `step`, a unit that remembers what it made

Status: implemented in both engines. Spec: `KOSIKA.md`, "Steps that
remember". Tests: `conformance/cases/step_unit.kos`,
`conformance/refused/step_without_call.kos`, `tests/test_step_unit.py`.

## Why

The research (`kosika-research/reports/Programming paradigms for
Kosika.md`, item 14) and the book-reading agent asked for the same thing: a
named unit that may ask a model or read files, and whose result is recorded
and used again. "A `fn` can't call a model" keeps pure code pure, so that
line stays. But an agent that reads a 30-chapter book should not pay for
chapter 1 again when it is run again, or when two parts of one run need the
same chapter.

Before this, an agent had two ways, both outside the language. It could
keep its own cache in a memory, which is three extra steps for every call
(recall, check, remember) and easy to get wrong. Or it could rely on
`learn`, which remembers a model's answers by likeness, and is meant for
answers that are reviewed and grow better, not for "this exact work is
done".

## What

```kosika
step read_chapter(chapter: Chapter) -> Notes {
  notes = use_model reader(title: chapter.title, body: chapter.body)
}

workflow read_book(book: Book) -> Reading {
  read = for c in book.chapters limit 50 { call read_chapter(c) }
  out  = join(read)
}
```

- `step` declares a workflow whose result is remembered. The body is a
  workflow's body, so nothing new is learned to write one, and it is made
  with `call`.
- Within one run, the same step with the same inputs is made once. The
  second call gives the first call's result and makes no calls.
- With `run --memory FOLDER`, each result is kept in `FOLDER/steps`, and a
  later run of the same program uses it. This is the same folder that keeps
  the program's memories and lessons, so one grant covers what an agent
  remembers.
- A result is named by the SHA-256 of the program's identity, the step's
  name and its inputs. Both engines compute it alike, so a book read by the
  Python engine is not read again by the Go engine, and the other way round.
- The trace of a reused call carries `"reused": true` and holds the trace
  of the call that made the result, so replay needs no change and proves
  the reused result the same way it proves the first one.

## Choices made

- **Keyed by the whole program, not the step's own lines.** A step's result
  depends on the functions, models and tools it reaches. Hashing only its
  own text would reuse a result after a prompt or helper changed, which is
  wrong in a way nobody notices. Keying by the program is always right, at
  the cost of making steps again after any edit. A finer key, over just what
  a step reaches, is a later step (see "Not yet").
- **`call` stays.** `read_chapter(c)` without `call` reads as a function
  call, which a step is not. The checker says so and names the fix:
  `Step 'notes' uses unknown function 'read_chapter'; 'read_chapter' is a
  step, so make it with `call read_chapter(...)``. The same hint now helps
  when a workflow is called without `call`.
- **`step` at the top of a file, `step NAME {` inside a workflow.** The
  long form of a workflow step keeps its meaning. The two never meet: one
  is a declaration with parameters, the other an item of a workflow body.
- **Kept results are trusted as the folder's owner's.** A kept result that
  no longer fits the step's type is made again. Beyond that, the folder is
  the host's, as the journal and memories are.

## Not yet

- A finer key: the step's own text and everything it reaches, so an edit
  elsewhere does not make every step again.
- `effects { fs.read }` on a step, from the research sketch. That belongs
  with capabilities, later in the order.
- Forgetting old results. The folder grows by one small file for each
  distinct call; clearing `FOLDER/steps` makes them all again.
- The book-reading agent and the other agents in kosika-agents moving their
  hand-made caches to `step`. That is a good coder-swarm task once this
  lands.
