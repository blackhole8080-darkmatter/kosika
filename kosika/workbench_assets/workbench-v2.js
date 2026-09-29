"use strict";
let formShape = null;
function sourceKey() {
  return JSON.stringify([state.project.files, state.project.entry, state.project.workflow]);
}
function showDiagnostic(info) {
  if (!info) return;
  const box = $("diagnostics");
  box.append(el("p", "", info.hint));
  if (info.file && Object.hasOwn(state.project.files, info.file)) {
    const button = el("button", "button secondary", `Open ${info.file}:${info.line}:${info.column}`);
    const source = state.project.files[info.file];
    button.onclick = () => {
      if (state.project.files[info.file] !== source) { toast("Source changed. Check again for a current location.", true); return; }
      selectFile(info.file);
      const lines = source.split("\n");
      const offset = lines.slice(0, info.line - 1).reduce((n, line) => n + line.length + 1, 0) + info.column - 1;
      const editor = $("source-editor");
      editor.focus(); editor.setSelectionRange(offset, Math.min(offset + 1, source.length));
      editor.scrollTop = Math.max(0, (info.line - 3) * parseFloat(getComputedStyle(editor).lineHeight));
      syncScroll(); updateCursor();
    };
    box.append(button);
  }
}
function buildForm(schema) {
  const host = $("generated-inputs"); host.replaceChildren();
  let current;
  try { current = JSON.parse($("inputs-editor").value); } catch { host.textContent = "Fix the input JSON before generating fields."; return; }
  if (!current || typeof current !== "object" || Array.isArray(current)) { host.textContent = "Inputs must be an object."; return; }
  let count = 0;
  function field(node, path, value, parent) {
    if (++count > 128) throw new Error("Large record: use the JSON input editor for this workflow.");
    if (node.type === "object") {
      const group = el("fieldset"); group.append(el("legend", "", path.join(".") || "Workflow inputs"));
      for (const [key, child] of Object.entries(node.properties || {})) field(child, [...path, key], value?.[key], group);
      parent.append(group); return;
    }
    const label = el("label", "", `${path.join(".")} · ${node.type}`);
    const control = el(node.type === "boolean" ? "select" : node.type === "string" || node.type === "array" ? "textarea" : "input");
    control.setAttribute("aria-label", path.join("."));
    if (node.type === "boolean") for (const v of ["", "true", "false"]) { const option = el("option", "", v || "Choose a value"); option.value = v; control.append(option); }
    if (node.type === "number") { control.type = "number"; control.step = "any"; }
    control.value = value === undefined ? "" : node.type === "string" ? value : JSON.stringify(value);
    if (node.type === "null") { control.value = "null"; control.readOnly = true; }
    control.oninput = () => {
      try {
        let result = node.type === "string" ? control.value : JSON.parse(control.value);
        if (node.type === "number" && (typeof result !== "number" || !Number.isFinite(result))) throw new Error("Enter a finite number.");
        if (node.type === "array" && !Array.isArray(result)) throw new Error("Enter a JSON array.");
        const inputs = JSON.parse($("inputs-editor").value);
        let cursor = inputs;
        for (const key of path.slice(0, -1)) {
          if (!Object.hasOwn(cursor, key) || !cursor[key] || typeof cursor[key] !== "object" || Array.isArray(cursor[key])) Object.defineProperty(cursor, key, {value: {}, enumerable:true, configurable:true,writable:true});
          cursor = cursor[key];
        }
        Object.defineProperty(cursor, path.at(-1), {value: result, enumerable:true, configurable:true,writable:true});
        $("inputs-editor").value = state.project.inputsText = JSON.stringify(inputs, null, 2);
        control.setCustomValidity(""); changed();
      } catch { control.setCustomValidity("Enter a valid value of the declared type."); control.reportValidity(); }
    };
    label.append(control); parent.append(label);
  }
  try { field(schema, [], current, host); }
  catch (error) { host.textContent = error.message; }
}
document.addEventListener("kosika-schema", (event) => {
  const p = event.detail.payload;
  if (JSON.stringify([p.files, p.entry, p.workflow]) !== sourceKey()) { diagnostics("Source changed while checking. Check again."); return; }
  formShape = {key: sourceKey(), schema: event.detail.schema}; buildForm(formShape.schema); $("raw-inputs").open = false;
});
document.addEventListener("kosika-diagnostic", (event) => showDiagnostic(event.detail));
document.addEventListener("kosika-changed", () => {
  if (formShape && formShape.key !== sourceKey()) { formShape = null; $("generated-inputs").textContent = "Program changed. Build the input form again."; }
});
document.addEventListener("kosika-project", () => { formShape = null; $("generated-inputs").replaceChildren(); $("execution-mode").value = "fixture"; $("execution-mode").dispatchEvent(new Event("change")); });
document.addEventListener("kosika-session", (event) => {
  const live = event.detail.live;
  if (!live) return;
  const option = $("execution-mode").querySelector('[value="live"]'); option.disabled = false; option.textContent = `Live · ${live.model}`;
  $("execution-mode").onchange = () => {
    const isLive = $("execution-mode").value === "live";
    $("fixtures-editor").disabled = isLive;
    document.querySelector(".fixture-note p").textContent = isLive ? "Live inference is selected. Scripted responses are ignored; returned records are still validated." : "Predictable by design. These are scripted responses, not live AI inference.";
    $("provider-detail").textContent = $("execution-mode").value === "live" ? `${live.model} at ${live.endpoint}. Up to ${live.max_requests} requests/run, ${live.max_output_tokens} output tokens/request, ${live.timeout_seconds}s request timeout. Inputs will be sent to this local server. Responses may vary.` : "Fixture runs require no model server.";
  };
});
$("build-inputs").onclick = () => execute("check");
$("inputs-editor").addEventListener("input", () => { if (formShape) buildForm(formShape.schema); });
