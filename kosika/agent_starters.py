"""Agent starting points for `kosika new --kind`.

Each is a complete, working agent with its own tests. They are kept as
text so that a new project is an exact copy the author then changes.
"""

from __future__ import annotations

#: kind -> (what it is, how to run it on a model, main.kos, input.json)
AGENT_STARTERS: dict[str, tuple[str, str, str, str]] = {
    "support": (
        "A support desk that remembers each customer between runs.",
        "kosika run main.kos --input input.json --model MODEL --memory memory",
        """\
// A support desk that remembers each customer between runs.
//
// How to read this file, top to bottom:
//   records   the shapes of the data
//   model     a model the program may ask (you choose which one when you run it)
//   memory    what it keeps between runs
//   fn        plain code: exact, and the same every time
//   workflow  the steps, in order
//   test      checks that need no model, because the answers are scripted

// What comes in, what is remembered, and what goes out.
record Ticket { customer: Text text: Text }
record History { tickets: Number last_queue: Text }
record DeskReply { queue: Text urgent: Bool returning: Bool tickets: Number message: Text }

model assistant
// One History is kept for each customer, under the customer's name.
memory customers of History

// Count this ticket. `known` is what memory gave back: it says whether
// anything was found, and holds the value if so.
fn updated(known: CustomersRecalled, label: Choice) -> History {
  let before = if known.found { known.value.value().tickets } else { 0 }
  return {tickets: before + 1, last_queue: label.label}
}

// Used only if the model's label is unusable twice in a row.
fn fallback_label(ticket: Ticket) -> Choice {
  return {label: "other", confidence: 0}
}

// Put the answer together. The model wrote the summary; code does the rest.
fn reply(ticket: Ticket, known: CustomersRecalled, history: History, label: Choice, urgent: Decision, draft: Summary) -> DeskReply {
  let greeting = if known.found { "Welcome back, " } else { "Hello, " }
  return {
    queue: label.label,
    urgent: urgent.answer,
    returning: known.found,
    tickets: history.tickets,
    message: greeting + ticket.customer + ". " + draft.text
  }
}

// Each line is one step: a name, then what it does. A later step may use
// the result of an earlier one by its name.
workflow main(ticket: Ticket) -> DeskReply {
  // The most this run may ask of the model and of the clock.
  budget 12 model calls, 300 seconds
  known   = recall customers under ticket.customer
  label   = classify ticket.text into ["billing", "bug", "account", "other"] retry 1 otherwise fallback_label(ticket)
  urgent  = decide "Is the customer unable to use the product at all right now?" given ticket.text with evidence retry 1
  draft   = summarize ticket.text
  history = updated(known, label)
  saved   = remember history in customers under ticket.customer
  result  = reply(ticket, known, history, label, urgent, draft)
}

// `answers` gives the model's replies in the order it will be asked, so the
// test runs in a moment and always the same way.
test "a new customer is greeted and remembered" {
  answers assistant: [
    {label: "bug", confidence: 0.9},
    {answer: true, confidence: 0.9, reason: "They cannot open the app.", evidence: ["cannot open the app"]},
    {text: "The app will not open since the update."}
  ]
  first = main({customer: "Ada", text: "I cannot open the app since the update."})
  expect first.returning == false
  expect first.tickets == 1
  expect first.urgent == true
  expect first.message.starts_with("Hello, Ada")
}

test "the second ticket knows the customer" {
  answers assistant: [
    {label: "bug", confidence: 0.9},
    {answer: true, confidence: 0.9, reason: "r", evidence: ["cannot open the app"]},
    {text: "The app will not open."},
    {label: "billing", confidence: 0.8},
    {answer: false, confidence: 0.7, reason: "r", evidence: []},
    {text: "Asks for an invoice copy."}
  ]
  first  = main({customer: "Ada", text: "I cannot open the app since the update."})
  second = main({customer: "Ada", text: "Please send me a copy of my invoice."})
  expect second.returning == true
  expect second.tickets == 2
  expect second.queue == "billing"
  expect second.message.starts_with("Welcome back, Ada")
}
""",
        """\
{"ticket": {"customer": "Ada", "text": "I cannot open the app since the update. Nothing works."}}
""",
    ),
    "expense": (
        "An expense checker: the model reads the receipt, code applies the rules.",
        "kosika run main.kos --input input.json --model MODEL",
        """\
// An expense checker: the model reads the receipt, code applies the rules.
//
// The idea to notice: a model is good at reading a messy receipt and bad
// at being exact. So the model only reads, and the limits are plain code.

// A `?` after a type means the value may be missing.
record Claim { employee: Text receipt: Text }
record Receipt { merchant: Text? total: Number? category: Text? }
record Verdict { approved: Bool amount: Number category: Text reasons: Text[] }

model assistant

// What the workflow uses if the model cannot read the receipt at all.
fn unreadable(claim: Claim) -> Receipt {
  return {merchant: null, total: null, category: null}
}

// The policy is ordinary code, so it is exact and the same every time.
fn limit_for(category: Text) -> Number {
  return match category {
    case "meal" { 50 }
    case "travel" { 400 }
    case "hotel" { 250 }
    case _ { 100 }
  }
}

// Decide, and collect every reason for a refusal so the person can see why.
fn judge(receipt: Receipt, personal: Decision) -> Verdict {
  var reasons: Text[] = []
  if not receipt.total.exists() {
    return {approved: false, amount: 0, category: "unknown", reasons: ["The receipt total could not be read."]}
  }
  let total = receipt.total.value()
  let category = receipt.category.or("other")
  let limit = limit_for(category)
  if total > limit {
    reasons = reasons.append("The total " + total + " is over the " + category + " limit of " + limit + ".")
  }
  if personal.answer {
    reasons = reasons.append("It looks personal: " + personal.reason)
  }
  return {approved: reasons.length() == 0, amount: total, category: category, reasons: reasons}
}

// `with evidence` makes the model quote the receipt for what it claims, and
// the quote is checked. `retry 2` asks again if the answer is refused.
// `otherwise` says what to do if it is refused every time.
workflow main(claim: Claim) -> Verdict {
  budget 8 model calls, 300 seconds
  receipt  = extract Receipt from claim.receipt with evidence retry 2 otherwise unreadable(claim)
  personal = decide "Does this receipt include alcohol, gifts, or other personal items?" given claim.receipt with evidence retry 1
  verdict  = judge(receipt, personal)
}

test "a cheap meal is approved" {
  answers assistant: [
    {merchant: "Cafe Luna", total: 18, category: "meal", evidence: {merchant: "Cafe Luna", total: "Total 18.00", category: "Lunch"}},
    {answer: false, confidence: 0.9, reason: "Only food.", evidence: []}
  ]
  verdict = main({employee: "Ada", receipt: "Cafe Luna. Lunch menu 18.00. Total 18.00"})
  expect verdict.approved == true
  expect verdict.amount == 18
}

test "an expensive meal is refused by the code, not the model" {
  answers assistant: [
    {merchant: "Chez Max", total: 95, category: "meal", evidence: {merchant: "Chez Max", total: "Total 95.00", category: "Dinner"}},
    {answer: false, confidence: 0.9, reason: "Only food.", evidence: []}
  ]
  verdict = main({employee: "Ada", receipt: "Chez Max. Dinner for one. Total 95.00"})
  expect verdict.approved == false
  expect verdict.reasons.length() == 1
  expect verdict.reasons[0].contains("over the meal limit of 50")
}

test "a made-up total is rejected and the claim is not approved" {
  answers assistant: [
    {merchant: "Cafe Luna", total: 12, category: "meal", evidence: {merchant: "Cafe Luna", total: "Total 12.00", category: "Lunch"}},
    {merchant: "Cafe Luna", total: 12, category: "meal", evidence: {merchant: "Cafe Luna", total: "Total 12.00", category: "Lunch"}},
    {merchant: "Cafe Luna", total: 12, category: "meal", evidence: {merchant: "Cafe Luna", total: "Total 12.00", category: "Lunch"}},
    {answer: false, confidence: 0.9, reason: "Only food.", evidence: []}
  ]
  verdict = main({employee: "Ada", receipt: "Cafe Luna. Lunch menu. The total is smudged."})
  expect verdict.approved == false
  expect verdict.reasons[0] == "The receipt total could not be read."
}
""",
        """\
{"claim": {"employee": "Ada", "receipt": "THE GRAND HOTEL BAR\\nDinner set menu 62.00\\nBottle of red wine 38.00\\nTotal 100.00\\nPaid by card"}}
""",
    ),
    "notes": (
        "A meeting notes agent: the model finds the tasks, code organises them.",
        "kosika run main.kos --input input.json --model MODEL",
        """\
// A meeting notes agent: the model finds the tasks, code organises them.
//
// The model turns free text into a list of Task records. Sorting, grouping
// and counting are then done by code, which never miscounts.

// `Task[]` is a list of tasks.
record Notes { text: Text }
record Task { owner: Text what: Text days: Number }
record TaskList { tasks: Task[] }
record OwnerLoad { owner: Text count: Number soonest: Number }
record Plan { summary: Text first: Text? total: Number busiest: Text? loads: OwnerLoad[] }

model assistant

// What the workflow uses if the model finds no usable list.
fn none(notes: Notes) -> TaskList {
  return {tasks: []}
}

// `let` names a value that does not change; `var` one that does.
fn plan(found: TaskList, brief: Summary) -> Plan {
  // Soonest first, and each owner's tasks together.
  let ordered = found.tasks.sort_by(field: "days")
  let by_owner = found.tasks.group_by(field: "owner")
  var loads: OwnerLoad[] = []
  for owner in by_owner.keys().sort() {
    let mine = by_owner.get(owner).or([])
    let soonest = (for task in mine { task.days }).min().or(0)
    loads = loads.append({owner: owner, count: mine.length(), soonest: soonest})
  }
  let busiest = loads.max_by(field: "count")
  let first = ordered.first()
  return {
    summary: brief.text,
    first: if first.exists() { first.owner + ": " + first.what } else { null },
    total: found.tasks.length(),
    busiest: if busiest.exists() { busiest.owner } else { null },
    loads: loads
  }
}

// `extract` asks the model to fill in a record from text; `summarize` asks
// it for a short version.
workflow main(notes: Notes) -> Plan {
  budget 6 model calls, 300 seconds
  found  = extract TaskList from notes.text retry 2 otherwise none(notes)
  brief  = summarize notes.text
  result = plan(found, brief)
}

test "tasks are grouped by owner and ordered by deadline" {
  answers assistant: [
    {tasks: [
      {owner: "Mira", what: "Send the budget", days: 5},
      {owner: "Leo", what: "Fix the login bug", days: 1},
      {owner: "Mira", what: "Book the venue", days: 3}
    ]},
    {text: "Budget, a bug, and a venue."}
  ]
  result = main({text: "notes"})
  expect result.total == 3
  expect result.first.or("") == "Leo: Fix the login bug"
  expect result.busiest.or("") == "Mira"
  expect result.loads.length() == 2
  expect result.loads[1].soonest == 3
}

test "no tasks is a valid answer" {
  answers assistant: [{tasks: []}, {text: "Nothing to do."}]
  result = main({text: "We just chatted."})
  expect result.total == 0
  expect result.first.exists() == false
}
""",
        """\
{"notes": {"text": "Weekly sync. Mira will send the Q3 budget to finance by Friday, so in 4 days. Leo needs to fix the login bug by tomorrow. Mira also agreed to book the offsite venue within 10 days. Sam will write the release notes in 2 days."}}
""",
    ),
    "researcher": (
        "A research brief: the model reads sources and extracts claims, code checks every quote.",
        "kosika run main.kos --input input.json --model MODEL",
        """\
// A research brief: the model reads sources and extracts claims; code
// checks every quote against the source it names.
//
// The idea to notice: a model is useful at reading and bad at being
// trusted. So the model never decides whether a claim is supported. It
// names a source and copies the words that prove it; code does the
// checking and counts what could not be checked.
//
//   kosika test main.kos
//   kosika run main.kos --input input.json --model-fixture responses.json
//   kosika run main.kos --input input.json --model qwen2.5:7b

record Source { name: Text text: Text }
record Brief { question: Text sources: Source[] }
record Claim { text: Text document: Text quote: Text }
record Claims { claims: Claim[] }
record Finding { claim: Text document: Text quote: Text verified: Bool }
record Report { question: Text summary: Text findings: Finding[] documents: Number checked: Number unsupported: Number }

model assistant

// What the workflow uses if the model returns no usable list of claims.
fn none(brief: Brief) -> Claims {
  return {claims: []}
}

// The sources as the model sees them: a name, then the text.
fn bundle(sources: Source[]) -> Text {
  var text = ""
  for source in sources {
    text = text + "SOURCE: " + source.name + "\\n" + source.text + "\\n\\n"
  }
  return text
}

// The question and the sources, with one instruction the check relies on:
// a claim must name one source and copy its words exactly.
fn request_text(brief: Brief) -> Text {
  return "Question: " + brief.question + "\\n\\nSources:\\n" + bundle(brief.sources) + "For each claim, name one source exactly as written, and copy the words that support the claim without changing them. Use nothing outside these sources."
}

// Case, spacing and line breaks are ignored when a quote is checked. The
// words themselves are not.
fn normalized(text: Text) -> Text {
  return text.lower().replace("\\n", " ").words().join(" ")
}

// The text of the source with this name, or "" when the brief has no source
// by that name.
fn text_named(sources: Source[], name: Text) -> Text {
  for source in sources {
    if source.name.lower().trim() == name.lower().trim() {
      return source.text
    }
  }
  return ""
}

// One claim, checked against the source it names. The quote must be in that
// source, and must be more than a word or two.
fn check_claim(claim: Claim, sources: Source[]) -> Finding {
  let source = text_named(sources, claim.document)
  let quote = normalized(claim.quote)
  let verified = not source.is_empty() and quote.length() >= 8 and normalized(source).contains(quote)
  return {claim: claim.text, document: claim.document, quote: claim.quote, verified: verified}
}

// The report keeps unsupported claims, marked, so the reader can see what the
// model tried to say and why it was not accepted.
fn assemble(brief: Brief, found: Claims, summary: Summary) -> Report {
  let findings = for claim in found.claims { check_claim(claim, brief.sources) }
  let good = for finding in findings where finding.verified { finding }
  return {
    question: brief.question,
    summary: summary.text,
    findings: findings,
    documents: brief.sources.length(),
    checked: findings.length(),
    unsupported: findings.length() - good.length()
  }
}

workflow main(brief: Brief) -> Report {
  budget 6 model calls, 300 seconds
  found   = extract Claims from request_text(brief) retry 2 otherwise none(brief)
  summary = summarize bundle(brief.sources)
  result  = assemble(brief, found, summary)
}

// A quote is checked against the source the model named, not just against
// any source in the brief.
test "a quote is checked against the source it names" {
  answers assistant: [
    {claims: [
      {text: "A model's answer must be a complete record.", document: "kosika.md", quote: "A model's answer is accepted only if it is a complete record"},
      {text: "Kosika runs on the JVM.", document: "notes.md", quote: "Kosika runs on the JVM"}
    ]},
    {text: "Kosika checks the shape of an answer before using it."}
  ]
  result = main({question: "What does Kosika check?", sources: [
    {name: "kosika.md", text: "A model's answer is accepted only if it is a complete record of the type you declared."},
    {name: "notes.md", text: "The demo is on Tuesday."}
  ]})
  expect result.documents == 2
  expect result.checked == 2
  expect result.unsupported == 1
  expect result.findings[0].verified == true
  expect result.findings[1].verified == false
}

test "a missing source or a tiny quote is not verified" {
  answers assistant: [
    {claims: [
      {text: "The sky is blue.", document: "missing.md", quote: "The sky is blue."},
      {text: "The sky is blue.", document: "notes.md", quote: "sky"}
    ]},
    {text: "One claim names a source that is not in the brief."}
  ]
  result = main({question: "What colour is the sky?", sources: [
    {name: "notes.md", text: "The sky is blue."}
  ]})
  expect result.checked == 2
  expect result.unsupported == 2
  expect result.findings[0].verified == false
  expect result.findings[1].verified == false
}

test "a quote may cross a line break in the source" {
  answers assistant: [
    {claims: [
      {text: "Rent is due on the first.", document: "home.md", quote: "Rent is due on the first"}
    ]},
    {text: "The rent is due at the start of the month."}
  ]
  result = main({question: "When is rent due?", sources: [
    {name: "home.md", text: "Rent is due\\non the first of the month."}
  ]})
  expect result.findings[0].verified == true
  expect result.unsupported == 0
}

test "no claims is a valid answer" {
  answers assistant: [{claims: []}, {text: "The sources do not answer the question."}]
  result = main({question: "What is the capital of France?", sources: [
    {name: "notes.md", text: "The demo is on Tuesday."}
  ]})
  expect result.checked == 0
  expect result.unsupported == 0
  expect result.findings.length() == 0
}
""",
        """\
{"brief": {"question": "What does Kosika check before a program runs?", "sources": [{"name": "kosika.md", "text": "Kosika checks before it runs. A model's answer is accepted only if it is a complete record of the type you declared. A program that does not check does not run."}, {"name": "notes.md", "text": "The demo is on Tuesday. Kosika is written in Python and Go."}]}}
""",
    ),
}

#: A recorded set of model answers for a starter, so its first run needs no
#: model. Keyed by kind; the order matches the workflow's model calls.
AGENT_FIXTURES: dict[str, str] = {
    "researcher": """\
{
  "assistant": [
    {
      "claims": [
        {
          "text": "A model's answer must be a complete record of the declared type.",
          "document": "kosika.md",
          "quote": "A model's answer is accepted only if it is a complete record of the type you declared"
        },
        {
          "text": "Kosika compiles to a native binary.",
          "document": "notes.md",
          "quote": "Kosika compiles to a native binary"
        }
      ]
    },
    {
      "text": "Kosika checks that a model's answer is a complete record of the declared type before the workflow continues. One claim is backed by its source; the other is not."
    }
  ]
}
""",
}
