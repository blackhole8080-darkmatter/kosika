# Kosika as a software platform — second opinion

**Status: review of `docs/PLATFORM.md`, written against the repo, not the prose.**
Everything below was verified by four parallel read-only investigations against
`projects/kosika` (branch `studio`, 165 commits ahead of `master`, dirty tree) on
7 Nov 2026. The full reports are in `.team/`:

- `.team/evidence-audit.md` — every claim in PLATFORM.md checked, with commands
- `.team/loop-design.md` — Stage-2 mechanics grounded in `selfdev.py`/`durable.py`
- `.team/benchmark-spec.md` — the benchmark design and six seed tasks
- `.team/market-scan.md` — external landscape, demand, and prior art (all cited)

PLATFORM.md gets the *shape* right and several numbers wrong. This document keeps
the shape, fixes the numbers, and changes the order of the first six weeks.

---

## TL;DR

The four-axis framing (unit of work → loop → artifact → authority) is the correct
reframe, and "A as the product, B as the bridge, never both at once" is the right
instinct. But the plan as written stands on three stale measurements and one
structural contradiction:

1. **The loop is 1-in-18 (5.6%), not "55% kept".** The 55% figure is an example
   in `docs/SELF_DEVELOPMENT.md`, not a measurement. The real ledger is 18
   attempts, 1 kept, 17 refused — and 11 of 17 refusals were "I need to see more
   lines", not edit failures.
2. **CI is not "red until mypy tests are fixed".** It is red on four independent
   things: `ruff format --check` (122 tracked files reformat), `ruff check`
   (52 diagnostics), `mypy` under the CI pin (6 errors in `kosika/`), and a
   10-minute job timeout against a suite that takes more than that. `mypy kosika`
   is green under local mypy 2.3.1 and **red** under the CI-pinned 1.15.0.
3. **"33 passing programs vs 218 CheckError sites" is a category error.** The 33
   passing cases exercise *zero* refusal sites. The real diagnostic coverage is
   **81 refused programs / 79 distinct messages vs 218 sites.**
4. **The contradiction:** "Kosika builds Kosika" means editing *Python* — where
   there is no AST to type-check, and no printer exists anywhere in the repo. The
   typed-edit capability the plan is built around applies only to `.kos`. So the
   platform's first "software" must be **Kosika programs, not Kosika's Python
   body**, or Stage 2 collapses into string surgery again.

The market adds a fifth correction: signed provenance for AI-built software is
**~70% commodity with a different label** (OpenFab, Publisher Authority Manifest,
Sonar, Cloudsmith). The one genuinely novel angle is **capability-declared
provenance — the checker proves what the artifact may do**, which is A-specific.

Consequence: keep the plan's ladder, but pull **sign + verify + a standard
attestation export** forward into the first stage, point the loop at `.kos`
projects, and treat "green CI" as a multi-day repair, not a two-week gate.

---

## 1. What the evidence corrects

| PLATFORM.md says | What the repo shows | Source |
| --- | --- | --- |
| "mypy kosika is green (was 44 errors)" | Green under local mypy 2.3.1; **6 errors in 3 files under CI-pinned mypy 1.15.0** (`designs.py:615,654`, `sugar.py:179,181`, `diagnostics.py:868`×2) | `.team/evidence-audit.md` §1 |
| "mypy kosika tests still reports 144 errors" | Unreproducible. mypy 2.3.1 → 15 errors in 2 test files; mypy 1.15.0 → **0 test errors** (the 6 are all in `kosika/`) | §1 |
| CI quality job "stays red until mypy tests fixed" | Red on four axes: `ruff format --check .` (**122 tracked files**), `ruff check .` (**52** diagnostics, ruff 0.11.13), mypy 1.15.0 (6), coverage ≥80 (unverified; last recorded 77% at `KOSIKA_PLAN.md:301`) | §2 |
| "33 passing programs against 218 CheckError sites" | 36 cases (33 with expected output), 82 refused, 11 failing. 33 passing exercise **0** `raise CheckError` sites; coverage is **81 refused / 79 distinct messages vs 218** | §3 |
| "Go hashes program text; approvals key on plan_identity" | Go `programHash = sha256("kosika-native\x00"+source)` (`native/trace.go:144-150`); Python `program_hash = sha256(canonical program_payload)` (`pack.py:44-47`). Measured on hello_world: `ae9afb30…` vs `8749e04a…`. Approvals key on **`plan_hash`** (`agent.py:101-137`, `durable.py:136`, `approval_ledger.py:30`), not `plan_identity` (which is the virtual-model *profile* identity, `virtual_model.py:170-194`) | §4 |
| selfdev "single file, exact-match, 55% kept" | Multi-file `Proposal` (a tuple of `Edit`s, `selfdev.py:464-490`); exact-match is true; **1 kept / 18 attempts = 5.6%**, all `roadmap` kind, 37 s median | §7 |
| "the parser and printer exist" | Parser exists; **no AST→source printer anywhere** — only `format_source` indentation (`devtools.py:21-43`), not on the CLI | loop-design §a |
| authority is "per-invocation, not per-project" | `kosika.env` already is a project file (models, MCP, folders, sites, shell, budgets, `keep`), and `--env` refuses conflicting flags. The real gap is **policy** (who may approve, signature requirements, model qualification), not environment declaration | LANGUAGE_DETAIL "Environments" |
| "28 example agents" | 29 directories (28 excluding `designs/`), **20 declare an `agent`**, all single-program, no targets | §9 |

Confirmed true and unchanged: 218 `raise CheckError` sites (`checker.py`); packs
unsigned (`pack.py:20-21`, CLI is only `make/check/unpack`); `development/v1` has
exactly five operations; `kosika.toml` does not exist.

---

## 2. The fork, reconsidered

PLATFORM.md proposes **A** (Kosika is the language) with **B** (component/v1) as
a bridge, and rejects both-at-once. The market scan supports the *instinct* but
reframes the product:

- **A alone is not a distribution strategy.** No buyer has ever switched language
  to obtain auditability; the historical pattern (SLSA/in-toto/Sigstore) is to
  *retrofit* attestation onto existing toolchains.
- **B is table stakes** where Factory, Devin, Codex, and Cursor are far better
  funded.
- The defensible product is **C — a language-agnostic attestation + offline
  `verify` layer** — sold through B (normal PR/CI, no rewrite), with A as the
  reference implementation and the moat.

The moat is specific and worth naming: **the checker derives capabilities from
the program**, so an attestation can state "this artifact may do exactly these
things" — whereas PAM's `permissions_max` and everyone else's are hand-declared
ceilings. That is the only thing the scan could not find prior art for.

**Recommendation: build one shared foundation, defer the market decision until
there is evidence.** Identity → sign → offline verify → standard attestation
export → green CI → project build is required by *both* A and C. The fork that
actually matters in the next six weeks is narrower:

- **Dogfood target: `.kos` agent software, not Kosika's Python body.** Typed
  edits, keyed answers, and replay all live in the `.kos` world. Editing Python
  stays a find/replace + gate bridge (it is B, in disguise). The benchmark and
  the loop should therefore operate on `.kos` projects (the 20 agents, `conformance/`,
  example apps), not on `kosika/*.py`.
- **No printer is a hard constraint.** Splice-based edits (selector + validated
  snippet, preflighted in memory) are feasible now; a full lossless printer is a
  900–1500 LOC side project and must not block the loop.
- **The Go engine:** freeze it to conformance parity. A second engine that lags
  is a cost sink, not an asset. New features land in Python first; Go must
  reproduce the identity vectors within one stage or be marked experimental.

---

## 3. The capability ladder — what you can expect, and when

Each stage states the *capability a person has at the end*, the exit proof, and
the honest limit. Estimates assume one focused author reusing what exists.

### Stage 0 — "the artifact you can hand over" (≈2 weeks)

**Capability:** `kosika pack sign` + `pack check --require-signature`;
`kosika verify <pack>` replays and checks hashes and signatures offline and
fails closed; one identity contract with cross-engine vectors; an attestation
export in a standard shape; CI green on the *committed* tree.

What must be built (mostly small, all measured):
- **One identity function** `kosika.identity/v1` (canonical payload hash + module
  hashes), replacing the five Python copies (`pack.py`, `runtime.py:1874`,
  `app.py:434`, `swarm.py:107`, `synapse_bridge.py`) and porting it to Go with a
  shared vector file both engines must reproduce byte-for-byte. Bump pack/trace
  schemas with a dual-read transition.
- **Ed25519 signing** — recommend a pure-Python RFC 8032 implementation
  (~200 LOC, zero deps) to keep the dependency-light stance; key in
  `.kosika/keys/`, public key id in the pack, signature over the canonical
  payload, verified *before* any unpack/replay.
- **`kosika verify`** — signature first, then replay + hash in a temp dir with
  grants denied, no network, timeout-bound. This is the same machinery as
  `replay`; the new part is that it must refuse to *run* anything before the
  signature matches.
- **Attestation export** as an in-toto Statement + SLSA provenance predicate with
  a `kosika.capabilities/v1` predicate carrying `needs` (the checker-derived
  capability set). Offline, cosign/Sigstore-compatible key material optional.
- **Green CI**, which is four repairs: `ruff format` (auto-fix 122 files), `ruff
  check --fix` (52), the 6 mypy-1.15 errors, and the timeout. See §6.

**Exit proof:** on machine B, offline, `kosika verify` accepts a signed pack;
tampering one module makes it refuse and name the module; both engines print the
same program hash; CI passes all three jobs.

**Honest limit:** this is *tamper-evidence*, not provenance. "Who made it" needs
key identity and a reproducible-build check (build twice in two directories →
identical hash). Do not claim provenance until those exist.

### Stage 1 — "the project" (≈3 weeks)

**Capability:** `kosika.toml` (name, entry workflows, targets, modules,
environments, policy reference); a content-addressed build graph; `kosika build`
emitting a deterministic target artifact; `kosika init`/`kosika add`.

**Exit proof:** a three-target project builds twice with identical hashes; a
build on a second machine of a signed pack reproduces the first machine's
artifact; a rebuild on an unchanged tree is a no-op.

**Honest limit:** it builds `.kos` projects. Foreign code enters only through
`component/v1` with a pinned SHA-256 — trusted by digest, **not** verified
("the digest identifies bytes but does not prove their behavior" is already
stated in the docs; the attestation must carry that boundary explicitly).

### Stage 2 — "the loop that learns" (≈6–8 weeks, the hard one)

**Capability:** a `.kosika/tasks/<id>.md` sentence becomes a failing acceptance
test, then typed splice edits, then gate results, then one approval for one
change set with a reviewable diff and atomic undo — or a clean refusal. Measured
against a benchmark.

Build order, because the failure data dictates it:
1. **Benchmark families first** (see §5), and a cold baseline of the *current*
   loop on ~8 tasks. You cannot set a 60%/10-minute bar you have not calibrated.
2. **Context provisioning, not AST edits, is the highest-leverage fix.** 11/18
   ledger refusals asked for more lines. Package for the proposer: the whole
   target file (with explicit line ranges), the module map, the symbol index, and
   the last failures for the task — before spending effort on edit precision.
3. **Keyed answers** (`answers assistant as "triage": [...]`), keyed by
   `Step.name` (unique, `checker.py:2465`), refused at check time in
   `testing._check_one` via `load_program`, so `check`/`test`/`run`/LSP all fail
   loudly on an inserted step. Additive, ~150–200 LOC.
4. **Typed splice edits** (`kosika/edit.py`, ~450–650 LOC): selector + validated
   snippet, spans from `lexer.Token(line,column)`, preflighted through
   `load_program(..., source_overrides=…)` with four assertions (minimality,
   expected-AST equality, formatter idempotence, full-graph check) **before any
   write**; `--preview` shows the diff, `--apply` writes.
5. **One change set = one `$plan` approval**: reuse `plan_hash`/`action_hash` and
   the `ApprovalLedger` one-use claim; add a `FileJournal` + `kosika edit recover`
   because the claims are irreversible and `durable.py` does no rollback.
6. **Ledger feedback + telemetry.** Today the loop feeds the last 3 notes of the
   same task id (not the kind); `ModelProposer` discards token usage. Fix both so
   the benchmark can record tokens and per-kind failures.

**Exit proof:** benchmark pass (calibrated bar), zero regressions, median under
the calibrated budget, every kept change replayable, and cost-per-family
run-1-vs-run-N published.

### Stage 3 — "distribution with policy" (≈3 weeks)

**Capability:** `kosika.lock` (content-addressed module fetch over HTTPS, pinned
SHA-256, no registry); a provenance record per pack (key identity, model/version
per change, task id, trace hash, gate results, policy hash); **policy-bound
artifacts** (a pack embeds the policy hash and `verify` refuses a pack built
under weaker policy); key identity/rotation/revocation.

**Exit proof:** a pack built on another machine, offline, with one mismatched
test, `verify` refuses and names the change — and a pack built under policy P is
refused under policy P′.

### Stage 4 — "the platform surface" (ongoing)

**Capability:** `development/v1` *and* the MCP server consolidated into one
versioned API with `build`, `verify`, `develop` added; Studio becomes the review
surface (task → plan → diff → evidence → trace → signature), not a chat window;
`kosika verify` ships as a GitHub Action; a first installable, signed release.

**Exit proof:** an external agent completes one benchmark task through the API
alone, and the resulting pack verifies on a second machine offline.

---

## 4. Methods — how I would actually build it

**Identity.** One function, one schema, one vector file
(`conformance/identity_vectors.json`) that Python and Go both read and must
reproduce. Deprecate the five copies by routing them through the one function;
version-bump the pack and trace schemas so old artifacts still read, then cut the
old hashes once nothing emits them.

**Signing.** Pure-Python Ed25519 over the canonical payload (the same bytes the
hash covers), so "the hash is over what I signed" and "what I signed is what
verifies" are the same assertion. Key in `.kosika/`, never in a pack. Verify
before any code runs.

**Verify.** Signature → hash → replay, in a temp dir, grants denied, no network,
timeout. Fails closed and names the exact module/change. This is the product
surface for both A and C, so it must be independent of Studio and the model.

**Attestation.** Emit in-toto Statement + SLSA provenance, with a
`kosika.capabilities/v1` predicate for the checker-derived `needs`. Standard shape
is what lets a first external user `cosign verify` / run OPA policy without
adopting the language.

**Build.** Content-addressed manifest, canonical sorted JSON, no timestamps, no
absolute paths; the two-machine and two-directory determinism tests are the gate.

**Loop.** As in §3 — context first, keyed answers, splice edits with the four
assertions, one-plan approval with a journal, then telemetry. The single most
important principle: **nothing is written until the whole module graph checks in
memory**, and every kept change must replay.

**Measurement.** Each stage ships a results file
(`benchmarks/platform/results/YYYY-MM-DD.json`) with wall clock, model calls,
tokens, edits proposed/refused, review minutes, and replay-verified boolean. The
headline metric stays cost-per-task-family run 1 vs run N — but it is only honest
if run on `.kos` task families, never on 30 unrelated tasks.

---

## 5. The benchmark (build it before the loop, and change it)

PLATFORM.md's benchmark is right in spirit, wrong in composition:

- **Families, not unrelated tasks.** 6 families × 30 tasks (5/5/6/5/5/4), keeping
  the kind totals (10 fix / 6 extend / 5 refactor / 5 test-gap / 4 behaviour-change)
  as metadata. The "cost per task, run 1 vs run 10" metric **cannot exist across
  unrelated tasks**; only within a family do you get paired cold/warm calls and
  identical verified output.
- **Calibrate, don't guess.** 60%/10-minute are targets, not bars. Measured
  today: 1/18 kept, ~160–183 s/model call, 77–222 s gates, 8–13 min full suite.
  Adopt **baseline + 25 points (12–18/30)** and **median ≤15 min with a
  single-file gate**, after an 8-task cold baseline.
- **Floor and ceiling.** Mirror the existing `kosika/benchmark.py`: an `identity`
  solver (floor) and `oracle` solver (applies the stored reference patch, ceiling)
  every run, so a number without both is unreadable.
- **Determinism without a model.** Fixture proposals keyed by `request_hash`,
  `.kos` keyed answers, and replay of kept diffs. New-file/structural tasks are
  unsolvable by today's loop (no printer) and must be refused for the baseline.
- **Seeds exist now**, grounded in `conformance/` and the 87 uncovered diagnostic
  codes (see `.team/benchmark-spec.md`): duplicate-field refusal, store-declaration
  refusal, approval-through-tool, vectors/matrices, store+table, and a keyed-answers
  acceptance test that today fails with `Expected ':' … got 'as'`.
- **The validity caveat, said plainly:** 30 binary tasks cannot distinguish a 50%
  loop from a 60% loop (18/30 has CI ≈ 42–76%), and families overstate
  generalization. The benchmark measures loop mechanics on this repo's idioms, not
  model quality or novel-work amortization.

---

## 6. Numbers you can expect

- **Next 6 weeks:** signed, verifiable packs; one identity across engines; green
  CI; a 3-target deterministic build; an attestation export; the benchmark seeded
  and the current loop baselined.
- **Next 14 weeks:** a calibrated loop with keyed answers, splice edits, one-plan
  approval, and a published cost-per-family number (which will likely start bad —
  publish it anyway).
- **Next 20 weeks:** distribution with policy, standard attestations, one
  consolidated development API, a review-grade Studio, and a first installable
  release.
- **Not in that window:** a general-purpose app language, editing foreign
  languages, a module registry, a marketplace, multi-user SaaS, or the swarm —
  none of which the benchmark needs.

The honest expectation for the loop: today's proposer keeps 1 in 18 on vague
roadmap items with single-file context. With well-formed `.kos` tasks, full-file +
map context, and keyed answers, the ceiling before model quality becomes the
binding constraint is modest — plan for a baseline in the low tens of percent and
let the *measurement* decide the next lever.

---

## 7. What would kill it (falsifiers and guardrails)

- **If Stage 0 (identity + sign + verify + green CI) exceeds 4 weeks**, the
  platform thesis is 3–6 months off; re-scope, don't push the date.
- **If the 8-task cold baseline is <20%**, typed edits will not reach 60% in
  8 weeks; re-scope the loop to smaller tasks and more context before more
  machinery.
- **If a 5-minute offline `verify` demo cannot be made** (foreign deps, sandbox,
  key handling), C is not sellable.
- **If cost per family does not fall measurably from run 1 to run N**, the
  compounding claim is false — publish the negative result.
- **If GitHub/Anthropic/Sonar ship signed per-PR artifact attestations**, C is a
  feature; the fallback is the capability-derived predicate, which is A-specific.
- Guardrails that must not move: no language feature without a benchmark task; no
  approval/grants/replay relaxation to make a number look better; no provenance
  claim before `verify` exists; the Go engine passes identity vectors or is marked
  experimental.

---

## 8. The next two weeks, in order

Identity before builds, sign/verify before any distribution claim, benchmark
baseline before any loop change.

1. **Days 1–2 — identity.** One `kosika.identity/v1`, one vector file, port to Go,
   route the five Python copies through it. Exit: both engines print one hash.
2. **Days 2–5 — sign + verify.** Pure-Python Ed25519, `pack sign`,
   `check --require-signature`, `kosika verify` offline/fail-closed. Exit: a
   tampered pack is refused, offline, naming the module.
3. **Days 4–7 — green CI.** `ruff format` (auto-fix 122), `ruff check --fix` (52),
   the 6 mypy-1.15 errors, and either shrink the suite below the 10-minute timeout
   or raise the timeout and fix the coverage gate (last recorded 77%). Exit: three
   green jobs on the committed tree.
4. **Days 6–9 — attestation export.** in-toto/SLSA shape + `kosika.capabilities/v1`
   predicate. Exit: `cosign`-compatible verification of one pack's capability set.
5. **Days 8–12 — benchmark baseline.** Adopt the 6-family spec; land 6 seed tasks;
   run the *current* loop cold on 8 tasks and record every metric. Exit: a
   `results/` file with a real baseline, so the 60%/10-minute bar becomes a
   decision, not a guess.
6. **Days 12–14 — `kosika.toml` (schema only).** Draft it and pressure-test against
   the 20 `.kos` agents: do they fit a project with targets? If not, the schema is
   wrong.

That ordering is deliberate: **identity before builds, builds before loops, loops
before distribution** — and every stage ends with a measured number, which is the
one habit this project already has and should never lose.

---

## 9. Decisions I need from you

1. **Positioning:** internal leverage (A, dogfood `.kos`) or a product (C,
   attestation/verify sold through B)? Recommendation: build the shared foundation
   now, decide after the 5-minute offline `verify` demo and the benchmark baseline.
2. **Dogfood target:** `.kos` agent software (recommended) vs Kosika's Python body
   (find/replace only).
3. **Go engine:** freeze to conformance parity (recommended) vs dual-track.
4. **Signing:** pure-Python Ed25519 (zero deps, recommended) vs a `cryptography`
   optional extra.
5. **Benchmark:** family-based (recommended) vs PLATFORM.md's unrelated-30.
6. **Release:** publish a 0.2 wheel + signed-pack demo (needed for any external
   user) — yes/no.

The four `.team/` reports are the evidence; this document is the decision. The
one-line version: **you don't need a platform yet, you need to make the artifact
you already have provable, then point the loop at `.kos` projects and measure it.**
