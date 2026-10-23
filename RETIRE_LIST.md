# What overlaps in Kosika, and what could be retired

The four recommended groups have since been retired, one commit each:

- `9ffc6f5` the city pictures, with the `city` and `design` commands
- `8cc3b47` the Python authoring loop, with `author` and `author-eval`
- `0b70411` the older maps, with `audience-map`, `unified`, `explain` and
  `assistant`
- `296e7f0` the first desktop app

As noted below, `repair`, `corpus`, `benchmark`, `mindmap` and
`synapse_bridge` stayed. Groups 3, 6 and 7 were measured afterwards and kept;
that measurement is at the end. What follows is the list as it was written,
before any of that happened.

## The measurement

- Kosika has 106 Python modules and 52,700 lines.
- Studio, the runtime, `kosika test`, `kosika start`, `kosika motion` and
  sessions together reach 49 modules and 23,000 lines. Call this the core.
- The other 57 modules, about 29,700 lines, are reached only through
  older commands. They also carry roughly 25,000 lines of tests, which is
  most of why the test suite takes 13 minutes.

How this was counted: by following imports from `app.py`, `runtime.py`,
`testing.py`, `quickstart.py`, `motion.py` and `sessions.py`. A module that
is loaded in some other way would be missed, so each group below needs a
second look before anything is deleted.

## Groups, with my recommendation for each

### 1. City pictures: retire (recommended)

`city`, `cityscape`, `metropolis`, `city_live`, `three_world`, `world`,
`design`, `event_stream_design`. About 6,600 lines, plus about 5,000 lines
of tests. Commands: `city`, `design`.

These draw agents as buildings in a city. Studio's Live view, run diagram
and Map tab now show the same things in a way that was tested with you.
Nothing in the core uses them.

### 2. Older maps for readers: retire most, keep one (recommended)

`mindmap`, `audience_maps`, `explain`, `unified_mindmap`, `assistant`,
`workbench_mindmap`. About 3,100 lines. Commands: `mindmap`,
`audience-map`, `unified`, `explain`, `assistant`.

Studio's Map tab (`project_map`) replaced these for a beginner. `mindmap`
is used by seven other modules, so it would go last or stay.

### 3. Reading other languages: kept, measured

`source_map`, `project_analysis`, `javascript_adapter`, `rust_adapter`,
`python_adapter`, `language_adapters`. About 3,200 lines. Commands:
`sourcemap`, `analyze`, `project`.

Your vision includes cross-language support, so I would keep this. It is
not connected to Studio today. See the measurement at the end.

### 4. The first desktop app and its helpers: retire (recommended)

`workbench`, `workbench_support`, `workbench_mcp`, `workbench_mcp_server`,
`workbench_samples`, `desktop`, `synapse_bridge`. About 1,900 lines.

This is the app that came before Studio. It is not the `workbench` example
agent, which stays.

### 5. The Python authoring loop: retire after one check (recommended)

`authoring`, `authoring_evaluation`, `repair`, and the commands `author`,
`author-eval`. About 2,200 lines.

The `author` example agent, written in Kosika, now does this job and has
its own evals. `corpus` and `benchmark` stay: the diagnostics tests use the
corpus.

### 6. The older `kosika agent` command and skill catalogs: kept, measured

`kosika agent`, `kosika skills`, and the `skill.json` form of skills.

Agents in workflows and skill notes replaced these for everyday use, but
the agent loop itself is shared with the core, so little would be saved.
See the measurement at the end.

### 7. Networks, components, extensions, the editor server: kept, measured

`network`, `components`, `extensions`, `lsp`, `editor_features`,
`development`, `mcp_server`, `sdk`. About 4,100 lines.

These are groundwork for the networked part of your vision and for editor
support. None is used by Studio today. I would keep `lsp` and
`editor_features` (they give code completion) and ask you about the rest.
See the measurement at the end.

## If you say yes to the recommended groups (1, 2, 4, 5)

This is the estimate as written; the work was done, and the commits are at the
top.

- About 13,800 lines of code and about 12,000 lines of tests would go.
- The test suite should drop from about 13 minutes to roughly 8. That is
  an estimate, not a measurement.
- Nine commands would disappear from `kosika --help`: `city`, `design`,
  `mindmap`, `audience-map`, `unified`, `explain`, `assistant`, `author`
  and `author-eval`. The first desktop app has no command of its own.
- It would be done one group per commit, each with the full suite run, so
  any group can be brought back with one `git revert`.

## What I am not sure of

- The line counts for groups are added up from the module list and are
  approximate.
- I have not checked whether any document, example or packaged file still
  points at these commands. That check comes before each removal.

## The measurement that closed groups 3, 6 and 7 (24 October 2026)

The list above guessed that these groups were where the test time went, on
the strength of "roughly 25,000 lines of tests". That guess was wrong, and
the numbers are worth keeping because they are the reason nothing here was
deleted.

**They work.** Each surface was run, not read:

```text
kosika sourcemap /tmp/retirecheck --format text
  Files: 2  declarations: 4   (a Python file and a JavaScript file found)
kosika analyze src/a.py
  function greet (1-2)
kosika project /tmp/retirecheck
  {"analysis_mode": "bounded_project_structure", ...}
kosika-lsp  (initialize over stdio)
  textDocumentSync, documentSymbol, hover, definition, references, completion
kosika-mcp  (initialize over stdio)
  five tools: kosika_guide, kosika_check, kosika_run, kosika_replay, kosika_map
```

`network`, `component` and `extension` each answer with their subcommands,
and each has an example in `examples/` that its tests use.

**They are documented.** The SDK, the MCP server, the LSP backend, the
development schema, cross-language components and network manifests each
have a section in [README.md](README.md). The language adapters have theirs
in [DEVELOPMENT.md](DEVELOPMENT.md) and are listed as exercised in
[KOSIKA_PLAN.md](KOSIKA_PLAN.md). `kosika-lsp` and `kosika-mcp` are
`[project.scripts]` entry points in [pyproject.toml](pyproject.toml), so they
are installed commands, not scripts inside the tree.

**They are cheap.** This is the part that settles it:

| Group | Code | Named test files |
| --- | --- | --- |
| 3. Reading other languages | 3,201 lines | 6 files, 1,337 lines |
| 6. `agent` and skills | 3,638 lines | 9 files, 2,013 lines |
| 7. Networks, components, editor | 4,118 lines | 9 files, 2,296 lines |

All sixteen of those test files together — 181 tests and 37 subtests — run in
**24.4 seconds**. That is the whole cost in suite time of the three groups,
against an 8-minute suite. The slowest single case was 2.2 s. Retiring them
would buy about half a percent of the runtime and would remove eight
commands, two installed entry points, and the SDK and MCP server that the
README tells people to use.

So they are kept. The original premise held for groups 1, 2, 4 and 5, whose
tests were the bulk of the 13 minutes; it does not hold here.

One thing inside group 6 is genuinely older than the rest: the `skill.json`
manifest form, about 250 lines (`SkillCatalog`, `SkillRouter`,
`_load_manifest`) inside the 786-line [kosika/skills.py](kosika/skills.py).
It cannot simply go, because that module is also where the skill *notes* are
parsed, and [runtime.py](kosika/runtime.py), [app.py](kosika/app.py),
[agent.py](kosika/agent.py) and [system_tools.py](kosika/system_tools.py)
import from it. Removing the manifest path would mean untangling
`SkillRouter` out of the agent loop to save 250 lines and a few tests — a
worse trade than leaving it.
