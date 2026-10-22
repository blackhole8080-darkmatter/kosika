# What overlaps in Kosika, and what could be retired

Nothing here has been removed. This is a list for you to choose from.

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

### 3. Reading other languages: keep, but decide its future

`source_map`, `project_analysis`, `javascript_adapter`, `rust_adapter`,
`python_adapter`, `language_adapters`. About 3,200 lines. Commands:
`sourcemap`, `analyze`, `project`.

Your vision includes cross-language support, so I would keep this. It is
not connected to Studio today.

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

### 6. The older `kosika agent` command and skill catalogs: keep for now

`kosika agent`, `kosika skills`, and the `skill.json` form of skills.

Agents in workflows and skill notes replaced these for everyday use, but
the agent loop itself is shared with the core, so little would be saved.

### 7. Networks, components, extensions, the editor server: your call

`network`, `components`, `extensions`, `lsp`, `editor_features`,
`development`, `mcp_server`, `sdk`. About 4,100 lines.

These are groundwork for the networked part of your vision and for editor
support. None is used by Studio today. I would keep `lsp` and
`editor_features` (they give code completion) and ask you about the rest.

## If you say yes to the recommended groups (1, 2, 4, 5)

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
