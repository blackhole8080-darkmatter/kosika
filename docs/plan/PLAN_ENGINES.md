# PLAN_ENGINES.md

## Goal
Grow Kosika’s language and its two engines (Python in `kosika/` and Go in `native/`) over the next month.  
The plan lists ten concrete, ordered steps, explains why each matters, identifies the files touched, and shows how to verify completion.

---

### 1. Add a “Hello, World!” benchmark to the Python engine
**Why it matters** – Provides a quick sanity check for the Python engine and a baseline for performance comparisons.  
**Files touched** – `kosika/benchmarks/hello_world.py`, `kosika/README.md` (add benchmark description).  
**Done when** – Running `python kosika/benchmarks/hello_world.py` prints “Hello, World!” and the benchmark section in the README lists the runtime.

### 2. Implement a simple type‑checker for the Go engine
**Why it matters** – Enables early error detection and improves developer experience.  
**Files touched** – `native/typecheck.go`, `native/engine.go` (invoke checker), `native/README.md`.  
**Done when** – Compiling a Go file with a type error produces a clear error message; the README notes the new feature.

### 3. Create a shared configuration file for both engines
**Why it matters** – Centralizes settings (e.g., debug mode, optimization flags) and reduces duplication.  
**Files touched** – `config.yaml`, `kosika/config.py`, `native/config.go`.  
**Done when** – Both engines load `config.yaml` and respect the `debug` flag; running `kosika/config.py` prints the config values.

### 4. Add automated linting for Python and Go code
**Why it matters** – Maintains code quality and catches style issues early.  
**Files touched** – `.pylintrc`, `golangci.yml`, `Makefile` (add `lint` target).  
**Done when** – `make lint` passes without errors for both directories.

### 5. Write a migration script to upgrade existing Python scripts to the new syntax
**Why it matters** – Ensures backward compatibility and eases adoption of language changes.  
**Files touched** – `scripts/migrate.py`, `kosika/CHANGELOG.md`.  
**Done when** – Running `python scripts/migrate.py` on an old script produces a new script that runs unchanged.

### 6. Implement a simple REPL for the Go engine
**Why it matters** – Provides an interactive way to test snippets and learn the language.  
**Files touched** – `native/repl.go`, `native/engine.go` (register REPL).  
**Done when** – Executing `go run native/repl.go` opens a prompt that evaluates Go expressions.

### 7. Add unit tests for the Python engine’s core interpreter
**Why it matters** – Guarantees correctness of the interpreter logic and facilitates future refactors.  
**Files touched** – `kosika/tests/test_interpreter.py`.  
**Done when** – `pytest kosika/tests/` passes all tests and shows 100% coverage for the interpreter module.

### 8. Document the Go engine’s API in `native/README.md`
**Why it matters** – Helps external developers understand how to embed or extend the engine.  
**Files touched** – `native/README.md`.  
**Done when** – The README contains a clear “API Reference” section with example code snippets.

### 9. Create a CI workflow that runs benchmarks for both engines
**Why it matters** – Detects regressions in performance and ensures continuous integration.  
**Files touched** – `.github/workflows/benchmarks.yml`.  
**Done when** – The workflow triggers on pushes, runs both benchmarks, and reports the runtime in the PR comment.

### 10. Publish a “Getting Started” guide for new contributors
**Why it matters** – Lowers the barrier to entry and encourages community growth.  
**Files touched** – `CONTRIBUTING.md`, `docs/getting_started.md`.  
**Done when** – The guide includes steps to clone the repo, run the Python and Go engines, and submit a pull request.
