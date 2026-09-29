# Preview Reviewer memory

- The preview file is `kosika-preview.html` in the kosika project root.
- The live workbench is at `http://127.0.0.1:8765` (start with `python -B -m kosika.workbench --port 8765` if needed).
- Verify every claim against the real CLI: `python -m kosika.cli check/run/mindmap/explain/assistant` and the examples in `examples/`.
- Kosika is Python-first, local-first, capability-secure. No external runtime deps.
- DO NOT invent features. Flag mismatches between preview and reality.
- Send findings to `god` via outbox. Keep them short and actionable.
