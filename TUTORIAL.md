# Your first Kosika program

This takes about ten minutes. You need Python 3.10 or newer. A model is
optional: every step except step 4 runs offline.

## 1. Install

From a checkout of this repository:

```text
python -m pip install .
```

That gives you the `kosika` command. Without installing, replace `kosika`
with `python -m kosika.cli` everywhere below.

## 2. Create a project

```text
kosika new hello
cd hello
```

You get four files:

| File | What it is |
| --- | --- |
| `main.kos` | The program |
| `input.json` | The question the program is asked |
| `responses.json` | A recorded answer, so you can run without a model |
| `README.md` | The commands from this tutorial |

Open `main.kos`. It has three parts:

```kosika
record Question { text: Text }
record Answer { text: Text }

model assistant {
  input: Question
  output: Answer
}

workflow main(question: Question) -> Answer {
  step respond {
    let answer: Answer = use_model assistant(text: question.text)
    return answer
  }
}
```

- A **record** is the shape of a piece of data.
- A **model** says what goes in and what must come out. It does not say which
  real model answers. You choose that when you run the program.
- A **workflow** is a list of named steps. This one asks the model once.

## 3. Check it and run it offline

```text
kosika check main.kos
kosika run main.kos --input input.json --model-fixture responses.json
```

`check` finds mistakes before anything runs. `run` prints the answer from
`responses.json` and writes `run_trace.jsonl`, a record of the run.

## 4. Run it with a real model

If you have [Ollama](https://ollama.com) running locally with a model pulled:

```text
kosika run main.kos --input input.json --model YOUR_MODEL
```

`--model` is the whole setup. Kosika prints which declared models that model
answers for, then prints the typed answer. If the model replies with anything
that is not a valid `Answer`, the run fails instead of passing bad data on.

Other servers: add `--model-url URL` for any OpenAI-compatible endpoint, or set
`KOSIKA_MODEL_URL`. An API key is read from `KOSIKA_MODEL_API_KEY`, never from
the command line. For Ollama reasoning models, `--model-protocol ollama` is
usually faster.

If you forget `--model`, Kosika refuses to run and tells you why: declaring a
model in a program never gives the program access to one.

## 5. Replay the run

```text
kosika replay run_trace.jsonl
```

This returns the recorded answer without calling any model. Keep traces
private: they contain your inputs and the model's output.

## 6. Change the program

Add a field to `Answer` in `main.kos`:

```kosika
record Answer {
  confidence: Number
  text: Text
}
```

Run step 3 again. The offline run now fails with
`missing required property 'confidence'`, because the recorded answer no longer
matches the type. Fix `responses.json`:

```json
{
  "assistant": [
    {"text": "Kosika is a typed language for AI workflows.", "confidence": 0.9}
  ]
}
```

Run it again and it passes. A live model is told the new shape automatically.

## 7. See what the program contains

```text
kosika audience-map main.kos --audience outsider --format text
```

This prints a map built from the checked program. Try `--audience reviewer`
for more detail, or `kosika mindmap main.kos --format mermaid` for a diagram.

## Where next

- Give the model tools: `examples/research_agent.kos` declares a `tool` and an
  `agent` that may use it. Run it with the fixture commands in
  [README.md](README.md#local-model-provider-and-agent-loop).
- Several agents under one human approval: `examples/swarm_review.kos` and
  [Approved swarm runs](README.md#approved-swarm-runs).
- Build the same program from Python instead of a `.kos` file:
  [Python SDK](README.md#python-sdk) and `examples/sdk_hello.py`.
- The full language: [KOSIKA.md](KOSIKA.md).
