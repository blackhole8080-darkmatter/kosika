"""A live, event-driven view of the Kosika agent city.

The isometric drawing comes from :mod:`kosika.metropolis`. This module only
wraps that drawing in a page and binds it to the workbench event stream, so
every pulse on screen is caused by a real ``kosika`` event rather than a
timer. Buildings are addressed by the authoritative ``data-agent`` name the
renderer already emits, which is the same identity the runtime puts on a
``kosika_tool_call`` event.
"""

from __future__ import annotations

import json
import secrets
import tempfile
import threading
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from .ast import (
    AgentCall,
    LiteralExpr,
    ModelDecl,
    Program,
    RecordDecl,
    Step,
    ToolCall,
    ToolDecl,
    WorkflowDecl,
)
from .city import City, build_city
from .metropolis import render_city_isometric
from .sse import EventStream, ReplayGapError

_STYLE = """
:root {
  --ink: #E6EDF5; --muted: #8FA0B4; --panel: #121822; --edge: #22303F;
  --live: #4ADE80; --warn: #F59E0B; --bad: #EF4444; --beam: #38BDF8;
}
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body {
  background: #070A0F; color: var(--ink); overflow: hidden;
  font: 13px/1.5 Menlo, Consolas, monospace;
}
#shell { display: grid; grid-template-columns: 1fr 320px; height: 100%; }
#stage { position: relative; overflow: hidden; cursor: grab; }
#stage.dragging { cursor: grabbing; }
#world { position: absolute; top: 0; left: 0; transform-origin: 0 0; }
#world svg { display: block; }

header {
  position: absolute; inset: 0 0 auto 0; padding: 14px 18px; z-index: 5;
  display: flex; gap: 18px; align-items: baseline; pointer-events: none;
  background: linear-gradient(#070A0F 55%, transparent);
}
h1 { margin: 0; font-size: 19px; letter-spacing: .3px; }
#stats { color: var(--muted); font-size: 12px; }
#link { margin-left: auto; display: flex; gap: 7px; align-items: center; font-size: 12px; }
#dot { width: 9px; height: 9px; border-radius: 50%; background: #64748B; }
#dot.on { background: var(--live); box-shadow: 0 0 9px var(--live); }
#dot.off { background: var(--bad); }

aside {
  background: var(--panel); border-left: 1px solid var(--edge);
  display: flex; flex-direction: column; min-height: 0;
}
aside h2 {
  margin: 0; padding: 11px 13px; font-size: 11px; letter-spacing: 1.3px;
  text-transform: uppercase; color: var(--muted);
  border-bottom: 1px solid var(--edge);
}
#rows { flex: 1; overflow-y: auto; }
#rows .empty { padding: 16px 13px; color: var(--muted); }
.row { padding: 8px 13px; border-bottom: 1px solid #18222E; }
.row .top { display: flex; gap: 7px; align-items: center; }
.chip {
  font-size: 10px; padding: 1px 6px; border-radius: 3px; text-transform: uppercase;
  background: #1C2733; color: var(--muted); letter-spacing: .6px;
}
.chip.call { background: #10331F; color: var(--live); }
.chip.hold { background: #3A2C0C; color: var(--warn); }
.chip.check { background: #14283C; color: var(--beam); }
.chip.err { background: #3A1717; color: var(--bad); }
.row .who { font-weight: 700; }
.row .seq { margin-left: auto; color: #5A6B7D; font-size: 10px; }
.row .det { color: var(--muted); font-size: 11px; word-break: break-all; }
footer { padding: 10px 13px; border-top: 1px solid var(--edge); color: var(--muted); font-size: 11px; }
.legend b { color: var(--ink); font-weight: 600; }
.hint { padding: 9px 13px; color: #5A6B7D; font-size: 11px; border-top: 1px solid var(--edge); }

/* Zoom controls, so the view is reachable without a keyboard. */
#controls {
  position: absolute; right: 16px; bottom: 16px; z-index: 6; display: flex;
  flex-direction: column; gap: 6px;
}
#controls button {
  width: 32px; height: 32px; border-radius: 7px; cursor: pointer;
  background: #121A26; color: #C6D4E6; border: 1px solid var(--edge);
  font: 15px/1 Menlo, Consolas, monospace;
}
#controls button:hover { background: #1B2534; color: #FFFFFF; border-color: #3A4A60; }
#controls button:focus-visible { outline: 2px solid var(--beam); outline-offset: 2px; }

/* A building wakes because a real event named its agent. */
g[data-agent] > polygon, g[data-agent] > rect { transition: filter .18s, opacity .18s; }
g[data-agent].active > polygon:first-of-type { filter: brightness(1.75) saturate(1.5); }
g[data-agent].active text { fill: #FFFFFF; }
@keyframes beacon { 0% { opacity: .95; } 100% { opacity: 0; } }
g[data-agent].active .beacon { animation: beacon 1.1s ease-out forwards; }
@keyframes hold { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }
g[data-agent].pending [data-prohibited] { animation: hold 1.6s ease-in-out infinite; }
  g[data-agent].pending polygon:first-of-type { filter: brightness(1.25) saturate(.7); }
  /* A working person steps out of their sub-sector onto the avenue. */
  g.avatar .figure { transition: transform 1.7s cubic-bezier(.35,0,.25,1); }
  g.avatar.walking .figure {
    transform: translate(var(--wx, 0px), var(--wy, 0px));
  }
  @keyframes stride { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-1.7px); } }
  g.avatar.walking .leg, g.avatar.walking .arm {
    animation: stride .44s ease-in-out infinite;
  }
  g.avatar.walking .leg:nth-of-type(2), g.avatar.walking .arm:nth-of-type(2) {
    animation-delay: .22s;
  }

polygon[data-district] { transition: opacity .2s; }
.district-live polygon[data-district] { fill-opacity: .95; }
@keyframes flow { to { stroke-dashoffset: -24; } }
polygon[data-avenue] { stroke-dasharray: 6 6; }
.district-live polygon[data-avenue] {
  stroke: var(--beam); stroke-width: 2.4; animation: flow .7s linear infinite;
}

/* Motion is decoration here, so honour the reader's system preference. */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .001ms !important;
  }
  g[data-agent].pending [data-prohibited] { animation: none; opacity: 1; }
  g[data-agent].active > polygon:first-of-type { filter: brightness(1.6) saturate(1.4); }
  /* A reader who asked for stillness keeps people at their sub-sectors. */
  g.avatar.walking .figure { transform: none; }
  g.avatar.walking .leg, g.avatar.walking .arm { animation: none; }
}
"""

_SCRIPT = """
"use strict";
const state = {token: "", cursor: "", abort: null, retry: 0, active: new Map()};
const $ = (id) => document.getElementById(id);
const domainOf = JSON.parse($("roster").textContent);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}
function clock(stamp) {
  try {
    return new Date(stamp).toLocaleTimeString([], {hour12: false});
  } catch { return ""; }
}
function status(on, label) {
  const dot = $("dot");
  dot.className = on ? "on" : "off";
  $("linktext").textContent = label;
}
function row(item) {
  const type = String(item.type || "event");
  const subject = type.includes("tool_call") ? "tool call"
    : type.includes("approval_pending") ? "awaiting approval"
    : type.includes("tool_return") ? "tool return"
    : type.includes("check") ? "check"
    : type.includes("error") || type.includes("failed") ? "error" : "run";
  const chip = type.includes("tool_call") ? "call"
    : type.includes("approval_pending") ? "hold"
    : type.includes("check") ? "check"
    : (type.includes("error") || type.includes("failed")) ? "err" : "";
  const who = item.payload && item.payload.agent;
  const line = el("div", "row");
  const top = el("div", "top");
  top.append(el("span", "chip " + chip, subject));
  if (who) top.append(el("span", "who", who));
  if (item.payload && item.payload.tool) {
    top.append(el("span", "det", "-> " + item.payload.tool));
  }
  if (item.workflow) top.append(el("span", "det", item.workflow));
  top.append(el("span", "seq", "#" + item.sequence));
  line.append(top, el("div", "det", clock(item.timestamp)));
  return line;
}
function push(item) {
  const rows = $("rows");
  rows.querySelector(".empty")?.remove();
  rows.prepend(row(item));
  state.cursor = item.sequence;
  while (rows.childElementCount > 60) rows.lastChild.remove();
}
function stepOut(group, hold) {
  const dx = parseFloat(group.dataset.walkDx || "0");
  const dy = parseFloat(group.dataset.walkDy || "0");
  if (!dx && !dy) return;
  group.style.setProperty("--wx", dx + "px");
  group.style.setProperty("--wy", dy + "px");
  group.classList.add("walking");
  clearTimeout(group.dataset.walkTimer);
  group.dataset.walkTimer = setTimeout(() => {
    group.classList.remove("walking");
  }, hold);
}
function glow(agent) {
  const domain = domainOf[agent];
  if (!domain) return;
  const slab = document.querySelector('polygon[data-grand="' + CSS.escape(domain) + '"]');
  if (!slab) return;
  slab.parentNode.classList.add("district-live");
  clearTimeout(state.active.get(domain));
  state.active.set(domain, setTimeout(() => {
    slab.parentNode.classList.remove("district-live");
    state.active.delete(domain);
  }, 2600));
}
function wake(agent) {
  if (!agent) return;
  const group = document.querySelector('g[data-agent="' + CSS.escape(agent) + '"]');
  if (!group) return;
  group.classList.add("active");
  stepOut(group, 2000);
  glow(agent);
  clearTimeout(group.dataset.timer);
  group.dataset.timer = setTimeout(() => {
    group.classList.remove("active");
    recount();
  }, 2200);
  recount();
}
function recount() {
  const lit = document.querySelectorAll("g[data-agent].active").length;
  $("working").textContent = lit;
}
function gate(agent) {
  if (!agent) return;
  const group = document.querySelector('g[data-agent="' + CSS.escape(agent) + '"]');
  if (!group) return;
  group.classList.add("pending");
  glow(agent);
}
function handle(item) {
  push(item);
  const payload = item.payload || {};
  if (String(item.type).includes("tool_call")) {
    wake(payload.agent);
  } else if (String(item.type).includes("approval_pending")) {
    gate(payload.agent);
  } else if (String(item.type).includes("check") && item.status === "failed") {
    status(true, "check failed");
  }
}
async function pump(signal) {
  const suffix = state.cursor ? "?after=" + encodeURIComponent(state.cursor) : "";
  const response = await fetch("/api/events" + suffix, {
    headers: {"X-Kosika-Token": state.token}, signal,
  });
  if (response.status === 409) { state.cursor = ""; return; }
  if (!response.ok || !response.body) return;
  status(true, "live");
  state.retry = 0;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    let cut;
    while ((cut = buffer.indexOf("\\n\\n")) !== -1) {
      for (const line of buffer.slice(0, cut).split("\\n")) {
        if (!line.startsWith("data:")) continue;
        try { handle(JSON.parse(line.slice(5).trim())); } catch { /* Skip bad frame. */ }
      }
      buffer = buffer.slice(cut + 2);
    }
  }
}
async function loop() {
  if (state.abort || !state.token) { setTimeout(loop, 900); return; }
  if (document.hidden) { setTimeout(loop, 2500); return; }
  state.abort = new AbortController();
  try {
    await pump(state.abort.signal);
  } catch {
    state.retry = Math.min(state.retry + 1, 6);
    status(false, "reconnecting");
  } finally {
    state.abort = null;
    setTimeout(loop, state.retry ? Math.min(700 * state.retry, 6000) : 1200);
  }
}
async function boot() {
  try {
    const response = await fetch("/api/session");
    const session = await response.json();
    state.token = session.token;
    status(true, "live");
    loop();
  } catch {
    status(false, "no session");
    setTimeout(boot, 2000);
  }
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) { state.abort?.abort(); }
});

/* Pan and zoom so a 29-building metropolis stays readable on a laptop. */
const stage = $("stage");
let scale = 1, offsetX = 24, offsetY = 64, drag = null, touched = false;
function apply() {
  $("world").style.transform =
    "translate(" + offsetX + "px," + offsetY + "px) scale(" + scale + ")";
}
function intrinsic() {
  const svg = document.querySelector("#world svg");
  if (!svg) return [2128, 1224];
  const box = svg.viewBox && svg.viewBox.baseVal;
  return box && box.width ? [box.width, box.height] : [2128, 1224];
}
function fit() {
  const [w, h] = intrinsic();
  const pad = 32;
  scale = Math.min((stage.clientWidth - pad) / w, (stage.clientHeight - pad) / h, 1.05);
  offsetX = (stage.clientWidth - w * scale) / 2;
  offsetY = Math.max(58, (stage.clientHeight - h * scale) / 2);
  apply();
}
function zoomBy(factor) {
  touched = true;
  const next = Math.min(3, Math.max(0.12, scale * factor));
  const box = stage.getBoundingClientRect();
  const cx = box.width / 2, cy = box.height / 2;
  offsetX = cx - (cx - offsetX) * (next / scale);
  offsetY = cy - (cy - offsetY) * (next / scale);
  scale = next;
  apply();
}
stage.addEventListener("wheel", (event) => {
  event.preventDefault();
  zoomBy(event.deltaY < 0 ? 1.12 : 0.89);
}, {passive: false});
stage.addEventListener("pointerdown", (event) => {
  drag = {x: event.clientX - offsetX, y: event.clientY - offsetY};
  touched = true;
  stage.classList.add("dragging");
  stage.setPointerCapture(event.pointerId);
});
stage.addEventListener("pointermove", (event) => {
  if (!drag) return;
  offsetX = event.clientX - drag.x;
  offsetY = event.clientY - drag.y;
  apply();
});
stage.addEventListener("pointerup", () => { drag = null; stage.classList.remove("dragging"); });
document.addEventListener("keydown", (event) => {
  if (event.target instanceof HTMLInputElement) return;
  if (event.key === "0") { touched = false; fit(); }
  if (event.key === "f") { touched = false; fit(); }
  if (event.key === "+" || event.key === "=") zoomBy(1.2);
  if (event.key === "-" || event.key === "_") zoomBy(1 / 1.2);
});
$("zoom-in").addEventListener("click", () => zoomBy(1.25));
$("zoom-out").addEventListener("click", () => zoomBy(1 / 1.25));
$("zoom-fit").addEventListener("click", () => { touched = false; fit(); });
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (!touched) fit(); }, 160);
});
fit();
boot();
"""


def _roster(city: City) -> dict[str, str]:
    """Map every agent name to the knowledge domain that houses it.

    The megastructure a person stands in is the domain their granted tools put
    them in, not the department that happens to employ them, so the glow that
    lights a landmark follows capability.
    """

    from .metropolis import _department_map, _domain_for

    departments = _department_map(city)
    return {
        person.name: _domain_for(person, departments.get(person.name, ""))
        for person in city.residents
    }


def render_city_live(city: City) -> str:
    """Return a self-contained page that animates the city from real events.

    The page carries no timer of its own: a building only lights up when the
    workbench stream delivers an event naming that agent.
    """

    drawing = render_city_isometric(city)
    roster = json.dumps(_roster(city), sort_keys=True).replace("<", "\\u003c")
    districts = ", ".join(district.name for district in city.districts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="color-scheme" content="dark" />
<meta name="description" content="A live isometric night view of the Kosika agent city, driven by real runtime events." />
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' fill='%23070A0F'/%3E%3Crect x='7' y='5' width='7' height='22' fill='%2322D3EE'/%3E%3Crect x='17' y='13' width='7' height='14' fill='%23F472B6'/%3E%3C/svg%3E" />
<title>{escape(city.name)} live city</title>
<style>{_STYLE}</style>
</head>
<body>
<div id="shell">
  <section id="stage">
    <header>
      <h1>{escape(city.name)} &#183; live city</h1>
      <span id="stats">{len(city.residents)} agents &#183; {len(city.districts)} districts
        &#183; <b id="working">0</b> working</span>
      <span id="link"><span id="dot" role="status" aria-live="polite"></span><span id="linktext">connecting</span></span>
    </header>
    <div id="controls">
      <button id="zoom-in" type="button" title="Zoom in (+)" aria-label="Zoom in">+</button>
      <button id="zoom-out" type="button" title="Zoom out (-)" aria-label="Zoom out">&minus;</button>
      <button id="zoom-fit" type="button" title="Fit to window (f)" aria-label="Fit to window">&#9633;</button>
    </div>
    <div id="world">{drawing}</div>
  </section>
  <aside>
    <h2>Event stream</h2>
    <div id="rows" role="log" aria-live="polite" aria-relevant="additions">
      <p class="empty">Waiting for kosika events&#8230;</p>
    </div>
    <div class="hint">Districts: {escape(districts)}</div>
    <footer class="legend">Drag to pan &#183; wheel to zoom &#183; <b>0</b> resets &#183;
      <b>f</b> fits. A building wakes only when a real event names its agent.
      Amber pulses mean an execute-capability tool is genuinely waiting for
      approval, so the city shows the gate instead of forcing it.</footer>
  </aside>
</div>
<script type="application/json" id="roster">{roster}</script>
<script>{_SCRIPT}</script>
</body>
</html>
"""


class _Fixture:
    """Deterministic stand-ins so real programs can run without external services.

    Tools return a value shaped by their declared output record and models return
    the same shape as JSON. The programs are real and the events are real; only
    the transport is local, which is the same fixture mode the workbench uses.
    """

    def __init__(self, program: Program) -> None:
        self.records: dict[str, RecordDecl] = {record.name: record for record in program.records}

    def value(self, type_name: str, depth: int = 0) -> Any:
        if depth > 6:
            return None
        if type_name.endswith("[]") or type_name.startswith("Array<"):
            return []
        if type_name in ("Text", "String"):
            return ""
        if type_name in ("Number", "Int", "Float"):
            return 0
        if type_name == "Bool":
            return False
        if type_name == "Null":
            return None
        record = self.records.get(type_name)
        if record is None:
            return {}
        return {field.name: self.value(field.type_name, depth + 1) for field in record.fields}

    def expr(self, type_name: str, depth: int = 0) -> Any:
        """Build a source expression that evaluates to a valid ``type_name``."""

        from .ast import ArrayExpr, RecordExpr

        if depth > 6:
            return LiteralExpr(None)
        if type_name.endswith("[]") or type_name.startswith("Array<"):
            return ArrayExpr(())
        if type_name in ("Text", "String"):
            return LiteralExpr("")
        if type_name in ("Number", "Int", "Float"):
            return LiteralExpr(0)
        if type_name == "Bool":
            return LiteralExpr(False)
        if type_name == "Null":
            return LiteralExpr(None)
        record = self.records.get(type_name)
        if record is None:
            return RecordExpr(())
        return RecordExpr(
            tuple((field.name, self.expr(field.type_name, depth + 1)) for field in record.fields)
        )

    def tool(self, declaration: ToolDecl, arguments: dict[str, Any]) -> Any:
        return self.value(declaration.output)

    def provider(self, declaration: ModelDecl) -> Any:
        from .model import ModelResponse

        body = json.dumps(self.value(declaration.output), sort_keys=True)

        class _Provider:
            def complete(self, request: Any) -> Any:
                return ModelResponse(content=body, finish_reason="stop")

        return _Provider()


def _harness(program: Program, fixture: "_Fixture") -> list[tuple[Program, str, str, str]]:
    """Build one isolated program per agent capability so every building can run.

    Each generated step calls a tool the agent already declares in ``can``, so
    the runtime performs genuine work and emits a genuine agent-scoped event.
    Every drive is its own program because the checker validates an execute
    tool against the whole program, and an execute tool must be declared as
    approval-gated before it will run at all.
    """

    tools = {tool.name: tool for tool in program.tools}
    records = {record.name: record for record in program.records}
    models = {model.name: model for model in program.models}
    drives: list[tuple[Program, str, str, str]] = []
    for agent in program.agents:
        for tool_name in agent.can:
            tool = tools.get(tool_name)
            if tool is None:
                continue
            source = records.get(tool.input) if tool.input else None
            arguments = (
                tuple((field.name, fixture.expr(field.type_name)) for field in source.fields)
                if source is not None
                else ()
            )
            step = Step(
                name=f"drive_{tool_name}",
                call=ToolCall(
                    agent_name=agent.name, tool_name=tool_name, arguments=arguments
                ),
                binding_type=tool.output,
            )
            name = f"drive_{agent.name}_{tool_name}"
            drives.append(
                (
                    Program(
                        records=list(program.records),
                        tools=list(program.tools),
                        agents=list(program.agents),
                        models=list(program.models),
                        workflows=[
                            WorkflowDecl(
                                name=name,
                                output_type=tool.output,
                                steps=(step,),
                                approval_required=tool.access == "execute",
                            )
                        ],
                    ),
                    name,
                    agent.name,
                    tool_name,
                )
            )
        model = models.get(agent.model or "")
        if agent.can or model is None:
            continue
        source = records.get(model.input)
        arguments = (
            tuple((field.name, fixture.expr(field.type_name)) for field in source.fields)
            if source is not None
            else ()
        )
        step = Step(
            name=f"ask_{agent.name}",
            call=AgentCall(agent_name=agent.name, arguments=arguments),
            binding_type=model.output,
        )
        name = f"drive_{agent.name}_model"
        drives.append(
            (
                Program(
                    records=list(program.records),
                    tools=list(program.tools),
                    agents=list(program.agents),
                    models=list(program.models),
                    workflows=[
                        WorkflowDecl(
                            name=name, output_type=model.output, steps=(step,)
                        )
                    ],
                ),
                name,
                agent.name,
                agent.model or "model",
            )
        )
    return drives


def _drive(root: Path, publish: Callable[[list[dict[str, Any]]], None], stop: threading.Event) -> None:
    """Run every workflow under ``root`` and publish the resulting traces."""

    from .durable import ApprovalPending, ExecutionSession
    from .execution import ExecutionLimits
    from .parser import parse
    from .runtime import run

    limits = ExecutionLimits(max_steps=200, max_tool_calls=64, max_model_calls=32, deadline_seconds=20.0)
    paths = sorted(path for path in root.rglob("*.kos") if ".git" not in path.parts)
    originals: list[tuple[Program, _Fixture]] = []
    drives: list[tuple[Program, str, str, str]] = []
    for path in paths:
        try:
            parsed = parse(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        fixture = _Fixture(parsed)
        originals.append((parsed, fixture))
        drives.extend(_harness(parsed, fixture))
    originals.sort(key=lambda item: not item[0].agents)

    for program, fixture in originals:
        if stop.is_set():
            return
        model_bindings = {
            model.name: fixture.provider(model) for model in program.models
        }
        tool_bindings = {tool.name: fixture.tool for tool in program.tools}
        for workflow in program.workflows:
            if stop.is_set() or workflow.parameters:
                continue
            events: list[dict[str, Any]] = []
            try:
                run(
                    program,
                    workflow.name,
                    None,
                    inputs={},
                    model_bindings=model_bindings,
                    tool_bindings=tool_bindings,
                    limits=limits,
                    _events_out=events,
                )
            except (RuntimeError, ValueError) as exc:
                events.append({"event": "run_failed", "error": str(exc)[:240]})
            publish(events)
            time.sleep(0.2)

    for program, name, agent_name, tool_name in drives:
        if stop.is_set():
            return
        fixture = _Fixture(program)
        raw: list[dict[str, Any]] = []
        # Each drive gets its own throwaway journal. A journal is bound to the
        # program it was opened for, so a leftover file in the shared temp
        # directory would make every later run of the same drive fail.
        with tempfile.TemporaryDirectory(prefix="kosika-city-") as journal_dir:
            session = ExecutionSession(
                Path(journal_dir) / f"{name}.jsonl",
                binding_id="kosika-city",
            )
            try:
                run(
                    program,
                    name,
                    None,
                    inputs={},
                    session=session,
                    model_bindings={model.name: fixture.provider(model) for model in program.models},
                    tool_bindings={tool.name: fixture.tool for tool in program.tools},
                    limits=limits,
                    _events_out=raw,
                )
            except ApprovalPending:
                raw.append(
                    {
                        "event": "approval_pending",
                        "agent": agent_name,
                        "tool": tool_name,
                        "workflow": name,
                    }
                )
            except (RuntimeError, ValueError) as exc:
                raw.append({"event": "run_failed", "error": str(exc)[:240]})
        publish(raw)
        time.sleep(0.12)


def _handler(page: str, stream: Any, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send(self, status: int, body: str, kind: str) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _authorised(self) -> bool:
            if secrets.compare_digest(self.headers.get("X-Kosika-Token", ""), token):
                return True
            self._send(403, '{"error":"Session expired."}', "application/json")
            return False

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(200, page, "text/html; charset=utf-8")
                return
            if path == "/api/session":
                self._send(200, json.dumps({"token": token, "samples": []}), "application/json")
                return
            if path == "/api/events":
                self._stream()
                return
            self._send(404, '{"error":"Not found."}', "application/json")

        def _stream(self) -> None:
            if not self._authorised():
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            query = self.path.split("?", 1)[1] if "?" in self.path else ""
            cursor: int | None = None
            for piece in query.split("&"):
                if piece.startswith("after="):
                    try:
                        cursor = int(piece[6:])
                    except ValueError:
                        cursor = None
            try:
                stream.acquire_subscriber()
            except ValueError:
                return
            try:
                while not stream.closed:
                    try:
                        batch = stream.wait_for(cursor, timeout=15.0)
                    except ReplayGapError:
                        cursor = max(0, stream.oldest_sequence - 1)
                        continue
                    if not batch:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                        continue
                    for event in batch:
                        frame = f"data: {json.dumps(event, sort_keys=True)}\n\n"
                        self.wfile.write(frame.encode("utf-8"))
                    self.wfile.flush()
                    cursor = batch[-1]["sequence"]
            except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
                return
            finally:
                stream.release_subscriber()

    return Handler


def serve_city(
    root: Path,
    host: str = "127.0.0.1",
    port: int = 8787,
    *,
    idle_delay: float = 1.0,
    name: str | None = None,
) -> None:
    """Serve the live city and drive it from real executions under ``root``.

    The stream only advances while a browser is watching, so an idle server
    costs nothing. Blocks until interrupted.
    """

    from .synapse_bridge import normalize_events

    city = build_city(root, name=name)
    page = render_city_live(city)
    stream = EventStream(buffer_size=16384, max_replay=16384)
    token = secrets.token_urlsafe(24)
    stop = threading.Event()

    def publish(raw: list[dict[str, Any]]) -> None:
        if not raw or not stream.subscriber_count:
            return
        try:
            stream.publish(
                normalize_events(raw, sequence_start=stream.next_sequence)
            )
        except ValueError:
            return

    def pump() -> None:
        while not stop.is_set():
            if stream.subscriber_count:
                try:
                    _drive(root, publish, stop)
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(idle_delay)

    worker = threading.Thread(target=pump, name="kosika-city", daemon=True)
    worker.start()
    server = ThreadingHTTPServer((host, port), _handler(page, stream, token))
    server.daemon_threads = True
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.shutdown()
        server.server_close()


__all__ = ["render_city_live", "serve_city"]
