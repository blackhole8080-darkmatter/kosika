# Kosika supported language contract

| Field | Value |
| --- | --- |
| Contract identifier | `kosika-language/0.1.0` |
| Package version | `0.1.0` |
| Source suffix | `.kos` |
| Emitted trace version | `5` |
| Stability | Prototype; incompatible changes are allowed before `1.0` |

This document is the normative contract for the Kosika surface that is
supported end to end by the parser, checker, runtime, and offline replay
validator. `MUST`, `MUST NOT`, `SHOULD`, and `MAY` are normative.

The implementation remains the executable authority. A disagreement between
this document and `kosika/parser.py`, `kosika/checker.py`,
`kosika/runtime.py`, or `kosika/trace_replay.py` is a contract bug. A language
change is incomplete until this identifier, this document, and
`tests/test_language_contract.py` have been reviewed together.

## Support labels

- **Supported:** parses, checks, executes through `kosika.runtime.run`, and
  produces a trace accepted by `kosika.runtime.replay`.
- **Host API:** implemented behavior available to an embedding host, but not
  syntax or semantics of the `.kos` language.
- **Tooling-only experimental:** parsed and possibly checked or rendered, but
  not an executable language feature. It carries no compatibility promise.
- **Planned:** design intent only. It MUST NOT be presented as implemented.

Only behavior labeled **Supported** below belongs to
`kosika-language/0.1.0`.

## Lexical grammar

- A source file is UTF-8 text.
- Identifiers match `[A-Za-z_][A-Za-z0-9_]*` and are case-sensitive.
- Whitespace separates tokens but is otherwise insignificant.
- `//` starts a line comment.
- String literals use double quotes and JSON-compatible escapes. String values
  may contain Unicode even though identifiers are ASCII.
- Number literals are decimal integers or decimals matching
  `[0-9]+(\.[0-9]+)?`. Negative values use unary `-`; exponent notation is not
  supported.
- The scalar literals are `true`, `false`, and `null`.
- Declarations, fields, and statements have no semicolon terminator. Commas
  separate call arguments and literal elements; record declaration fields may
  optionally use commas.

## Program and module grammar

A checked entry point is loaded with `load_program(path)`, not by parsing the
entry file in isolation when imports are present.

```ebnf
program     = { import | export | record | function | model | tool | agent |
                workflow } ;
import      = "import" string ;
export      = "export" "[" [ identifier { [","] identifier } ] "]" ;
```

Imports have these semantics:

- Paths MUST be relative, name `.kos` files, resolve inside the granted module
  root, and MUST NOT name a symlink file.
- A module sees its own declarations and declarations explicitly exported by
  each direct import. There are no namespaces, aliases, wildcard exports, or
  implicit re-exports.
- Export names MUST be unique local declarations.
- Duplicate names within a declaration category are rejected. Imported modules
  also MUST NOT contribute conflicting declaration names.
- Import cycles are rejected.
- The graph is limited to 64 files, nesting depth 16, and 256 KiB per file.
- Loading declarations never grants model providers, tool executors, MCP
  servers, files, sockets, or any other host authority.

`parse(source)` is supported for single-file programs without imports.
`load_program(path)` resolves imports, lowers named nested functions, and
checks the merged program.

## Types and values

### Types

The primitive types are:

| Kosika | Runtime JSON value |
| --- | --- |
| `Text` | string |
| `Number` | finite integer or floating-point number, excluding booleans |
| `Bool` | boolean |
| `Null` | null |

Supported composite types:

```ebnf
type          = identifier | identifier "[]" | function-type ;
function-type = "fn" "(" [ parameter { "," parameter } ] ")" "->" type ;
parameter     = identifier ":" type ;
```

- An identifier type MUST name a primitive or declared record.
- `T[]` is a homogeneous array.
- Function values are internal values with a checked named parameter list and
  output type. They MUST NOT appear in external record schemas.
- Type nesting is limited to 32.

### Records and schemas

```kosika
record Result {
  text: Text
  scores: Number[]
}
```

Record fields are required, unique, and closed: runtime inputs and external
results MUST contain every declared field and MUST NOT contain undeclared
fields. Recursive record schemas are not supported in `0.1.0`.

Array and record literals require a type context from a declaration, call
argument, return type, or containing field. The checker does not infer a record
name or the element type of an unannotated array literal.

Equality is structural JSON equality. Booleans are distinct from numbers;
integer `1` and floating-point `1.0` are equal.

## Records and pure functions

```ebnf
record   = "record" identifier "{" { identifier ":" type [","] } "}" ;
function = "fn" identifier "(" [ parameter { "," parameter } ] ")"
           "->" type [ "recurse" integer ]
           "{" { statement } { nested-function }
           "return" expression "}" ;
```

Supported functions:

- Are pure language computation: they cannot call models, tools, agents, or
  workflows.
- Use named arguments. Provided argument names and types MUST exactly match the
  declaration.
- Run their body in the order it is written: each `let`, `var`, loop,
  conditional, and assignment executes in turn, and a line may use only names
  declared above it.
- May declare immutable `let` locals and assignable `var` locals. Assignment
  to a `let`, an unknown name, or a value of another type is rejected.
- May declare named nested functions. A nested function may capture lexical
  values. `ref name` creates a typed function value, including its captures,
  and function values may be passed, returned, stored in workflow bindings, and
  called.
- May recurse only when every function participating in the cycle declares
  `recurse N`, where `N` is from 1 through 32. Static function nesting and call
  depth are also bounded at 32.
- MUST end in one `return` expression with the declared output type.

There is no implicit coercion. The one mixed arithmetic form is
`Text + Number`, which formats the number and concatenates it to the text.

## Expressions and control flow

The supported expression surface is:

- Scalar, array, and record literals.
- Variables, record field access (`value.field`), and array indexing
  (`value[index]`). An index MUST evaluate to an integer and be in range.
- Unary `not` on `Bool` and unary `-` on `Number`.
- Number arithmetic `+`, `-`, `*`, `/`, `%`; division or remainder by zero
  fails the run. `/` always produces a fractional Number (`6 / 2` is `3.0`).
  `%` is the floored remainder, so the result has the sign of the divisor
  (`-7 % 3` is `2`).
- Built-in methods, listed under "Built-in methods" below.
- Number comparisons `<`, `<=`, `>`, `>=`.
- Same-type equality `==` and inequality `!=`.
- Short-circuit boolean `and` and `or`.
- `Text + Text` and `Text + Number`.
- Named function calls and `ref` references.
- `else if` chains: `if a { x } else if b { y } else { z }`. A final `else`
  is still required.
- `if condition { value } else { value }`. The condition MUST be `Bool` and
  both branches MUST have the same type.
- `match value { case literal { value } ... case _ { value } }`. The subject
  MUST be scalar, patterns MUST match its type, and branches MUST return one
  type. A final wildcard is required unless all `Bool` values or the single
  `Null` value are covered.
- Lexical blocks with local bindings and an optional final value.
- Bounded loops described below.

Binary precedence, from lowest to highest, is `or`, `and`, equality,
comparison, addition/subtraction, multiplication/division/remainder. Unary operators bind
more tightly. Parentheses override precedence.

### Built-in methods

A method call is `value.method(name: argument)`. The set is closed: there are
no user-defined methods and records have none. The checker resolves a call from
the receiver's type, and every method is pure and deterministic.

| Receiver | Method | Result |
| --- | --- | --- |
| `Text` | `length()` | `Number` of characters |
| `Text` | `upper()`, `lower()`, `trim()` | `Text` |
| `Text` | `contains(part: Text)`, `starts_with(prefix: Text)`, `ends_with(suffix: Text)` | `Bool` |
| `Text` | `split(separator: Text)` | `Text[]`; the separator MUST be non-empty and a result over 512 parts fails the run |
| `Text[]` | `join(separator: Text)` | `Text` |
| any array | `length()` | `Number` of elements |
| array of `Text`, `Number`, or `Bool` | `contains(item: T)` | `Bool`, using `==` |
| `Number` | `floor()`, `ceil()`, `round()` | whole `Number`; `round` rounds halves up |

An unknown method or wrong argument name is a check error
(`check/unknown-method`, `check/method-arguments`). A parenthesised literal is
needed to call a method on a number literal: `(2.5).round()`.

### Bounded loops

```kosika
for item in values limit 32 { expression }
repeat 8 times { expression }
```

- `for` is a functional map over an array and returns the collected body
  results. Its `limit` is an integer from 1 through 512 and defaults to 128.
  Execution fails before the first iteration if the input array exceeds that
  limit.
- `repeat` takes an integer literal from 1 through 512 and returns the final
  body result.
- `break` and `continue` are supported only inside these loop bodies. A break
  returns the results collected so far from `for`; `repeat` returns its latest
  completed result or `Null`.
- Each iteration gets a child lexical scope. An outer `var` remains assignable.

## Models

```kosika
model reader {
  input: Question
  output: Answer
  deployment: "local-model"
  parameters: {
    temperature: 0.2
    max_output_tokens: 512
    top_p: 1
    frequency_penalty: 0
    presence_penalty: 0
    stop: ["END"]
  }
}
```

`input` and `output` are required record types. `deployment` and `parameters`
are optional. Supported parameter bounds:

- `temperature`: 0 through 2.
- `max_output_tokens`: 1 through 1,000,000.
- `top_p`: 0 through 1.
- `frequency_penalty` and `presence_penalty`: -2 through 2.
- `stop`: at most four unique non-empty strings, each at most 256 characters.

A model declaration describes a typed call; it does not create a provider.
The host MUST supply a binding for each model reachable from the selected
workflow before execution. A direct `use_model` call sends the input record to
that provider and accepts only a complete, strict JSON result matching the
declared output record.

## Tools and capabilities

```kosika
tool inspect_disk {
  source mcp "local_stdio"
  operation: "inspect_disk"
  access: observe
  input: DiskRequest
  output: DiskResult
}

agent reviewer {
  model: reader
  can: [inspect_disk]
  cannot: []
  instructions: "Inspect only the requested disk."
  max_steps: 8
  max_tool_calls: 256
  max_model_calls: 64
  deadline_seconds: 30
}
```

Tools require `source`, `operation`, `access`, and an output record. Input is
optional; an omitted input is an empty closed record. Supported access labels
are `observe`, `analyze`, `prepare`, and `execute`. Only `execute` has special
runtime approval semantics in `0.1.0`; the host remains responsible for making
its bound operation match the declared effect class.

`agent.can` is the enforced tool allow-list. Every listed capability MUST name
a declared tool. `agent.cannot` records explicit denial and MUST NOT overlap
`can`; it does not widen authority. An agent used by `ask` MUST name a declared
model. Its provider sees only tools in `can`.

Agent defaults and bounds:

| Field | Default | Allowed |
| --- | ---: | ---: |
| `max_steps` | 8 | 1–64 |
| `max_tool_calls` | 256 | 1–1,000,000 |
| `max_model_calls` | 64 | 1–1,000,000 |
| `deadline_seconds` | none | greater than 0 and at most 86,400 |

Declarations are requests for authority, never authority themselves. The host
MUST bind declared providers and tools explicitly. Bindings for undeclared
models or tools are rejected, and reachable capabilities are preflighted
before the first workflow step executes.

### Host component contracts

`kosika.component/v1` is the supported host contract for binding a declared tool
to a local implementation artifact written in any language. It adds no new
Kosika syntax. A bound tool uses source `component:<component-id>`; its name,
operation, access label, generated input schema, and generated output schema MUST
exactly match the contract before execution.

The contract pins:

- semantic component version and tool identity;
- exact input and output JSON schemas;
- required effect and capability names;
- declared error codes and retryability;
- termination mode and deadline; and
- the relative implementation artifact, SHA-256 identity, and
  `json-stdio/v1` protocol.

The host MUST grant every contract effect and capability explicitly. It chooses
the runtime executable separately; the contract cannot grant one. Before each
call, the host verifies that the artifact remains inside the component root and
still matches its digest. Requests and responses are finite JSON objects,
limited to 1 MiB, and validated at both the component and Kosika tool
boundaries. Only declared component error codes are accepted. The v1
cancellation mode is `terminate`: the host kills a process that exceeds its
bounded deadline.

This contract verifies declaration alignment, host grants, bytes, protocol,
schemas, errors, and timeout behavior. It does not prove what arbitrary process
code does and is not an OS sandbox. Remote components, in-process native ABIs,
artifact signatures, and package distribution remain unsupported.

## Workflows

```ebnf
workflow = "workflow" identifier
           "(" [ record-parameter { "," record-parameter } ] ")"
           "->" record-type
           "{" { step } [ "require" "approval" ] "}" ;

step = "step" identifier "{"
       "let" identifier [ ":" type ] "=" call
       "return" identifier
       "}" ;

call = identifier "(" named-arguments ")"                 (* function *)
     | "call" identifier "(" named-arguments ")"          (* workflow *)
     | "use_model" identifier "(" named-arguments ")"     (* model *)
     | "ask" identifier "(" named-arguments ")"           (* agent *)
     | "execute" identifier "." identifier
       "(" named-arguments ")" ;                           (* agent.tool *)
```

- Workflow parameters and outputs MUST be record types.
- A workflow contains at least one uniquely named step.
- Steps execute sequentially. Each step binds exactly one result and returns
  that same binding; later steps may use earlier bindings.
- The final step result MUST have the workflow output type.
- Workflow calls are acyclic and statically bounded to depth 32.
- Arbitrary branching and loops belong in pure functions, not at workflow
  level.
- `execute agent.tool(...)` is valid only when the tool appears in the agent's
  `can` list and its input/output types match.
- `ask agent(...)` runs the bounded agent loop against the agent's model and
  declared tools, then validates the final record.

A direct `execute agent.tool(...)` call whose tool has `access: execute`
requires the containing workflow to declare `require approval`. That
declaration is necessary but does not approve anything. At runtime, every
`access: execute` effect, including one selected by an agent during `ask`, also
requires a host-provided `ExecutionSession` backed by a matching verified
approval record. The runtime
claims the exact action and arguments before the effect and journals completion
for safe resume. Approval actor strings are record labels, not authenticated
human identity; authentication is a host responsibility.

## Execution semantics

`run(program, workflow, ...)` checks the whole program before execution.

- The supplied input object MUST contain exactly the selected workflow's
  parameters, and every value is validated against its closed record schema.
- Function arguments, model inputs/results, tool inputs/results, agent
  transcripts, nested workflow results, and the final output are schema
  validated at their trust boundaries.
- Pure expressions evaluate deterministically and left to right. `and` and
  `or` short-circuit.
- Workflow steps execute sequentially. A failed operation stops the run; there
  is no language-level implicit retry.
- Values recorded at external boundaries MUST be finite JSON values.
- Whole-run limits are cooperative. The default host limits are 10,000
  function calls, 10,000 workflow steps, 64 model calls, and 256 tool calls,
  with no deadline. Hosts MAY lower or raise each count from 1 through
  1,000,000 and MAY set a positive deadline.
- Cancellation, deadline, and budget checks occur between language operations
  and provider/tool calls. They do not preempt an in-flight host effect.

The Python builder `kosika.sdk` is a **Host API**: it renders `.kos` source and
passes it through the same parser and checker, so it adds no syntax or
semantics.

The bounded read-only DAG executor, HTTP/JSON/SQLite adapters, live provider
adapters, and retry helpers are **Host API** features. They do not add syntax
or parallel execution semantics to `.kos`.

## Trace and replay semantics

A successful run may write newline-delimited JSON. New runs emit trace version
`5`; offline replay accepts versions `1` through `5`.

Version 5 records:

- Program hash, selected workflow, exact inputs, and output schema.
- Ordered step start, call, return, and completion events.
- Nested workflow event lists.
- Function call/return events, including function calls evaluated inside
  arguments and function bodies.
- Model, agent, and tool input/output schemas and recorded outcomes.
- Final result and cooperative execution counters.

`replay(trace)` is **validated outcome replay**:

- It performs no model, tool, MCP, network, or source-code execution.
- It requires one completed run with ordered, matched events.
- It recursively validates nested workflows and function events.
- It validates recorded arguments and results against recorded schemas.
- It validates model content against its recorded JSON result and validates
  agent transcripts.
- It returns the final recorded result only after those checks pass.

Replay does not prove that a trace came from trusted source, that the program
hash identifies approved code, or that events were not rewritten consistently.
Traces are not signed or cryptographically chained in `0.1.0`. Authenticity
and storage integrity are host responsibilities.

## Executable conformance examples

These examples are part of the supported contract:

| Example | Contract behavior |
| --- | --- |
| `examples/hello_world.kos` | records, pure function, workflow; returns `{"text": "Hello, world!"}` |
| `examples/typed_input_output.kos` | typed input, arithmetic, comparison; returns `{"score": 0.87, "accepted": true}` for `examples/typed_input.json` |
| `examples/closures.kos` | nested closures, `ref`, higher-order calls; returns `{"value": 115}` |
| `examples/recursion_match.kos` | bounded direct/mutual recursion and exhaustive match; returns factorial 120, parity `odd`, classification `zero` |
| `examples/composed/main.kos` | explicit local modules plus a fixture-bound model; returns the first `reader` record in `responses.json` |

Run the contract suite:

```text
python -m unittest tests/test_language_contract.py
```

Run representative examples through the public CLI:

```text
python -m kosika.cli check examples/hello_world.kos
python -m kosika.cli run examples/hello_world.kos --trace hello.jsonl
python -m kosika.cli replay hello.jsonl

python -m kosika.cli run examples/typed_input_output.kos \
  --input examples/typed_input.json --trace typed.jsonl

python -m kosika.cli run examples/composed/main.kos \
  --input examples/composed/inputs.json \
  --model-fixture examples/composed/responses.json \
  --trace composed.jsonl
```

## Tooling-only experimental and unsupported surfaces

The parser recognizes some syntax outside the supported runtime contract:

- `swarm` declarations are parsed, checked, and rendered in design views.
  `run` does not accept a swarm. The **Host API** `kosika.swarm.run_swarm`
  (`kosika swarm`) runs a swarm's declared workflow once per round, up to
  `max_rounds` or until two consecutive rounds return the same result, under
  one ledger-claimed approval bound to the swarm's plan hash and one shared
  execution budget. There is no member messaging, scheduling policy, shared
  state between rounds, or consensus. The `swarm` syntax itself remains
  **Tooling-only experimental**.
- Inline anonymous `fn(...) -> T { expression }` values are parsed and may pass
  checking when non-capturing, but the runtime does not execute them.
- `try { ... } retry N` is parsed and type-checked, but the runtime does not
  execute it.
- Method calls outside the built-in set above are rejected by the checker.

None of those forms is supported by `kosika-language/0.1.0`. Supported function
values use named declarations plus `ref`; supported retry behavior must be
implemented by an explicit host policy rather than `.kos` syntax.

Also unsupported:

- Exceptions, user-defined generics, unions, optional types, and implicit
  numeric/text coercions other than `Text + Number`.
- Import aliases, package registries, remote imports, and dynamic loading.
- Language-level parallel workflow steps or executable multi-agent consensus.
- A language guarantee that any live model endpoint is available.
- Native in-process component ABIs, remote/cloud execution, artifact signatures,
  signed traces, and authenticated approval identity.

The larger goals in `KOSIKA_VISION.md` are **Planned** unless this document
explicitly promotes them into the supported contract.

## Product naming

- **Kosika Language:** this source and execution contract.
- **Kosika Core:** parser, checker, runtime, and trace validator.
- **Kosika Bridge:** MCP and host capability adapters.
- **Kosika Agent Loop:** bounded provider-neutral model/tool interaction.
- **Kosika Design:** deterministic structural and observed-execution views.
- **Kosipack:** planned package manager.

The current implementation is Python-hosted. Cross-language local process
components use the separately versioned `kosika.component/v1` host contract;
the language grammar and runtime remain `kosika-language/0.1.0`.
