"use strict";
let mapTimer, mapRevision = 0, mapKey = "", mapPending = false;
function mapSnapshot() {
  if (!state.project) return null;
  return {files: {...state.project.files}, entry: state.project.entry, workflow: state.project.workflow};
}
function invalidateMap() {
  const payload = mapSnapshot(); if (!payload) return;
  const key = JSON.stringify(payload);
  if (key === mapKey) return;
  mapKey = key; mapRevision++;
  clearTimeout(mapTimer);
  $("mindmap-tree").replaceChildren();
  $("mindmap-status").textContent = "Updating from your code…";
  $("mindmap-detail").textContent = "Pause typing to see how the program fits together.";
  $("mindmap-panel").setAttribute("aria-busy", "true");
  mapTimer = setTimeout(refreshMap, 700);
}
async function refreshMap() {
  if (mapPending || !state.project || !state.token) return;
  const revision = mapRevision, payload = mapSnapshot();
  mapPending = true;
  try {
    const result = await api("mindmap", payload);
    if (revision !== mapRevision) return;
    renderMindmap(result.mindmap, payload);
    $("mindmap-status").textContent = `Up to date · ${result.mindmap.node_count} nodes · ${result.mindmap.files.length} file(s)`;
  } catch (error) {
    if (revision !== mapRevision) return;
    $("mindmap-tree").replaceChildren();
    $("mindmap-status").textContent = "Map unavailable until the code checks successfully";
    $("mindmap-detail").textContent = error.message;
  } finally {
    mapPending = false;
    if (revision !== mapRevision) { clearTimeout(mapTimer); mapTimer = setTimeout(refreshMap, 700); }
    else $("mindmap-panel").setAttribute("aria-busy", "false");
  }
}
function buildHelpChips(map, payload) {
  const host = $("mindmap-help");
  host.replaceChildren();
  const counts = {record: 0, model: 0, agent: 0, tool: 0, workflow: 0, memory: 0};
  function count(node) {
    if (node.kind && Object.hasOwn(counts, node.kind)) counts[node.kind]++;
    node.children.forEach(count);
  }
  count(map.root);
  const has = (k) => counts[k] > 0;
  const decl = [];
  if (has("record")) decl.push(`${counts.record} record${counts.record === 1 ? "" : "s"}`);
  if (has("model")) decl.push(`${counts.model} model${counts.model === 1 ? "" : "s"}`);
  if (has("agent")) decl.push(`${counts.agent} agent${counts.agent === 1 ? "" : "s"}`);
  if (has("tool")) decl.push(`${counts.tool} tool${counts.tool === 1 ? "" : "s"}`);
  if (has("workflow")) decl.push(`${counts.workflow} workflow${counts.workflow === 1 ? "" : "s"}`);
  if (has("memory")) decl.push(`${counts.memory} memory${counts.memory === 1 ? "" : "s"}`);
  const chips = [];
  chips.push(el("span", "mindmap-help-line", `I see ${decl.join(", ") || "no declarations yet"}.`));
  const suggestions = [];
  if (has("model") && has("workflow")) suggestions.push({label: "Use a model in a step", action: "Use a typed model call as a step in your workflow", kind: "model"});
  if (has("tool")) suggestions.push({label: "Bound tool", action: "Bind a declared tool to a step with observe or execute", kind: "tool"});
  if (has("agent")) suggestions.push({label: "Run a bounded agent", action: "Run an agent with a declared model and capability set", kind: "agent"});
  if (has("record") && !has("workflow")) suggestions.push({label: "Add a workflow", action: "Turn one of your records into a workflow entry point", kind: "workflow"});
  if (!suggestions.length) suggestions.push({label: "Declare first", action: "Add a record, model, tool, or agent, then Kosika can suggest the next step", kind: "struct"});
  const suggestion = suggestions[0];
  chips.push(el("div", "mindmap-help-suggest",
    el("span", "mindmap-help-suggest-label", suggestion.label),
    el("span", `mindmap-help-suggest-kind map-kind map-${suggestion.kind}`, suggestion.kind.toUpperCase()),
    el("span", "mindmap-help-suggest-action", suggestion.action)
  ));
  host.append(...chips);
}

function renderMindmap(map, payload) {
  const host = $("mindmap-tree"); host.replaceChildren();
  $("mindmap-detail").textContent = map.notice;
  buildHelpChips(map, payload);
  $("mindmap-help").hidden = false;
  const root = el("div", "mindmap-root"); root.append(el("span", "eyebrow", "YOUR PROGRAM"), el("h3", "", map.root.title), el("p", "", map.root.explanation));
  const branches = el("div", "mindmap-branches");
  function render(node, depth) {
    const branch = el("details", `mindmap-node map-${node.kind}`);
    branch.open = depth < 2;
    const heading = el("summary"); heading.append(el("span", "map-kind", node.kind), el("strong", "", node.title));
    branch.append(heading, el("p", "", node.explanation));
    if (node.location) {
      const where = node.location;
      const jump = el("button", "map-source", `${where.file}:${where.line} ↗`);
      jump.setAttribute("aria-label", `Go to ${node.title} in ${where.file} line ${where.line}`);
      jump.onclick = () => {
        if (JSON.stringify(mapSnapshot()) !== JSON.stringify(payload)) { invalidateMap(); return; }
        selectFile(where.file);
        const editor = $("source-editor"), text = payload.files[where.file];
        const offset = text.split("\n").slice(0, where.line - 1).reduce((sum, line) => sum + line.length + 1, 0) + where.column - 1;
        editor.focus(); editor.setSelectionRange(offset, text.indexOf("\n", offset) < 0 ? text.length : text.indexOf("\n", offset));
        editor.scrollTop = Math.max(0, (where.line - 3) * parseFloat(getComputedStyle(editor).lineHeight));
        editor.scrollIntoView({block:"center", behavior:"auto"}); syncScroll(); updateCursor();
      };
      branch.append(jump);
    }
    if (node.children.length) {
      const children = el("div", "map-children");
      node.children.forEach(child => children.append(render(child, depth + 1)));
      branch.append(children);
    }
    return branch;
  }
  map.root.children.forEach(child => branches.append(render(child, 0)));
  host.append(root, branches);
}
document.addEventListener("kosika-changed", invalidateMap);
document.addEventListener("kosika-project", invalidateMap);
$("mindmap-refresh").onclick = () => { mapRevision++; clearTimeout(mapTimer); refreshMap(); };
$("show-mindmap").onclick = () => $("mindmap-panel").scrollIntoView({block:"start", behavior:"auto"});
$("mindmap-expand").onclick = () => $("mindmap-tree").querySelectorAll("details").forEach(item => {item.open = true;});
$("mindmap-collapse").onclick = () => $("mindmap-tree").querySelectorAll("details").forEach(item => {item.open = false;});
