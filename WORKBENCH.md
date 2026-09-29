# Kosika Workbench

A local browser workspace for editing, checking, running, and replaying Kosika programs.

## Windows desktop launcher

Run `powershell -NoProfile -ExecutionPolicy Bypass -File desktop\install.ps1`
from the project directory to install **Kosika** and **Stop Kosika** shortcuts on
your Desktop and in your Start menu. The installer uses your existing Python
3.10+ installation; `-PythonPath` can select it explicitly. No administrator
rights, scheduled task or automatic sign-in startup is required.

**Kosika** starts a hidden local host and opens an Edge/Chrome app window without
normal browser tabs. Reopening it reuses the same host. Closing the window does
not stop the host; use **Stop Kosika** to stop it without killing other Python or
browser processes. It starts again from the shortcut after a reboot.

This is a lightweight desktop wrapper, not a self-contained native installer.
Keep this project folder, Python, and Edge or Chrome installed. If you move the
project, reinstall the shortcuts after removing the old ones. The local service
uses `127.0.0.1:8792`; a port conflict fails rather than connecting to an unrelated
service. Fixture mode is the default; desktop launch grants no live model or MCP
integration. The existing workbench Host/Origin/token protections still apply.

State and logs live in `%LOCALAPPDATA%\Kosika`, with a separate browser profile
under `Browser`. Drafts persist at the stable local address; they are separate
from drafts in your normal browser or Codex preview. Use the workbench export
controls to back up important work. `python -m kosika.desktop --status` is a
read-only health check. Logs are in `desktop.log`.

To remove the launcher, use **Stop Kosika**, delete its Desktop/Start-menu shortcuts,
and optionally remove `%LOCALAPPDATA%\Kosika` after backing up drafts. Removing
that profile deletes its saved browser drafts. The repository remains untouched.

## Browser launch

From the repository folder, start:

```powershell
python -B -m kosika.workbench --port 8765
```

Open http://127.0.0.1:8765. Stop the server with Ctrl+C.

## Try a program

1. Choose an example from the library.
2. Edit its `.syn` files and JSON inputs.
3. Select **Check**, then **Run workflow**.
4. Inspect the result, timeline, or JSONL trace. **Replay** reuses recorded outcomes.

Model responses are deterministic fixtures entered in the model fixtures panel. By default this interface does not connect to live providers. An explicitly configured loopback model is optional; external MCP servers are unavailable. It uses the real Kosika parser, checker, interpreter, and replay engine. Only the built-in allowlisted read-only mock tools are available; arbitrary shell commands and external mutations are unavailable.

## Workspace and data

Your draft is saved in this browser's local storage. Export a workspace JSON file for a portable backup and import it later. Source downloads and trace downloads are also available. Run history lasts for the current page session. Runs use temporary source files and do not edit repository files. Avoid putting secrets in fixtures or traces.

The server binds only to `127.0.0.1`, validates Host and Origin, and requires a per-process token for API calls. Requests, files, fixtures, and workflow expansion are bounded. This is a single-user local development tool, not a hardened public hosting service. Do not expose it through a public proxy.

## Workbench v0.2

- **Build input form** checks the current workflow and generates nested fields from its record schemas. Arrays use a JSON field. The raw JSON editor remains available. Source changes invalidate the form.
- Parser diagnostics offer an exact file/line/column jump when the parser supplies one. Other errors retain their message without inventing a location.
- Expand a **Timeline** step to inspect the arguments and recorded outcome, including nested workflow calls.
- Choose **Document assistant** to summarize explicitly supplied text. Saving summaries with approval is a future interface milestone; this sample has no filesystem capability.

### Optional local inference

Start your compatible local model server separately, then explicitly configure its installed model:

```powershell
python -B -m kosika.workbench --port 8765 --live-model YOUR_INSTALLED_MODEL --live-url http://127.0.0.1:11434/v1
```

Choose **Live** in the execution selector to send input records to that local server. Fixture mode remains the default and selecting a sample resets it. URLs and model identifiers are host configuration, never accepted from browser request payloads. No credentials or model downloads are managed by this interface.

The live transport disables proxies and redirects, accepts only a numeric loopback HTTP endpoint under `/v1`, and limits response bodies to 256 KiB. One shared provider budget permits four requests per run across all model declarations, at most 1024 requested output tokens per request, no retries, a 20-second request timeout, and a 60-second cooperative deadline. The deadline is checked between requests and after responses; it does not forcibly interrupt an in-flight request. Provider-enforced output limits may vary. Reported usage is recorded; no total billable token cap or cost estimate is claimed.

Fresh inference can vary. Exact replay uses the captured output and makes no additional inference request. Tests exercise this path against a deterministic local HTTP server; that is not proof of inference quality or that a particular installed model fits local hardware.

## Workbench v0.3: integrations and motion

The Integrations page distinguishes configured providers from verified connections. Run telemetry shows model requests, reported total tokens when available, and model latency. Missing usage is never displayed as zero cost. Entry and feedback animations honor the operating system's reduced-motion preference.

To enable the bundled MCP text server:

```powershell
python -B -m kosika.workbench --port 8765 --mcp-demo
```

Open **Integrations**, choose **Verify connection**, then **Try MCP example**. The server is a real child process using the existing MCP stdio adapter. Its sole operation, `inspect_text`, counts whitespace-separated words and Unicode characters in supplied text. It cannot read files, invoke commands, or access a network. Every call verifies protocol version, server identity/version, tool discovery, and exact pinned input/output schemas. Each process is closed after its operation, including failed handshakes. The pin is supplied by the host, never accepted from discovery or the browser. Execution uses the runtime's host-tool binding; its recorded transport label is `host`, while that binding internally uses MCP stdio.

The browser cannot add arbitrary servers, commands, endpoints, credentials, or mutation grants. Third-party servers and Streamable HTTP remain future host integrations. Use `--live-model YOUR_MODEL` alongside `--mcp-demo` to make a configured local model available. A configured model may still fail to load on the current hardware.

## Workbench v0.4: validated local event stream

The workbench exposes a read-only Server-Sent Events endpoint at `/api/events`. It uses the same loopback Host/Origin checks and per-process token as the API; send `X-Kosika-Token` from the `/api/session` response. Successful fixture or configured-local runs publish normalized `synapse.kosika.event/v1` events while preserving the raw JSONL trace returned by the run API.

Reconnect with either `Last-Event-ID: <event-id>` or `?after=<sequence-or-event-id>`. Use `?limit=N` to bound each replay batch. The stream retains a bounded in-memory buffer; a cursor older than the retained range returns HTTP 409 so the client can reload a JSONL source of truth instead of receiving an ambiguous partial replay. Slow or disconnected clients are bounded by the subscriber limit and cleaned up when their socket closes. The server sends heartbeat comments while waiting for new events.

For durable local replay across process restarts, start the host with:

```powershell
python -B -m kosika.workbench --port 8765 --event-trace artifacts\kosika-events.jsonl```

The event JSONL is validated for strict JSON, contiguous sequence numbers, unique IDs, and bounded metadata before it is persisted or streamed. The endpoint is observation-only; it cannot execute workflows, mutate files, control the desktop, or grant capabilities. WebSockets remain unnecessary until a future feature needs bidirectional control.

## Automatic code mindmap

The playground builds a mindmap automatically after 700 ms without a source change. **Code mindmap** jumps to the map; expand branches to inspect data contracts, ordered workflow steps, model/agent declarations, and tool capabilities. Source links navigate to token-derived file and line locations, including imported modules.

The map is derived from the checked AST. It does not call a model, run a tool, or prove the availability of a declared capability. It covers the entry file and its reachable imports, not unrelated files in the workspace. The selected workflow is marked as the entry; other declared workflows are shown without assuming they execute. Workflow calls remain references rather than recursively expanding the graph.

Editing source invalidates the previous map immediately. Results from older requests are discarded. Syntax/static-check failures show a diagnostic rather than an old diagram. Maps are bounded to 400 nodes, and the existing source/request limits also apply. Input JSON edits do not trigger another source analysis. Refresh, expand, and collapse controls are keyboard accessible; this is a static program explanation, not an execution timeline or inferred business intent.
