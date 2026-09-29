"use strict";

const $ = (id) => document.getElementById(id);
const state = {token: "", samples: [], project: null, file: "main.syn", view: "editor", output: "result", runs: [], current: null, busy: false, edited: false};
const storageKey = "kosika.workbench.draft.v1";
let toastTimer;
let saveTimer;

function el(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}
function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.setAttribute("aria-hidden", "true");
  svg.append(use);
  return svg;
}
function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").className = `toast${error ? " error" : ""}`;
  $("toast").hidden = false;
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 4200);
}
function setConnection(connected) {
  const node = $("connection");
  node.className = `connection ${connected ? "connected" : "offline"}`;
  node.replaceChildren(el("span", "status-dot"), document.createTextNode(connected ? "Local runtime connected" : "Runtime unavailable"));
}
function draftFrom(sample) {
  return {version: 1, name: sample.name, description: sample.description, files: {...sample.files}, entry: sample.entry, workflow: sample.workflow,
    inputsText: JSON.stringify(sample.inputs, null, 2), fixturesText: JSON.stringify(sample.fixtures, null, 2)};
}
function validateDraft(draft) {
  if (!draft || typeof draft !== "object" || !draft.files || Array.isArray(draft.files) || typeof draft.files !== "object") throw new Error("Choose a Kosika workspace JSON file.");
  const names = Object.keys(draft.files);
  if (!names.length || names.length > 16 || names.some((name) => !/^[A-Za-z0-9_/-]+\.syn$/.test(name) || name.startsWith("/") || typeof draft.files[name] !== "string" || draft.files[name].length > 65536)) throw new Error("A workspace needs 1–16 valid .syn source files.");
  if (!names.includes(draft.entry) || typeof draft.workflow !== "string") throw new Error("The workspace needs a valid entry file and workflow name.");
  const result = {version: 1, name: typeof draft.name === "string" ? draft.name.slice(0,80) : "Imported workspace", description: typeof draft.description === "string" ? draft.description.slice(0,160) : "Your local Kosika project.", files: {...draft.files}, entry: draft.entry, workflow: draft.workflow,
    inputsText: typeof draft.inputsText === "string" ? draft.inputsText : JSON.stringify(draft.inputs || {}, null, 2),
    fixturesText: typeof draft.fixturesText === "string" ? draft.fixturesText : JSON.stringify(draft.fixtures || {}, null, 2)};
  if (JSON.stringify(result).length > 524288) throw new Error("This workspace is too large for the workbench.");
  return result;
}
function saveDraft() {
  if (!state.project) return;
  try { localStorage.setItem(storageKey, JSON.stringify(state.project)); $("draft-status").textContent = "Draft saved locally"; }
  catch { $("draft-status").textContent = "Export to keep your draft"; }
}
function changed() {
  document.dispatchEvent(new Event("kosika-changed"));
  state.edited = true;
  $("draft-status").textContent = "Saving draft…";
  clearTimeout(saveTimer);
  saveTimer = setTimeout(saveDraft, 350);
  if ($("diagnostics").classList.contains("success")) $("diagnostics").hidden = true;
  if (state.current) $("execution-meta").textContent = "Previous run · source may have changed";
}
function loadProject(draft, ask = true) {
  if (state.busy) { toast("Wait for the current action to finish."); return; }
  if (ask && state.edited && !confirm("Open another example? Export your current workspace first if you want to keep this draft.")) return;
  clearTimeout(saveTimer);
  state.project = validateDraft(draft);
  document.dispatchEvent(new Event("kosika-project"));
  state.file = state.project.entry;
  state.edited = false;
  state.current = null;
  $("project-name").textContent = state.project.name;
  $("project-description").textContent = state.project.description;
  $("project-tree-name").textContent = slug(state.project.name);
  $("inputs-editor").value = state.project.inputsText;
  $("fixtures-editor").value = state.project.fixturesText;
  $("workflow-name").value = state.project.workflow;
  renderFiles();
  selectFile(state.file);
  setView("editor");
  clearResults();
  saveDraft();
}
function slug(text) { return text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "workspace"; }
function renderFiles() {
  $("file-tree").replaceChildren();
  $("file-tabs").replaceChildren();
  $("entry-file").replaceChildren();
  for (const name of Object.keys(state.project.files)) {
    for (const [parent, className] of [["file-tree", "file-button"], ["file-tabs", "file-tab"]]) {
      const button = el("button", `${className}${name === state.file ? " active" : ""}`);
      button.dataset.file = name;
      button.append(icon("file"), document.createTextNode(name));
      button.title = name;
      if (parent === "file-tabs") { button.setAttribute("role", "tab"); button.setAttribute("aria-selected", String(name === state.file)); }
      else button.setAttribute("aria-label", `Open ${name}`);
      button.addEventListener("click", () => selectFile(name));
      $(parent).append(button);
    }
    const option = el("option", "", name);
    option.value = name;
    $("entry-file").append(option);
  }
  $("entry-file").value = state.project.entry;
}
function selectFile(name) {
  state.file = name;
  $("source-editor").value = state.project.files[name];
  $("source-editor").setSelectionRange(0, 0);
  $("source-editor").scrollTop = 0;
  $("source-editor").scrollLeft = 0;
  $("editor-path").textContent = `${slug(state.project.name)} / ${name}`;
  document.querySelectorAll("[data-file]").forEach((button) => {
    button.classList.toggle("active", button.dataset.file === name);
    if (button.getAttribute("role") === "tab") button.setAttribute("aria-selected", String(button.dataset.file === name));
  });
  highlight();
  updateCursor();
}
function highlight() {
  const text = $("source-editor").value;
  const pattern = /\/\/[^\n]*|"(?:\\.|[^"\\])*"|\b(?:record|tool|agent|model|workflow|step|let|return|import|export|call|ask|execute|use_model|require|approval|can|cannot|source|mcp|fn)\b|\b(?:Text|Number|Bool|Null)\b|\b\d+(?:\.\d+)?\b|\b[A-Za-z_]\w*(?=\s*\()/g;
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    fragment.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    let type = "keyword";
    if (token.startsWith("//")) type = "comment";
    else if (token.startsWith('"')) type = "string";
    else if (/^(Text|Number|Bool|Null)$/.test(token)) type = "type";
    else if (/^\d/.test(token)) type = "number";
    else if (!/^(record|tool|agent|model|workflow|step|let|return|import|export|call|ask|execute|use_model|require|approval|can|cannot|source|mcp|fn)$/.test(token)) type = "function";
    fragment.append(el("span", `tok-${type}`, token));
    cursor = match.index + token.length;
  }
  fragment.append(document.createTextNode(text.slice(cursor) + "\n"));
  $("code-highlight").replaceChildren(fragment);
  $("line-numbers").textContent = Array.from({length: text.split("\n").length}, (_, i) => i + 1).join("\n") + "\n";
  syncScroll();
}
function syncScroll() {
  $("code-highlight").scrollTop = $("source-editor").scrollTop;
  $("code-highlight").scrollLeft = $("source-editor").scrollLeft;
  $("line-numbers").scrollTop = $("source-editor").scrollTop;
}
function updateCursor() {
  const parts = $("source-editor").value.slice(0, $("source-editor").selectionStart).split("\n");
  $("cursor-position").textContent = `Ln ${parts.length}, Col ${parts.at(-1).length + 1}`;
}
function setView(view) {
  state.view = view;
  ["editor", "runs", "guide", "integrations"].forEach((name) => { $(`${name}-view`).hidden = name !== view; });
  document.querySelectorAll("[data-view]").forEach((button) => { button.classList.toggle("active", button.dataset.view === view); button.setAttribute("aria-current", button.dataset.view === view ? "page" : "false"); });
  $("view-label").textContent = {editor: "Editor", runs: "Run history", guide: "Language guide", integrations: "Integrations"}[view];
  if (view === "runs") renderHistory();
}
function setOutput(name) {
  state.output = name;
  ["result", "timeline", "trace"].forEach((key) => { $(`${key}-content`).hidden = key !== name; });
  document.querySelectorAll("[data-output]").forEach((button) => { button.classList.toggle("active", button.dataset.output === name); button.setAttribute("aria-selected", String(button.dataset.output === name)); });
}
function diagnostics(message, success = false) {
  const node = $("diagnostics");
  node.textContent = message;
  node.className = `diagnostics${success ? " success" : ""}`;
  node.hidden = !message;
}
function clearResults() {
  $("run-metrics").hidden = true;
  diagnostics("");      $("result-content").replaceChildren();
      const ready = el("div", "ready-state");
      const badge = el("div", "ready-icon"); badge.append(icon("play"));
      const text = el("div"); text.append(el("h3", "", "Your program is ready."), el("p", "", "Kosika can check this workspace, run the workflow, and show every step it takes."));
      const hint = el("span", "ready-key"); hint.append(el("kbd", "", "Ctrl"), document.createTextNode(" + "), el("kbd", "", "Enter"));
      ready.append(badge, text, hint); $("result-content").append(ready);
  $("timeline-content").textContent = "Run a workflow to inspect its steps.";
  $("trace-content").textContent = "A successful run creates a replayable JSONL trace.";
  $("execution-meta").textContent = "Ready when you are";
  $("event-count").textContent = "0";
  ["copy-output", "replay-button", "download-trace"].forEach((id) => { $(id).disabled = true; });
  setOutput("result");
}
function snapshot() {
  if (!state.project) throw new Error("The workspace is still loading.");
  for (const field of $("generated-inputs").querySelectorAll("input, textarea, select")) {
    if (!field.checkValidity()) { field.reportValidity(); throw new Error("Correct the highlighted input before running."); }
  }
  let inputs, fixtures;
  try { inputs = JSON.parse(state.project.inputsText); } catch (error) { throw new Error(`Inputs are not valid JSON: ${error.message}`); }
  try { fixtures = $("execution-mode").value === "live" ? {} : JSON.parse(state.project.fixturesText); } catch (error) { throw new Error(`Model responses are not valid JSON: ${error.message}`); }
  return {files: {...state.project.files}, entry: state.project.entry, workflow: state.project.workflow, inputs, fixtures, mode: $("execution-mode").value};
}
async function api(action, payload) {
  let response;
  try { response = await fetch(`/api/${action}`, {method: "POST", headers: {"Content-Type": "application/json", "X-Kosika-Token": state.token}, body: JSON.stringify(payload)}); }
  catch { setConnection(false); throw new Error("The local runtime is not responding. Start it with python -m kosika.workbench, then reload this page."); }
  const result = await response.json();
  if (!response.ok) { const error = new Error(result.error || "The action could not be completed."); error.diagnostic = result.diagnostic; throw error; }
  setConnection(true);
  return result;
}
function busy(value, action = "run") {
  state.busy = value;
  document.body.classList.toggle("busy", value);
  ["run-button", "check-button", "format-button", "add-file", "import-project", "build-inputs", "execution-mode"].forEach((id) => { $(id).disabled = value; });
  $("replay-button").disabled = value || !state.current?.trace;
  $("run-button").querySelector("span").textContent = value && action === "run" ? "Running…" : "Run workflow";
}
async function execute(action) {
  if (state.busy || !state.project) return;
  setView("editor");
  busy(true, action);
  let payload;
  const name = state.project.name;
  try {
    payload = action === "check" ? {files: {...state.project.files}, entry: state.project.entry, workflow: state.project.workflow} : snapshot();
    const answer = await api(action, payload);
    if (action === "check") {
      const count = answer.summary.workflows.length;
      diagnostics(`All checks passed · ${answer.summary.files} file${answer.summary.files === 1 ? "" : "s"} · ${count} workflow${count === 1 ? "" : "s"} · capabilities verified`, true);
      document.dispatchEvent(new CustomEvent("kosika-schema", {detail: {schema: answer.input_schema, payload}}));
      toast("Program checked successfully.");
    } else {
      const record = {id: Date.now(), project: name, workflow: payload.workflow, time: new Date().toISOString(), status: "passed", files: payload.files, ...answer};
      state.runs.unshift(record); state.runs = state.runs.slice(0, 20);
      state.current = record;
      renderResult(record);
      toast("Workflow completed. Your trace is ready to replay.");
    }
  } catch (error) {
    if (action === "run") {
      const record = {id: Date.now(), project: name, workflow: state.project.workflow, time: new Date().toISOString(), status: "failed", error: error.message, diagnostic: error.diagnostic};
      state.runs.unshift(record); state.runs = state.runs.slice(0, 20); state.current = record;
      renderResult(record);
    } else { diagnostics(error.message); document.dispatchEvent(new CustomEvent("kosika-diagnostic", {detail: error.diagnostic})); }
    toast(action === "run" ? "Run stopped. See the diagnostic below." : "Check failed. See the diagnostic below.", true);
  } finally { $("run-count").textContent = state.runs.length; busy(false); }
}
function flattened(events, prefix = "") {
  const result = [];
  for (let i = 1; i < events.length - 1; i += 4) {
    const [step, call, returned] = events.slice(i, i + 3);
    if (!step || !call || !returned) continue;
    result.push({name: prefix + step.step, kind: call.event, target: call.workflow || call.function || call.model || call.agent || call.tool, call, returned});
    if (Array.isArray(returned.events)) result.push(...flattened(returned.events, prefix + step.step + " / "));
  }
  return result;
}
function timelineDesignSection(svg, stages, digest) {
  const section = el("details", "timeline-design");
  section.open = true;
  const summary = el("summary", "timeline-design-heading");
  const label = el("div", "timeline-design-label");
  label.append(icon("history"), document.createTextNode("Deterministic design timeline"));
  summary.append(label, el("span", "timeline-digest", digest.slice(0, 12)));
  const body = el("div", "timeline-design-body");
  const chipRow = el("div", "timeline-stage-row");
  for (const stage of stages) chipRow.append(el("span", `timeline-stage-chip stage-${stage}`, stage));
  const figure = el("div", "timeline-figure");
  figure.innerHTML = svg;
  const caption = el("p", "timeline-caption", `Design ${digest.slice(0, 12)} — identical input events always produce this exact diagram.`);
  body.append(chipRow, figure, caption);
  section.append(summary, body);
  return section;
}
async function renderTimelineDesign(record) {
  if (!Array.isArray(record.events) || !record.events.length) return;
  try {
    const design = await api("timeline", {events: record.events});
    if (state.current !== record) return;
    const section = timelineDesignSection(design.svg, design.design.stages, design.design.digest);
    $("timeline-content").prepend(section);
  } catch { /* The step list above stays usable even if the design render fails. */ }
}
function renderResult(record) {
  clearResults();
  document.dispatchEvent(new CustomEvent("kosika-run-rendered", {detail: record}));
  $("result-content").replaceChildren();
  if (record.status === "failed") {
    diagnostics(record.error);
    document.dispatchEvent(new CustomEvent("kosika-diagnostic", {detail: record.diagnostic}));
    $("result-content").append(el("div", "field-hint", "The workflow did not produce a successful result. Fix the diagnostic and run again."));
    $("execution-meta").textContent = "Run failed";
    return;
  }
  const heading = el("div", "result-heading");
  heading.append(icon("check"), document.createTextNode("Workflow completed"), el("span", "result-chip", "VALIDATED OUTPUT"));
  $("result-content").append(heading, el("pre", "json-result", JSON.stringify(record.result, null, 2)));
  $("execution-meta").textContent = `${record.duration_ms} ms · ${record.mode} run`;
  const timeline = flattened(record.events);
  $("event-count").textContent = timeline.length;
  $("timeline-content").replaceChildren();
  timeline.forEach((step, index) => {
    const row = el("details", "timeline-step");
    const summary = el("summary", "timeline-row");
    const detail = el("div", "timeline-detail");
    detail.append(el("strong", "", step.name), el("p", "", `${step.target} · completed and recorded`));
    summary.append(el("span", "timeline-circle", index + 1), detail, el("span", "timeline-kind", step.kind.replace("_call", "").toUpperCase()));
    row.append(summary);
    const inspect = el("div", "step-inspector");
    inspect.append(el("h4", "", "Inputs"), el("pre", "json-result", JSON.stringify(step.call.arguments || {}, null, 2)), el("h4", "", "Recorded outcome"), el("pre", "json-result", JSON.stringify(step.returned.result ?? step.returned, null, 2)));
    row.append(inspect);
    $("timeline-content").append(row);
  });
  renderTimelineDesign(record);
  $("trace-content").replaceChildren(el("pre", "trace-code", record.trace));
  ["copy-output", "replay-button", "download-trace"].forEach((id) => { $(id).disabled = false; });
}
async function replayCurrent() {
  if (state.busy || !state.current?.trace) return;
  busy(true, "replay");
  try {
    const result = await api("replay", {trace: state.current.trace});
    diagnostics(`Exact replay verified in ${result.duration_ms} ms. No model or tool calls were made.`, true);
    toast("Replay verified. Recorded output restored.");
    $("result-content").querySelector("pre").textContent = JSON.stringify(result.result, null, 2);
    setOutput("result");
  } catch (error) { diagnostics(error.message); toast("Replay rejected.", true); }
  finally { busy(false); }
}
function download(name, contents, mime = "application/json") {
  const url = URL.createObjectURL(new Blob([contents], {type: mime}));
  const link = el("a"); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function renderHistory() {
  $("history-list").replaceChildren();
  if (!state.runs.length) {
    const empty = el("div", "history-empty");
    empty.append(el("h2", "", "A clean slate."), el("p", "", "Run a workflow in the playground. Its result and trace will appear here."));
    const button = el("button", "button secondary", "Go to playground"); button.onclick = () => setView("editor"); empty.append(button);
    $("history-list").append(empty);
    return;
  }
  for (const record of state.runs) {
    const row = el("div", `history-row${record.status === "failed" ? " error-row" : ""}`);
    const details = el("div"); details.append(el("strong", "", record.project), el("small", "", `${record.workflow} · ${new Date(record.time).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"})}`));
    const button = el("button", "button secondary", "Inspect"); button.onclick = () => { state.current = record; setView("editor"); renderResult(record); };
    row.append(icon(record.status === "passed" ? "check" : "close"), details, el("span", "", record.status === "passed" ? "Completed" : "Failed"), el("span", "", record.duration_ms === undefined ? "—" : `${record.duration_ms} ms`), button);
    $("history-list").append(row);
  }
}

$("source-editor").addEventListener("input", () => { if (!state.project) return; state.project.files[state.file] = $("source-editor").value; highlight(); updateCursor(); changed(); });
$("source-editor").addEventListener("scroll", syncScroll);
$("source-editor").addEventListener("click", updateCursor);
$("source-editor").addEventListener("keyup", updateCursor);
$("source-editor").addEventListener("keydown", (event) => {
  if (event.key === "Tab" && !event.shiftKey) { event.preventDefault(); const editor = event.target; editor.setRangeText("  ", editor.selectionStart, editor.selectionEnd, "end"); editor.dispatchEvent(new Event("input")); }
});
for (const [id, field] of [["inputs-editor", "inputsText"], ["fixtures-editor", "fixturesText"], ["workflow-name", "workflow"]]) {
  $(id).addEventListener("input", () => { if (!state.project) return; state.project[field] = $(id).value; changed(); });
}
$("entry-file").addEventListener("change", () => { state.project.entry = $("entry-file").value; changed(); });
document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
document.querySelectorAll("[data-output]").forEach((button) => button.addEventListener("click", () => setOutput(button.dataset.output)));
$("check-button").onclick = () => execute("check");
$("run-button").onclick = () => execute("run");
$("replay-button").onclick = replayCurrent;
$("format-button").onclick = async () => {
  if (state.busy || !state.project) return;
  busy(true, "format");
  const file = state.file;
  const source = state.project.files[file];
  try {
    const result = await api("format", {source});
    if (state.project.files[file] !== source) throw new Error("Source changed while formatting. Try formatting again.");
    state.project.files[file] = result.source;
    if (state.file === file) selectFile(file);
    changed(); toast("Source formatted. Program behavior preserved.");
  } catch (error) { diagnostics(error.message); toast("Could not format source.", true); }
  finally { busy(false); }
};
$("export-project").onclick = () => { if (state.project) { download(`${slug(state.project.name)}.kosika.json`, JSON.stringify(state.project, null, 2)); state.edited = false; toast("Workspace exported."); } };
$("download-source").onclick = () => { if (state.project) download(state.file.split("/").at(-1), state.project.files[state.file], "text/plain"); };
$("download-trace").onclick = () => { if (state.current?.trace) download(`${slug(state.current.project)}-${state.current.id}.jsonl`, state.current.trace, "application/x-ndjson"); };
$("copy-output").onclick = async () => { try { await navigator.clipboard.writeText(JSON.stringify(state.current.result, null, 2)); toast("Result copied."); } catch { toast("Clipboard access unavailable. Select the result to copy it.", true); } };
$("keyboard-help").onclick = () => setView("guide");
$("guide-start").onclick = () => { const sample = state.samples.find((item) => item.id === "model"); if (sample) loadProject(draftFrom(sample)); };
$("add-file").onclick = () => { $("new-file-name").value = ""; $("file-error").textContent = ""; $("file-dialog").showModal(); $("new-file-name").focus(); };
$("close-dialog").onclick = $("cancel-dialog").onclick = () => $("file-dialog").close();
$("file-form").onsubmit = (event) => {
  event.preventDefault();
  const name = $("new-file-name").value.trim();
  if (!/^[A-Za-z0-9_/-]+\.syn$/.test(name) || name.startsWith("/") || name.includes("//")) { $("file-error").textContent = "Use a relative filename such as helpers.syn."; return; }
  if (Object.keys(state.project.files).some((file) => file.toLowerCase() === name.toLowerCase())) { $("file-error").textContent = "That filename already exists."; return; }
  if (Object.keys(state.project.files).length >= 16) { $("file-error").textContent = "The workbench supports up to 16 source files."; return; }
  state.project.files[name] = "// A new module. Export the declarations you want to share.\n";
  state.file = name; renderFiles(); selectFile(name); changed(); $("file-dialog").close(); toast(`${name} created.`);
};
$("import-project").onclick = () => $("project-picker").click();
$("project-picker").onchange = async (event) => {
  const file = event.target.files[0]; if (!file) return;
  try { if (file.size > 524288) throw new Error("Workspace files must be under 512 KiB."); loadProject(validateDraft(JSON.parse(await file.text()))); }
  catch (error) { toast(error.message, true); }
  finally { event.target.value = ""; }
};
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); execute(event.shiftKey ? "check" : "run"); }
  if (event.key === "?" && !["INPUT", "TEXTAREA"].includes(document.activeElement.tagName)) setView("guide");
});

// Theme toggle functionality
const themes = ["glass", "lab"];
let currentThemeIndex = 0;

function toggleTheme() {
  currentThemeIndex = (currentThemeIndex + 1) % themes.length;
  const newTheme = themes[currentThemeIndex];
  document.body.className = newTheme;
  localStorage.setItem("kosika.theme", newTheme);
  toast(`Switched to ${newTheme === "glass" ? "Glassmorphism" : "Lab"} theme`);
}

// Initialize theme from localStorage or default
function initializeTheme() {
  const savedTheme = localStorage.getItem("kosika.theme");
  if (savedTheme && themes.includes(savedTheme)) {
    currentThemeIndex = themes.indexOf(savedTheme);
    document.body.className = savedTheme;
  } else {
    // Default to glass theme
    currentThemeIndex = 0;
    document.body.className = "glass";
  }
}

// Add theme toggle button listener
$("theme-toggle").addEventListener("click", toggleTheme);
window.addEventListener("beforeunload", saveDraft);

async function initialize() {
  initializeTheme();
  busy(true, "load");
  try {
    const response = await fetch("/api/session");
    if (!response.ok) throw new Error("Could not initialize the local workbench.");
    const session = await response.json();
    state.token = session.token; state.samples = session.samples;
    document.dispatchEvent(new CustomEvent("kosika-session", {detail: session}));
    setConnection(true);
    for (const sample of state.samples) {
      const button = el("button", "sample-button");
      button.append(el("span", "sample-marker", "↳"), document.createTextNode(sample.name));
      button.title = sample.description; button.onclick = () => loadProject(draftFrom(sample));
      $("sample-list").append(button);
    }
    let draft = draftFrom(state.samples[0]);
    try { const saved = localStorage.getItem(storageKey); if (saved) draft = validateDraft(JSON.parse(saved)); } catch { /* An invalid local draft never blocks startup. */ }
    state.busy = false;
    loadProject(draft, false);
  } catch (error) { setConnection(false); diagnostics(error.message); toast(error.message, true); }
  finally { busy(false); }
}
document.addEventListener("DOMContentLoaded", initialize);
