# Kosika for AI authors

Everything needed to write a correct Kosika program. Kosika is its own
language. Do not use Python, JavaScript, or Rust syntax.

## Shape of a program

A program is a list of declarations in any order: `record`, `fn`, `model`,
`tool`, `agent`, `workflow`, `swarm`. No semicolons. `//` starts a comment.
The entry point is `workflow main`. A program that only computes can use
`fn main(input: In) -> Out` instead and needs no workflow.

```kosika
record Person { name: Text }
record Greeting { text: Text }

fn greet(person: Person) -> Greeting {
  return {text: "Hello, " + person.name + "!"}
}

workflow main(person: Person) -> Greeting {
  greeting = greet(person)
}
```

## Types

- Primitives: `Text`, `Number`, `Bool`, `Null`. There is no `String`, `Int`,
  or `Float`.
- Arrays: `Number[]`, `Person[]`.
- Maps (dictionaries) from Text keys: `Number{}`, written with no space
  before `{}`. A literal is `{"north": 4, "south": 2}`; `{}` is empty.
- Optional values: `Text?` is a Text or `null`. Use `Text?` for anything that
  may be missing. To use the value write `x.or(default: "none")`, test it with
  `x.exists()`, or demand it with `x.value()` (fails the run if absent).
- Records: `record Name { field: Type other: Type }`. Every field is
  required. Record names start with a capital letter.
- A record value is written `{field: value, other: value}`. An array value is
  `[1, 2, 3]`. Read a field with `value.field` and an element with `list[0]`.

## Functions

```kosika
fn total(numbers: Numbers) -> Total {
  var sum = 0
  var count = 0
  for value in numbers.values {
    sum = sum + value
    count = count + 1
  }
  return {sum: sum, count: count}
}
```

- A function is pure: it cannot call a model, tool, agent, or workflow.
- **Arguments are named**: `area(width: 2, height: 3)`. Two short forms:
  a call with exactly one argument needs no name (`double(n + 1)`,
  `words.join("-")`, `stock.get("pen").or(0)`), and a variable or field with
  the same name as the parameter needs none (`route(label, refund)`,
  `ask_about(question.text)`).
- The body is `let`/`var` declarations and statements, run in the order they
  are written, then a `return expression` as the last line. A function may
  also return early from inside an `if` or loop:
  `if n < 0 { return {text: "negative"} }`.
- `let` cannot be reassigned. Use `var` for a value you assign later.
- Operators: `+ - * / %`, `< <= > >=`, `== !=`, `and`, `or`, `not`.
  `Text + Text` and `Text + Number` join text. There is no `&&`, `||`, or `!`.
  `/` always gives a fraction (`7 / 2` is `3.5`); use `(7 / 2).floor()` for a
  whole number, for example as an array index.
- There are no free built-in functions (no `len(x)`), but there are built-in
  methods, all with named arguments:

| On | Methods |
| --- | --- |
| `Text` | `length()`, `upper()`, `lower()`, `trim()`, `contains(part: t)`, `starts_with(prefix: t)`, `ends_with(suffix: t)`, `split(separator: t)`, `slice(start: a, end: b)` |
| any array | `length()`, `append(item: x)`, `concat(other: xs)`, `reverse()`, `slice(start: a, end: b)`; `contains(item: x)` for arrays of Text, Number, or Bool |
| `Number` | `floor()`, `ceil()`, `round()`, `to_text()`, `min(n)`, `max(n)` |
| `Number[]` | `sum()`, `sort()`; `min()`, `max()`, `average()` give `Number?` |
| `Text[]` | `sort()`, `join(separator: t)` |
| scalar arrays | `unique()` |
| arrays of records | `sort_by(field: "price")`, `sum_by(field: "price")`, `min_by(field: "price")`, `max_by(field: "price")` (give `T?`), `group_by(field: "city")` (gives `T[]{}`) |
| any `T{}` map | `get(key: k)` gives `T?`, `has(key: k)`, `set(key: k, value: v)`, `remove(key: k)`, `keys()`, `values()`, `length()` |
| any `T?` | `or(default: v)`, `exists()`, `value()` |

  The `field:` argument is always a quoted field name of the record. There
  is no descending sort: use `.sort().reverse()`. To call a method on a `for`
  result, wrap the loop in parentheses: `(for x in xs { x.name }).first()`.

  Also `text.to_number()` gives `Number?`, and `items.first()` and
  `items.last()` give `T?`. `set` and `remove` return a new map, like `append`.

  Arrays are never changed in place. `append` returns a new array, so write
  `items = items.append(item: x)` on a `var`.

## Choosing and repeating

```kosika
// if is an expression with braces around each branch. Without an else it
// gives an optional value (null when false); as a statement it does nothing.
let label: Text = if score >= 50 { "pass" } else { "fail" }

// else if chains are allowed.
let grade: Text = if score >= 90 { "A" } else if score >= 75 { "B" } else { "C" }

// match on Text, Number, or Bool. End with `case _`.
let reply: Text = match name {
  case "start" { "Starting" }
  case _ { "Unknown" }
}

// for as an expression builds a new array. `where` keeps only some elements.
let doubled: Number[] = for value in numbers.values { value * 2 }
let big: Number[] = for value in numbers.values where value > 10 { value }

// repeat runs a fixed number of times (a literal from 1 to 512).
repeat 3 times { sum = sum + 1 }
```

There is no `while`. A function may call itself only with a declared bound:

```kosika
fn factorial(n: Number) -> Number recurse 16 {
  return if n <= 1 { 1 } else { n * factorial(n: n - 1) }
}
```

## Models

A model is declared by its input and output records. A call passes the
**fields of the input record** as named arguments.

```kosika
record Question { text: Text }
record Answer { text: Text }

model assistant {
  input: Question
  output: Answer
  deployment: "local-model"        // optional
  parameters: {                    // optional
    temperature: 0.2
    max_output_tokens: 200
  }
}

workflow main(question: Question) -> Answer {
  answer = use_model assistant(question.text)
}
```

## Thinking steps

For common model tasks, declare a model by name only and use a thinking step.
Each is one workflow step with a built-in, typed result.

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

| Step | Result |
| --- | --- |
| `classify text into choices` | `Choice {label: Text, confidence: Number}`; the label is always one of the choices |
| `extract Record from text` | your own record `Record` |
| `summarize text` | `Summary {text: Text}` |
| `decide question given text` | `Decision {answer: Bool, confidence: Number, reason: Text}` |

- Do not declare `Choice`, `Summary`, or `Decision` yourself; they are built in.
  Use them as parameter types: `fn route(label: Choice, refund: Decision)`.
- With more than one model, end the step with `using model_name`.
- Thinking steps go in a workflow only, never inside a `fn`.

## Tools and agents

A tool is something outside the program. Only an agent that lists the tool in
`can` may use it.

```kosika
tool get_weather {
  source mcp "weather"
  operation: "current"
  access: observe          // observe = read-only. execute = changes the world.
  input: City              // optional; omit for a tool with no input
  output: Weather
}

agent forecaster {
  model: assistant         // needed only if the agent is used with `ask`
  instructions: "Be brief."
  can: [get_weather]
  cannot: [shell, delete_files]
  max_steps: 4
}
```

Note `source mcp "name"` has no colon. Every other line does.

For the machine itself use `source system "shell"` or `source system "files"`.
These must be `access: execute` with these exact records:

| Source and operation | Input record fields | Output record fields |
| --- | --- | --- |
| `system "shell"`, operation is the program name, e.g. `"python"` | `args: Text[]` | `exit_code: Number`, `stdout: Text`, `stderr: Text` |
| `system "files"`, operation `"read"` | `path: Text` | `text: Text` |
| `system "files"`, operation `"write"` | `path: Text`, `text: Text` | `bytes: Number` |
| `system "files"`, operation `"list"` | `path: Text` | `names: Text[]` |

Paths are relative to a folder the person running the program grants. A human
approves every call.

## Workflows

```kosika
workflow main(city: City) -> Report {
  weather = execute forecaster.get_weather(city.name)
  report = build_report(weather)
}
```

- A workflow's parameters and its result type are always records.
- **Each step is one line: `name = <one call>`.** The name holds the result.
  No other statements, no expressions, no `if`. (The older long form
  `step s { let name: Type = <call>  return name }` means the same.)
- The call is one of:
  - `some_function(arg: value)`
  - `use_model model_name(field: value)`
  - `ask agent_name(field: value)` runs the agent's model with its tools; the
    arguments are the fields of that model's input record, and the result is
    that model's output record
  - `execute agent_name.tool_name(field: value)` calls one tool directly
  - `call other_workflow(param: value)`
- Later steps can use the names bound by earlier steps and the parameters.
- The last step's result must have the workflow's result type.
- Put any logic in a `fn` and call it from a step.
- A step can name a fallback for when its call fails, such as an unusable
  model answer: `label = classify t.text into ["a", "b"] otherwise
  keyword_label(t)`. Both calls must return the same type. Not allowed on
  `access: execute` tools.
- If a step executes a tool whose access is `execute`, the workflow must end
  with the line `require approval` after its last step.

## Swarms

```kosika
swarm Panel {
  agents: [writer]
  workflow: refine
  max_rounds: 2
  require approval
}
```

`require approval` is mandatory, and every listed agent must be used by the
workflow.

## Checklist before answering

1. Name every argument unless it is a same-named variable or field.
2. Only `Text`, `Number`, `Bool`, `Null`, arrays, and your records as types.
3. Each workflow step is one line: `name = one_call(...)`.
4. One `return` at the end of each `fn`; logic lives in `fn`, not in steps.
5. Only the built-in methods listed above; no semicolons; every function
   ends with a `return`.
