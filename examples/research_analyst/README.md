# Research analyst

Answers a question from the files in the folder you grant, and shows an answer
only with a quote that is really in a line it found, naming that file and line.
When nothing supports an answer it refuses and shows the closest lines.

```sh
kosika test examples/research_analyst/main.kos
kosika measure examples/research_analyst/main.kos --model qwen2.5:3b --repeat 3
kosika studio FOLDER --model qwen2.5:3b --allow-folder YOUR_NOTES
```

## What the program guarantees

Code picks the key words, searches the granted folder, and shows the model only
the lines that were found. The model must answer and give one exact quote. Code
then checks, before anything is shown:

- the quote is word for word in one of the lines found (case and spacing
  ignored, and a quote that copies its own `file:line:` place is understood);
- the line quoted is not a heading, which states no fact;
- the line quoted shares a word with the question;
- a quote with no answer, or an answer with no quote, is refused.

`verify` on the `reader` model applies those checks to every answer, `retry 1`
asks again with the reason, `escalate strong` asks the second model, and
`otherwise {answer: "", quote: ""}` turns exhaustion into the refusal the
caller shows. A run never shows an answer the checks did not pass.

## Measured

`kosika measure` runs the evals at the end of `main.kos`. Six ask something the
fixture lines cannot answer, and must be refused; four ask something they do
answer, and must come back naming the exact line. Measured 24 October 2026 on
this laptop through Ollama:

| model | repeat | evals passed | unanswerable questions answered anyway | answerable questions refused | time |
| --- | ---: | ---: | ---: | ---: | ---: |
| `qwen2.5:3b` | 3 | 25 of 30 | 0 of 18 | 5 of 12 | 151 s |
| `gemma3:4b` | 2 | 16 of 20 | 4 of 12 | 0 of 8 | 401 s |

The two models fail in opposite directions. `qwen2.5:3b` never cited anything
irrelevant across 18 runs, and instead refused questions the files do answer,
5 times in 12. `gemma3:4b` answered every answerable question correctly and
cited a real line that does not answer the question in 4 of 12 unanswerable
runs — every one of them from the two questions written to be tempting:
"When is the demo for the new client?" against a line about Ravi's demo, and
"What does the licence cost for two people?" against the single-user price.
Those lines share a word with the question, so the shared-word check passes,
and a real quote is shown as support for an answer it does not support.

That is the known weakness of a quote check: it proves the words are real, not
that they answer the question. Knowing where it fails is the measurement's
point, and a cheaper check is worth trying only against numbers like these.
