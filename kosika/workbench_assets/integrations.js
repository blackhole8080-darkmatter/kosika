"use strict";
let integrationSession;
document.addEventListener("kosika-session", (event) => {
  integrationSession = event.detail;
  const live = event.detail.live;
  $("ai-status").textContent = live ? "Configured · inference not checked" : "Fixtures available";
  $("ai-description").textContent = live ? `${live.model} · ${live.endpoint}` : "Deterministic responses work offline. Configure a local provider when starting Kosika to enable live inference.";
  $("ai-limits").textContent = live ? `${live.max_requests} requests per run · ${live.max_output_tokens} output tokens per request · ${live.timeout_seconds}s request timeout` : "No API keys required. Inputs and responses stay in your local workspace.";
  const mcp = event.detail.mcp;
  $("mcp-status").textContent = mcp ? "Configured · not yet verified" : "Not enabled";
  $("verify-mcp").disabled = !mcp;
  $("try-mcp").disabled = !mcp;
  $("mcp-description").textContent = mcp ? "Text lab runs as a local stdio process. Its only grant is counting supplied text. Every execution checks the pinned contract." : "Start with --mcp-demo to enable the bundled read-only text server. The browser cannot supply process commands.";
  if (mcp) $("mcp-manifest").textContent = JSON.stringify(mcp.manifest, null, 2);
});
$("verify-mcp").onclick = async () => {
  const button = $("verify-mcp"); button.disabled = true;
  $("mcp-status").textContent = "Checking handshake and tool contract…";
  try {
    await api("inspect-mcp", {});
    $("mcp-status").textContent = `Verified at ${new Date().toLocaleTimeString()} · checked again before every call`;
    toast("MCP handshake and pinned schemas verified.");
  } catch (error) { $("mcp-status").textContent = `Verification failed: ${error.message}`; toast("MCP verification failed. Execution remains gated.", true); }
  finally { button.disabled = false; }
};
$("try-mcp").onclick = () => {
  const sample = state.samples.find(item => item.id === "mcp-text");
  if (sample) loadProject(draftFrom(sample));
};
$("ai-playground").onclick = () => setView("editor");
document.addEventListener("kosika-run-rendered", event => {
  const record = event.detail;
  const box = $("run-metrics"); box.replaceChildren();
  if (record.status !== "passed") { box.hidden = true; return; }
  box.hidden = false;
  let requests = 0, tokens = 0, known = false, latency = 0;
  // Live mode shares one provider budget across model declarations.
  const values = Object.values(record.telemetry || {});
  const metrics = record.mode === "live" ? values.slice(0, 1) : values;
  for (const list of metrics) for (const item of list) {
    requests++; latency += item.latency_ms || 0;
    if (typeof item.usage?.total_tokens === "number") { tokens += item.usage.total_tokens; known = true; }
  }
  for (const [label, value] of [["Execution", record.mode === "live" ? "Live model" : "Fixture models"], ["Model requests", requests], ["Reported tokens", known ? tokens : "Not reported"], ["Model latency", `${Math.round(latency)} ms`]]) {
    const card = el("div", "metric"); card.append(el("span", "", label), el("strong", "", String(value))); box.append(card);
  }
});
