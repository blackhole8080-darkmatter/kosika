# Kosika declaration layer — design (spec A)

Date: 2026-11-05
Status: approved in conversation, pending spec review
Supersedes: nothing. Discharges the Mind-layer spec's deferred item, *"Virtual models (to be
specified separately by the author)"* (`2026-11-02-kosika-mind-layer-design.md`, Out of scope).

Piece **A** of four: **A** declaration layer → **B** `.kos` components on the Python host →
**C** self-hosted checker → **P** conformance corpus growth. **P** is not a separate schedule;
it rides inside A, because every new rule A adds must add its own conformance cases. B and C
each get their own spec.

## Goal

Let a Kosika program **declare** its models, tools, MCP server grants and manifests, instead of
pointing at JSON files that hold inert text. Declarations compose: a model can be built from
other models. The checker resolves every reference before anything runs, so a config that names
a model, source or tool that does not exist is refused at check time rather than discovered at
load time or, worse, at run time.

The security model does not change. Approval hashes, the ledger, and durable resumes keep
depending on exactly the values they depend on today.

## Locked decisions

1. **First-class declarations, plus composition.** A model is a declaration in a program,
   referenceable by name from other declarations. Not merely a config format swap.
2. **Dual source.** The loader accepts either a `.kos` declaration or a legacy JSON profile.
   Safe by construction, because identity hashes the *resolved payload*, not the file: the same
   payload from either form yields the same hash. One name resolving to two source forms is a
   refusal, not a precedence order.
3. **Full JSON parity in what config can express** — arbitrary nested maps, lists, numbers,
   booleans, null. But every field whose type is known is still checked. Parity of what config
   can *say*, not parity of what goes *unchecked*.
4. **Full parity in both engines** for declaration resolution and identity. Passage retrieval
   stays Python-only in spec A; see "Parity contract" for why that line is sound.
5. **Static composition, dynamic selection.** `extends` and reference resolution happen at load;
   which branch a router takes happens per call, over alternatives that were all resolved and
   hashed up front.

## Grammar

Literals are added to **declaration bodies only**. The expression grammar and the type system
are untouched by this spec.

```kosika
model specialist {
  base "qwen2.5:7b"                       // STRING: a provider id, inert text
  embed "nomic-embed-text"
  sources ["KOSIKA.md", "std/nn.kos"]
  limits { context: 8000, results: 6 }    // nested map, NUMBER values
  cache on                                // BOOL
  fallback none                           // NULL
}

model router extends specialist {
  resolved: late
  route "code" -> coder                   // IDENT: a reference, resolved and checked
  route "prose" -> author
  otherwise -> specialist
}
```

**The rule that makes this better than JSON:** a bare identifier is a *reference* the checker
resolves; a quoted string is *inert text*. This is already the language's convention —
`can: [check_disk]` against `operation: "get_disk_info"` — so `-> coder` is refused if no model
named `coder` is declared, and a route to itself is refused. JSON can express neither.

Values permitted in a declaration body:

| Form | Meaning | Canonical JSON |
|---|---|---|
| `"text"` | string, inert | `"text"` |
| `40`, `40.0`, `1e3` | number; integer and decimal are **distinct values** | `40`, `40.0`, `1000.0` |
| `on` / `off` | boolean | `true` / `false` |
| `none` | null | `null` |
| `name` | reference, resolved by the checker | the referenced declaration's resolved payload |
| `[a, b]` | list, nesting arbitrarily | `[a, b]` |
| `{ k: v }` | map, nesting arbitrarily | `{"k": v}` |

New keywords are plain identifiers, so the lexer is untouched: `extends`, `resolved`, `flat`,
`late`, `on`, `off`, `none`, `route`, `otherwise`.

`resolved` is a **field name**, not a mode name. Its values are `flat` (the default, and omitted
when not written) and `late`. The two modes are referred to throughout as **flat** and **late**.

`//` comments are available in config, which JSON denies.

### Composition

- `model x extends y { ... }` merges, then expands. **Scalars and lists replace. Maps merge
  recursively.** Naming a field the base does not have is a checker error, so nothing merges
  silently.
- Expansion happens **before** reference resolution. A cycle through `extends` or `route`,
  transitively, is a checker error that prints the chain.

### Resolution modes

- `resolved: flat` (the default, and what a model is when `resolved` is not written) — expanded
  to a flat payload at load. The runtime never sees a composition, so there is no sub-model to
  swap mid-run.
- `resolved: late` — resolved per call, over the reachable set. Three rules keep it sound:
  1. Routing must be **lexical**, in the manner of `routing.by_words` (`kosika/routing.py:84`).
     A `late` model that routes by asking a model — `route_question`/`read_route`
     (`kosika/routing.py:58`) — is refused by the checker.
  2. `task` is already inside `plan_hash` (`kosika/agent.py:110`), so reachability is a pure
     function of (declaration tree, task). Both are hashed; the closure is computable before
     anything runs.
  3. The registry is **frozen at plan binding** (`kosika/durable.py:135`). Mutation afterwards
     is refused.

## Resolution and identity

**The payload dict is the interface.** `.kos` gets a front-end: parse → merge `extends` →
resolve references → emit the *same* payload dict the JSON loader emits today. Everything
downstream is unchanged: source reading, evidence checking, and `plan_identity`
(`kosika/virtual_model.py:169`).

```
router.kos  → merge extends → resolve refs → cycle? → payload dict → sha256 → plan_identity
profile.json → json.loads →                          payload dict → sha256 → plan_identity
router.kos AND router.json → refuse, naming both paths
```

**Name resolution is new.** Models load only by explicit path today —
`VirtualModelProvider(provider, args.virtual_model)` (`kosika/cli.py:424`). Composition needs
names, so there is a search path, tried in this order and stopping at the first match:

1. an explicit path, when one was given — always wins, never ambiguous;
2. the directory of the file that declares the reference;
3. a `models/` directory beside that file.

A name that matches nothing is a checker error. A name that matches more than one file is the
exclusivity refusal below.

**Exclusivity is a refusal, not a precedence order.** If a name resolves to both `router.kos`
and `router.json`, the loader refuses and names both paths. A precedence order is exactly how an
edit to the ignored file would silently do nothing.

**Identity, flat mode:** the fully expanded flat payload, plus passages and verified examples.
Mechanically identical to today. Touching a sub-model invalidates everything composed from it —
correct, since the evidence the model receives has changed.

**Identity, late mode:** the transitive closure of the *reachable* set only.

**Serialization is preserved per site, not unified.** Two different canonicalizations exist
today and both stay exactly as they are:

- `plan_identity`: `json.dumps(identity, sort_keys=True, ensure_ascii=False)` — default
  separators (`kosika/virtual_model.py:169`).
- `plan_hash`: `_canonical`, `json.dumps(value, sort_keys=True, separators=(",", ":"))`
  (`kosika/agent.py:97`).

Unifying them would change every hash in the repository. This spec does not touch either.

**Verified examples stay in a JSON sidecar** that the declaration references, so
`kosika virtual-model add-example` keeps its atomic append under an exclusive lock
(`kosika/virtual_model.py:449`). No tooling is asked to rewrite Kosika source text.

## Parity contract

| Surface | Obligation | Cost |
|---|---|---|
| Tokens | None. `STRING`, `NUMBER` (decimals and exponents), `IDENT`, `ARROW`, and `SYMBOL` covering `{ } [ ] ( ) : , .` already exist in both lexers (`kosika/lexer.py:19`). Keywords are plain identifiers. | zero |
| Parse + check declarations | Go already has `ModelDecl` (`native/ast.go:265`), a models map (`native/check.go:1423`), and a `max_output_tokens` range check. Extension, not a new subsystem. | moderate |
| Expansion + identity | New capability `kosika conformance payload <file> <model>` prints the resolved canonical payload and its sha256. Both engines are compared **on the hash**: byte-identical canonical JSON, or the test fails. | moderate |
| Diagnostics | Refusal messages identical strings — the existing `refused/` contract. | small |
| Passage retrieval | **Python-only in spec A.** Go has no virtual-model code at all. | deferred |

**Why the retrieval line is principled rather than a shortcut.** Retrieved passages are already
*inside* the hashed payload (`kosika/virtual_model.py:151`). If the two engines ever retrieved
differently, the hash would differ and everything downstream fails closed: no ledger row means
the approval is asked for again, and a stale journal raises `plan_mismatch`
(`kosika/durable.py:136`). A retrieval divergence cannot widen an approval. It can only refuse
one.

## Error handling

Fail closed, and name what to do. Observed live on 2026-11-05: a provider 404 produced
`Agent failed safely: …` *and* still printed the plan hash, so the failure stayed attributable.

**Check time — nothing runs, no model is called, no cost.** Unknown reference. Cycle through
`extends` or `route`, chain printed. An `extends` field the base does not have. A decimal where
an integer is typed. A `late` model that routes by asking a model. One name resolving to two
source forms.

**Load time.** A source path that does not exist. Expansion producing a payload that violates
the profile schema, refusing and naming the field. The existing rule is kept: inside a directory
an invalid `.kos` is skipped, but a single file named directly must check or loading fails.

**Run time.** Registry mutation after plan binding in `late` mode. Budget exhaustion keeps
today's behaviour. Provider failure keeps today's `Agent failed safely` plus plan hash.

**Every new error site claims a diagnostic code.** `kosika diagnostics-coverage` reports whether
every error raise site is claimed by a diagnostic code. Every refusal listed in this section is
a new raise site — roughly ten against today's 219 `CheckError` sites — and that command must
stay clean. This is a gate, not a convention.

## Migration

Nothing rewrites a user's files. Existing JSON profiles keep loading untouched, and hash
identically.

`kosika virtual-model to-kos <profile.json>` **prints** the equivalent declaration to stdout and
never writes. This honours the no-two-way-sync cut while giving a migration path.

## Artifacts in scope

Hand-authored only:

- virtual-model profiles (`examples/virtual_models/*.json`)
- MCP server **grants** — a grant is a human decision
- extension manifests (`examples/extension_manifest.json`)
- network manifests (`kosika network init` writes an empty stub)
- the `--models` route registry — a JSON file giving each declared model its own route

Not in scope: MCP manifests and component contracts. Both are machine-generated and re-verified
on load (`kosika/manifest.py:222`, `kosika/components.py:379`). Giving them a human syntax
invites hand-edits that break the hash.

## Testing

**Unit (Python).** Parser on the new literals, `extends`, `route`. Checker on each refusal.
Expansion merge semantics. Canonical payload hashing. Dual-source exclusivity. One property
test: `to-kos` output re-parses to a payload identical to the JSON it came from.

**Conformance (both engines).** Fifteen new cases — passes in `cases/`, refusals in `refused/` —
plus hash comparison through the new `payload` capability. Trace-event equality already covers
runs (`tests/test_conformance.py:177`).

Cases, each with its refusals:

1. `extends` scalar replace
2. `extends` list replace
3. `extends` map merge, recursive
4. `extends` unknown field → refused
5. cycle through `extends` → refused, chain printed
6. cycle through `route` → refused, chain printed
7. unknown reference → refused
8. self-route → refused
9. `on` / `off` / `none` / nested maps and lists
10. decimal where an integer is typed → refused
11. both source forms present → refused, both paths named
12. flat expanded payload hash, equal across engines
13. late reachable-closure hash, equal across engines
14. `late` model routing by asking a model → refused
15. convergence: `X.kos` and `X.json` with the same payload → identical `plan_identity`

**The convergence invariant, over real artifacts.** For every JSON profile in the repository:
generate its `.kos` twin, load both, assert byte-identical `plan_identity`. A mechanical proof
that dual source is safe, run over actual files rather than a toy.

**The hash-stability guard — a merge blocker.** Hash a corpus of existing `.kos` programs before
and after the change and assert equality. This enforces the `_ABSENT_DEFAULTS` discipline
(`kosika/ast.py:916`), which exists so old programs hash identically. If this test fails, spec A
has silently invalidated real approvals and does not merge.

**Live demonstration, on Ollama with `qwen2.5:7b`.**

1. `kosika check` on a new `.kos` declaring `specialist` and `router extends specialist` → `OK`
2. break one reference → the refusal message, proving names really are resolved
3. `kosika conformance payload` on **both engines** → the same sha256 printed twice
4. a live `kosika agent` run through the `.kos`-declared model → a real cited answer
5. Studio open on the run, screenshotted

**Merge gate:** all five live steps, `diagnostics-coverage` clean, the existing unit suite green,
conformance green on both engines, and the hash-stability guard green.

## Slices

Each slice ends green on **both** engines, so the Go half is never deferred to the end.

1. **Literals and checking.** Numbers, booleans, `none`, nested maps and lists in declaration
   bodies; typed-field checking; the new diagnostic codes; conformance cases 9, 10. Both engines.
2. **The `.kos` front-end.** Parse a declaration to the payload dict; dual-source exclusivity;
   `to-kos` printing and its round-trip property test; the convergence invariant; case 11, 15.
3. **Composition.** `extends` merge rules, reference resolution, cycle detection with the chain
   printed; flat-mode identity; the hash-stability guard lands here; cases 1–8, 12.
4. **Late mode.** The lexical routing rule, reachable-closure identity, registry freeze at plan
   binding; cases 13, 14.
5. **The remaining artifacts.** MCP grants, extension manifests, network manifests, the
   `--models` route registry — each with its own declaration form and conformance cases.

## Verified state

Checked in this session, not assumed:

- A `model` declaration already exists, with `input`, `output`, `deployment`, `parameters`,
  `instruction`, `verb`, `evidence`, `default`, `plain` (`kosika/ast.py:566`,
  `kosika/parser.py:747`), used across `examples/agents/*/main.kos`.
- Top-level declaration kinds today: `import`, `export`, `record`, `tool`, `agent`, `swarm`,
  `workflow`, `fn`, `model`, `default model`, `shape`, `choice`, `enum`, `board`, `store`,
  `memory`, `test`.
- Numbers already parse in declaration bodies, but only inside a closed typed record of six
  fields (`kosika/parser.py:876`, `parse_model_parameters`). That is the grain of the language,
  and the typed-field checking in slice 1 follows it.
- The lexer already covers the entire new syntax (`kosika/lexer.py:19`).
- A second engine exists: 94,361 lines of Go, held to token-stream equality
  (`tests/test_conformance.py:262`) and trace-event equality (`:177`), with identical refusal
  messages.
- Conformance today: 122 programs — 32 `cases/`, 11 `failing/`, 79 `refused/` — against 219
  `CheckError` and 56 `ParseError` sites, with no parser-error cases. Spec A adds the first
  parser-adjacent error cases the suite has had.

## Out of scope

- **Spec B**: `.kos` components on the Python host. Components are pinned subprocess artifacts
  (Python or JS) with no `.kos` path; B extends the module system `kosika/std/*.kos` already
  uses, not the component protocol.
- **Spec C**: a self-hosted checker or parser in Kosika.
- Passage retrieval in Go.
- Two-way sync. JSON is never rewritten from `.kos`.
- `.kos` syntax for generated artifacts (MCP manifests, component contracts).
- Literals in expression position.
- A config UI.
- Consensus or quorum across peers; self-granting of capabilities. Both remain forbidden, as in
  the Mind-layer design.
