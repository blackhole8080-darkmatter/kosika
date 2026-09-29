# Synapse to Kosika migration

Kosika is the official project and language name. Synapse was the earlier internal codename.

## v0.1 rename

- Project directory: `synapse_v0_1` -> `kosika_v0_1`
- Python package: `synapse` -> `kosika`
- CLI identity: `synapse` -> `kosika`
- Package metadata: `synapse-language-v0-1` -> `kosika-language-v0-1`
- Test module: `test_synapse.py` -> `test_kosika.py`
- Source extension: `.kos` remains provisional while the grammar is still evolving

The v0.1 language behavior is unchanged by this branding migration.

The migrated prototype now includes a real local MCP stdio client adapter and a deterministic
protocol-compatible test server. Live MCP execution is now gated by a pinned JSON manifest that
checks server identity, protocol version, exact tool schemas, and the canonical schema digest.
Kosika validates tool arguments before invocation and returned structured data afterward. The
original mock boundary remains available for the baseline example.
