from kosika.parser import parse
from kosika.checker import check, CheckError
from kosika.cognitive import load_cognitive

# Valid program checks clean
src = """
memory_bubble user_pref {
  content: \"User prefers concise answers\"
  kind: preference
  confidence: 1.0
}
personality reviewer {
  tone: precise
  priorities: [correctness, safety]
}
"""
p = parse(src)
check(p)
print("valid program: checks clean")
store = load_cognitive(p)
print("store summary:", store.get_state_summary())

# Bad kind is refused
try:
    check(parse('memory_bubble x { content: \"hi\" kind: belief }'))
    print("ERROR: bad kind accepted")
except CheckError as exc:
    print("bad kind refused:", exc)

# Bad confidence is refused
try:
    check(parse('assumption x { statement: \"hi\" confidence: 1.5 }'))
    print("ERROR: bad confidence accepted")
except CheckError as exc:
    print("bad confidence refused:", exc)

# Approved judgment without approver is refused
try:
    check(parse('judgment x { claim: \"hi\" approved: true }'))
    print("ERROR: unattributed approval accepted")
except CheckError as exc:
    print("unattributed approval refused:", exc)

# Duplicate across kinds is refused
try:
    check(parse('memory_bubble same { content: \"a\" } judgment same { claim: \"b\" }'))
    print("ERROR: duplicate accepted")
except CheckError as exc:
    print("duplicate refused:", exc)

# Unknown tone is refused
try:
    check(parse('personality p { tone: shouty priorities: [x] }'))
    print("ERROR: bad tone accepted")
except CheckError as exc:
    print("bad tone refused:", exc)
