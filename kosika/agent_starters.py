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

record Ticket { customer: Text text: Text }
record History { tickets: Number last_queue: Text }
record DeskReply { queue: Text urgent: Bool returning: Bool tickets: Number message: Text }

model assistant
memory customers of History

fn updated(known: CustomersRecalled, label: Choice) -> History {
  let before = if known.found { known.value.value().tickets } else { 0 }
  return {tickets: before + 1, last_queue: label.label}
}

fn fallback_label(ticket: Ticket) -> Choice {
  return {label: "other", confidence: 0}
}

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

workflow main(ticket: Ticket) -> DeskReply {
  budget 12 model calls, 300 seconds
  known   = recall customers under ticket.customer
  label   = classify ticket.text into ["billing", "bug", "account", "other"] retry 1 otherwise fallback_label(ticket)
  urgent  = decide "Is the customer unable to use the product at all right now?" given ticket.text with evidence retry 1
  draft   = summarize ticket.text
  history = updated(known, label)
  saved   = remember history in customers under ticket.customer
  result  = reply(ticket, known, history, label, urgent, draft)
}

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

record Claim { employee: Text receipt: Text }
record Receipt { merchant: Text? total: Number? category: Text? }
record Verdict { approved: Bool amount: Number category: Text reasons: Text[] }

model assistant

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

record Notes { text: Text }
record Task { owner: Text what: Text days: Number }
record TaskList { tasks: Task[] }
record OwnerLoad { owner: Text count: Number soonest: Number }
record Plan { summary: Text first: Text? total: Number busiest: Text? loads: OwnerLoad[] }

model assistant

fn none(notes: Notes) -> TaskList {
  return {tasks: []}
}

fn plan(found: TaskList, brief: Summary) -> Plan {
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
}
