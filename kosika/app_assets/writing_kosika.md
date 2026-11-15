# Writing Kosika

Kosika is its own language. It is not Python, JavaScript or Rust, and code
written from habit in one of those will not check. Read this before you
write a line. Files end in `.kos`.

## The shape of a file

```kosika
// A comment runs to the end of the line.

record Point { x: Number y: Number }

fn shout(text: Text) -> Text {
  return text.upper() + "!"
}

fn total(numbers: Number[]) -> Number {
  var sum = 0
  for n in numbers { sum += n }
  return sum
}

test "shout makes it loud" {
  expect shout("hi") == "HI!"
  expect total([1, 2, 3]) == 6
}
```

- No semicolons. No commas between the fields of a `record` declaration.
- A function is `fn NAME(arg: Type, ...) -> Type { ... }` and must end with
  `return`. Every parameter and the result have a type.
- A test is `test "what it shows" { ... }`. Each check is `expect` followed
  by a condition. Tests sit in the same file as what they test.

## Types

`Text`, `Number`, `Bool`, a record's name, and a list of any of them
written `Text[]`, `Number[]`, `Point[]`. There is one number type; `3` and
`3.5` are both `Number`. `true` and `false` are the two `Bool` values.

## Inside a function

```kosika
fn describe(n: Number) -> Text {
  let half = n / 2
  var label = "small"
  if n > 100 { label = "large" }
  else if n > 10 { label = "medium" }
  return f"{n} is {label}, half is {half}"
}

fn evens(numbers: Number[]) -> Number[] {
  var found: Number[] = []
  for n in numbers {
    if n % 2 == 0 { found = found.append(n) }
  }
  return found
}

fn longest(words: Text[]) -> Text {
  var best = ""
  for word in words {
    if word.length() > best.length() { best = word }
  }
  return best
}
```

- `let` names a value once; `var` names one that changes. An empty list
  needs its type: `var found: Number[] = []`.
- Conditions use `and`, `or`, `not`, and `==`, `!=`, `<`, `<=`, `>`, `>=`.
- `if cond { ... } else if cond { ... } else { ... }`. Braces always.
- `for item in list { ... }` and `while cond { ... }`.
- `if` is also a value: `let size = if n > 10 { "big" } else { "small" }`.
- Text is joined with `+`, and a number may be joined to a text the same
  way. `f"..."` puts values into a text: `f"{name} is {age}"`.
- Lists do not change in place. `list.append(x)` gives a new list, so write
  `found = found.append(x)`. Two lists are joined with `a.concat(b)`, not
  `+`.
- A record value is written `{x: 1, y: 2}`, with commas, and its fields are
  read as `p.x`. When the type cannot be told, name it:
  `let origin: Point = {x: 0, y: 0}`.

## Methods you can use

Called with a dot and brackets, always: `text.length()`, never
`len(text)` or `text.length`.

- `Text`: `length()`, `upper()`, `lower()`, `trim()`, `contains(part)`,
  `starts_with(prefix)`, `ends_with(suffix)`, `replace(old, new)`,
  `split(separator)`, `words()`, `lines()`, `chars()`, `take(count)`,
  `drop(count)`, `slice(start, end)`, `is_empty()`, `to_number()`,
  `index_of(part)`, `pad_left(width, fill)`, `pad_right(width, fill)`.
- `Number`: `floor()`, `ceil()`, `round()`, `abs()`, `sqrt()`,
  `power(exponent)`, `min(other)`, `max(other)`, `is_whole()`,
  `to_text()`.
- Any list: `length()`, `append(item)`, `concat(other)`, `contains(item)`,
  `first()`, `last()`, `take(count)`, `drop(count)`, `reverse()`,
  `slice(start, end)`, `is_empty()`, `index_of(item)`, and `list[0]` for
  the item at a position.
- `Number[]`: `sum()`, `sort()`. `Text[]`: `join(separator)`, `sort()`.

`first()`, `last()`, `to_number()` and `index_of(...)` may find nothing, so
they give a value that may be absent. Give the value to use instead with
`.or(...)`: `words.first().or("")`, `text.to_number().or(0)`.

There is no `len`, `print`, `range`, `str`, `int`, `null`, `None`, `++`,
`elif`, `function`, `def`, `class`, `import X from`, or `=>`.

## A list from a list

```kosika
fn doubled(numbers: Number[]) -> Number[] {
  return for n in numbers { n * 2 }
}

fn long_words(words: Text[]) -> Text[] {
  return for word in words where word.length() > 3 { word }
}
```

## When you change a file

Keep every `record`, `fn` and `test` the file already has, unless the task
says to remove or rename it. A new function needs a new `test` in the same
file. A renamed function is renamed everywhere it is used, tests included.
