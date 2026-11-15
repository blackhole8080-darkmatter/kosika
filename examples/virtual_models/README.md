# Kosika virtual specialists

A virtual specialist is a **model-slot wrapper**, not a newly trained set of
weights. It calls the base model the host has authorized, retrieves relevant
passages from configured local Kosika sources, and adds the passages to that
request with source/line citations. The specialist cannot add tools or bypass
Kosika's model-call, approval, input, or output checks.

The starter profile `kosika-specialist.json` points at the language guide,
getting-started guide, Python/Go runtime, and example programs. `.kos` files
are checked before they are indexed. In a directory, intentionally invalid
`.kos` examples are skipped; if a single file is configured directly and it
does not check, profile loading fails. Retrieval is lexical and bounded, not
embedding-based in this first version.

Run a declared agent with an offline fixture to verify the wiring:

```sh
kosika agent examples/test_host.kos --agent monitor \
  --task "Explain how Kosika model calls are bounded" \
  --model-fixture answers.json \
  --virtual-model examples/virtual_models/kosika-specialist.json
```

For a live provider, replace `--model-fixture answers.json` with
`--model qwen2.5:7b` (or another host-authorized model), and optionally set
`--model-url` / `KOSIKA_MODEL_URL`.

## Verified examples

To make a prior answer available to the specialist, add it to
`verified_examples` in the profile only after a person or a deterministic
acceptance check has verified it. Every example requires a task, answer,
`"verified": true`, and at least one evidence object:

```json
{
  "task": "Explain model call budgets",
  "answer": "They cap model invocations.",
  "verified": true,
  "evidence": [
    {
      "source": "../../KOSIKA.md",
      "line": 120,
      "quote": "An execution budget limits model calls."
    }
  ]
}
```

The evidence source must be among the configured sources, the line must exist,
and the quote must match that exact line. Answers are never added automatically
from agent output. To review a candidate JSON file containing `task`, `answer`,
and `evidence` (the evidence objects above), run:

```sh
kosika virtual-model add-example \
  examples/virtual_models/kosika-specialist.json candidate.json
```

Kosika prints the answer and exact source quotes, then requires an interactive
yes before saving. Non-interactive runs are refused. Acceptance rechecks the
profile and source snapshot under an exclusive lock and writes atomically; if
either changed after review, the candidate must be reviewed again. A changed
source snapshot changes the agent plan identity, so durable resumes and
approval hashes cannot silently carry over to different specialist knowledge.

Profiles use schema `kosika.virtual-model/v1`; their paths are relative to the
profile file. Supported source files are `.kos`, `.md`, `.py`, and `.go`. Keep
sources limited to material you intend to send to your configured model
provider; a hosted provider receives retrieved content as part of the model
request.
