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
type          = identifier { "[]" | "{}" | "?" } | function-type ;
function-type = "fn" "(" [ parameter { "," parameter } ] ")" "->" type ;
parameter     = identifier ":" type ;
```

- An identifier type MUST name a primitive or declared record.
- `T[]` is a homogeneous array.
- `T{}` is a map from `Text` keys to `T` values, in insertion order. The `{}`
  MUST touch the type (`Number{}`), which is what tells it from a body brace.
- `T?` is an optional `T`: either a `T` or `null`. A `T` or `null` may be
  stored where `T?` is expected, never the reverse. A type is optional at most
  once, and `Null?` is not a type. In JSON an optional field is still required
  and carries `null` when absent.
- Suffixes combine left to right: `Text[]{}` maps keys to lists of text and
  `Item[]?` is an optional list.
- A choice whose branches are `T` and `null` has type `T?`. `break` and
  `continue` produce no value, so a branch containing one takes the type of
  the other branch.
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
- MAY also return early. `return value` written inside an `if`, a `match`
  arm, or a loop body leaves the innermost enclosing function at once with
  that value, which MUST have the function's output type and is validated
  like the final one. An early return produces no value where it stands, so
  the branch holding it takes the type of the other branch.

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
- `else if` chains: `if a { x } else if b { y } else { z }`.
- `if condition { value }` with no `else` evaluates to `null` when the
  condition is false, so as a value it has type `T?`. As a statement it simply
  does nothing otherwise.
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
| any array | `append(item: T)`, `concat(other: T[])`, `reverse()`, `slice(start: Number, end: Number)` | a new array of the same type; the receiver is never changed. A result over 4,096 elements fails the run |
| `Text` | `slice(start: Number, end: Number)` | `Text` from position `start` up to, not including, `end` |
| `Number` | `to_text()` | `Text`, formatted as in `Text + Number` |
| `Number` | `min(other: Number)`, `max(other: Number)` | the smaller or larger of the two |
| `Text` | `to_number()` | `Number?`: the decimal number the trimmed text spells, else `null` |
| any array | `first()`, `last()` | `T?`, `null` for an empty array |
| `Number[]`, `Text[]` | `sort()` | a new array in ascending order; Text sorts by character code |
| array of `Text`, `Number`, or `Bool` | `unique()` | a new array keeping the first of each equal value |
| `Number[]` | `sum()` | `Number`, `0` for an empty array |
| `Number[]` | `min()`, `max()`, `average()` | `Number?`, `null` for an empty array |
| array of records | `sort_by(field: "name")` | a new array ordered by a `Number` or `Text` field; equal elements keep their order |
| array of records | `sum_by(field: "name")` | `Number`, the total of a `Number` field |
| array of records | `min_by(field: "name")`, `max_by(field: "name")` | `T?`, the first element with the smallest or largest field |
| array of records | `group_by(field: "name")` | `T[]{}`, elements grouped under each value of a `Text` field |

A `field:` argument MUST be a text literal naming a field of the element
record. The checker resolves it, so a misspelled or wrongly typed field is a
check error rather than a run failure. To sort descending, follow `sort()` or
`sort_by(...)` with `reverse()`.
| any `T?` | `or(default: T)` | `T`: the value, or the default when absent |
| any `T?` | `exists()` | `Bool` |
| any `T?` | `value()` | `T`; fails the run when absent |
| any `T{}` | `get(key: Text)` | `T?` |
| any `T{}` | `has(key: Text)` | `Bool` |
| any `T{}` | `set(key: Text, value: T)`, `remove(key: Text)` | a new map; the receiver is never changed. A result over 4,096 entries fails the run |
| any `T{}` | `keys()`, `values()`, `length()` | `Text[]`, `T[]`, `Number` |

An optional value has only `or`, `exists`, and `value`: its own methods and
fields are reached after one of those, or after a test proves it present.

A test of `name.exists()` narrows `name` from `T?` to `T` wherever the test is
known to have passed:

- in the true branch of `if name.exists() { ... }`, and in the `else` branch
  of `if not name.exists()`;
- with `and`, for every name tested: `if a.exists() and b.exists() { a + b }`;
- on the lines after a guard that always leaves when the value is absent:
  `if not name.exists() { return ... }` (or `break`, or `continue`);
- in the body of `for x in xs where x.exists() { ... }`.

Only a plain name declared with `let`, a parameter, or a loop variable is
narrowed. A `var` is not, because it may be set back to `null`; a field or an
index is not, so bind it to a `let` first. `exists`, `value`, and `or` remain
valid on a narrowed name. Narrowing is a check-time rule only and does not
change what a program computes.

A braced block holds declarations, assignments, and expressions in any order.
An expression followed by another line is a statement whose value is
discarded; the last expression, if any, is the block's value.

A map has no fields. A map literal is
written like a record literal, with quoted or bare keys (`{"north": 4}`), and
needs a map type from its context; `{}` is the empty map.
| array of `Text`, `Number`, or `Bool` | `contains(item: T)` | `Bool`, using `==` |
| `Number` | `floor()`, `ceil()`, `round()` | whole `Number`; `round` rounds halves up |

`slice` positions MUST be whole numbers with `0 <= start <= end <= length`;
anything else fails the run.

An unknown method or wrong argument name is a check error
(`check/unknown-method`, `check/method-arguments`). A parenthesised literal is
needed to call a method on a number literal: `(2.5).round()`.

### Bounded loops

```kosika
for item in values limit 32 { expression }
repeat 8 times { expression }
```

- `for item in values where condition { expression }` skips elements whose
  `Bool` condition is false, so one loop both filters and maps. The limit still
  counts every element of the input array.
- When the loop's array type is known from context, the body is typed as its
  element, so it may be a record literal:
  `let rows: Row[] = for w in words { {name: w} }`.
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

### General models and thinking steps

`model name`, with no body or with only `deployment` and `parameters`, declares
a general model. Its input is the built-in record `Prompt {text: Text}` and its
output `Reply {text: Text}`, so `use_model name(text: "...")` asks it directly.

A thinking step is a workflow step that asks a model and binds a typed answer:

```ebnf
think = "classify"  expression "into" expression [ "using" identifier ]
      | "extract"   identifier "from" expression [ "using" identifier ]
      | "summarize" expression                   [ "using" identifier ]
      | "decide"    expression "given" expression [ "using" identifier ] ;
```

| Step | Operands | Result record |
| --- | --- | --- |
| `classify text into choices` | `Text`, `Text[]` | `Choice {label: Text, confidence: Number}` |
| `extract Record from text` | a declared record, `Text` | that record |
| `summarize text` | `Text` | `Summary {text: Text}` |
| `decide question given text` | `Text`, `Text` | `Decision {answer: Bool, confidence: Number, reason: Text}` |

- `using name` selects a declared model. It MAY be omitted only when the
  program declares exactly one model.
- A thinking step is lowered to an ordinary typed model call before checking,
  against a generated model named `<model>/<verb>` (or
  `<model>/extract/<Record>`). The generated model uses the host binding,
  deployment, and parameters of the declared model; it has no binding of its
  own, so a thinking step grants nothing a `use_model` call would not.
- Beyond its record shape, a `classify` answer MUST carry a label that is one
  of the given choices, and `classify` and `decide` answers MUST carry a
  confidence from 0 through 1. Any other answer fails the run. A confidence is
  the model's own estimate, not a measured probability.
- The result records, `Prompt`, and `Reply` are built in and added only to
  programs that use them. A program MAY declare a record with one of those
  names only with identical fields.
- A thinking step is a step, not an expression: it cannot appear inside a `fn`.
- Budgets, traces, and replay treat a thinking step as one model call.
- `classify`, `extract`, and `decide` MAY end `with evidence`, written before
  any `using`: `extract Invoice from ticket.text with evidence`. The model is
  then asked for quotes as well as its answer, and every quote MUST appear in
  the text the step was given. Matching ignores letter case, runs of
  whitespace, and straight versus curly quotation marks; a paraphrase is not
  a quote. What must be quoted:
  - `extract`: one quote for each field the model fills. A field left `null`
    or empty needs none, so fields that may be missing SHOULD be optional.
  - `classify`: at least one quote.
  - `decide`: at least one quote for a `true` answer; a `false` answer may
    have none.
  An answer whose evidence is missing or not found fails the step like any
  other broken answer, so an `otherwise` fallback runs. The step's value is
  the plain record; the quotes are kept in the trace, and replay checks them
  again against the recorded input. `summarize` cannot give evidence, and
  neither can `extract` into a record with its own field named `evidence`.
- Evidence proves the quoted words were in the input. It does not prove that
  they support the answer.

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

### System tools: shell and files

A tool whose source is `system` asks for access to the machine the program
runs on. It adds no new statement: it is a tool, used with `execute`.

```kosika
tool run_tests {
  source system "shell"
  operation: "python"
  access: execute
  input: ShellArguments
  output: ShellResult
}
```

| Source | `operation` | Input record | Output record |
| --- | --- | --- | --- |
| `system "shell"` | the one program to start | `{args: Text[]}` | `{exit_code: Number, stdout: Text, stderr: Text}` |
| `system "files"` | `read` | `{path: Text}` | `{text: Text}` |
| `system "files"` | `write` | `{path: Text, text: Text}` | `{bytes: Number}` |
| `system "files"` | `list` | `{path: Text}` | `{names: Text[]}` |

- A system tool MUST declare `access: execute`, and its records MUST have
  exactly the fields above (`check/invalid-system-tool`). A workflow that
  executes one therefore MUST state `require approval`.
- Declaring a system tool grants nothing. The host binds files tools only when
  it grants a folder, and shell tools only when it also allows shell. An
  unbound system tool fails the run before its first step.
- Every call needs its own approval for that exact tool and arguments, through
  the same session and ledger as any other `execute` tool.
- A shell tool starts only the program named in `operation`, with `args`
  passed as a list and never interpreted by a shell, inside the granted
  folder, for at most 60 seconds, with output cut at 64 KiB per stream.
- File paths are relative to the granted folder. A path that is absolute,
  contains `..`, or passes through a symlink is refused. Files are UTF-8 text
  of at most 1 MiB.
- This is not an operating-system sandbox. An approved program runs with the
  authority of the person who approved it.

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
       "}"
     | identifier "=" call [ "otherwise" call ] ;          (* one-line step *)

call = identifier "(" named-arguments ")"                 (* function *)
     | "call" identifier "(" named-arguments ")"          (* workflow *)
     | "use_model" identifier "(" named-arguments ")"     (* model *)
     | "ask" identifier "(" named-arguments ")"           (* agent *)
     | "execute" identifier "." identifier
       "(" named-arguments ")" ;                           (* agent.tool *)
```

- A one-line step `name = call` is exactly
  `step name { let name = call  return name }`: the step and its result share
  the name and the result type is inferred. Both forms parse to the same tree.
- Named arguments have a short form. An argument written as a bare variable or
  a field path takes its name from the last identifier: `f(order)` is
  `f(order: order)` and `f(question.text)` is `f(text: question.text)`. Any
  other expression MUST be named (`parse/unnamed-argument`). The short form
  applies to every call, including methods.
- A call with exactly one argument MAY leave its name out, whatever the
  expression: `double(n + 1)`, `use_model assistant("hi")`,
  `words.join("-")`. It binds to the callee's only parameter. If the callee
  has more or fewer than one parameter, or is a function value whose
  parameters are not known where the call is written, the bare-variable rule
  above applies, and any other expression is rejected
  (`check/unnamed-argument`).
- A program with `fn main` and no `workflow main` is given the workflow
  `workflow main(<same parameters>) -> <same result> { result = main(...) }`,
  provided every parameter and the result are records.
- A step MAY name a fallback: `label = first(...) otherwise second(...)`.
  The long form takes `otherwise` after its call too. If the first call fails
  in a recoverable way, the step's partial events are discarded and the second
  call runs in its place; the step's `step_start` event then records
  `recovered_from` with the failure's type and message. Recoverable means a
  failure of the work itself: an unusable model answer, a failed or wrongly
  shaped tool result, or a run-time failure in a function. Budgets, deadlines,
  cancellation, missing approval, and missing host bindings are never
  recovered. Both calls MUST produce the same type, both are checked and
  authorized before the run starts, and neither may be an `access: execute`
  tool, because a failed action may still have happened.
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

- Exceptions and `try`/`catch` (a step's `otherwise` is the only recovery
  form), user-defined generics, unions other than `T?`, and implicit
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
