"use strict";
let eventCursor = "";
let eventFeedBound = false;
let eventReconnectTimer;
let eventAbort = null;
const eventFeedMax = 12;

function eventFeedChip(type) {
  if (String(type).includes("error")) return "event-chip error";
  if (String(type).includes("approval")) return "event-chip approval";
  if (String(type).includes("check")) return "event-chip check";
  if (String(type).includes("replay")) return "event-chip replay";
  return "event-chip";
}
function eventFeedRow(item) {
  const row = el("div", "event-feed-row");
  const meta = el("div", "event-feed-meta");
  meta.append(
    el("span", eventFeedChip(item.type), String(item.type || "").replace(/^kosika_/, "").replace(/_/g, " ")),
    el("span", "event-feed-seq", `#${item.sequence}`)
  );
  const subject = item.workflow ? `${item.workflow}${item.step ? " · " + item.step : ""}` : "runtime";
  const body = el("div", "event-feed-body");
  body.append(el("strong", "", subject));
  if (item.status) body.append(el("span", "event-feed-status", item.status));
  const clock = el("time", "event-feed-time", new Date(item.timestamp).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"}));
  row.append(meta, body, clock);
  return row;
}
function eventFeedApply(batch) {
  const rows = $("event-feed-rows");
  rows.querySelector(".event-feed-empty")?.remove();
  for (const item of batch) {
    if (!item || typeof item.sequence !== "number" || typeof item.event_id !== "string") continue;
    rows.prepend(eventFeedRow(item));
    eventCursor = item.event_id;
  }
  while (rows.childElementCount > eventFeedMax) rows.lastChild.remove();
}
function eventFeedFrame(frame) {
  for (const line of frame.split("\n")) {
    if (!line.startsWith("data:")) continue;
    try { eventFeedApply([JSON.parse(line.slice(5).trim())]); } catch { /* Skip malformed frames. */ }
  }
}
async function connectEventFeed(signal) {
  const suffix = eventCursor ? `?after=${encodeURIComponent(eventCursor)}` : "";
  const response = await fetch(`/api/events${suffix}`, {headers: {"X-Kosika-Token": state.token}, signal});
  if (response.status === 409) { eventCursor = ""; return; }
  if (!response.ok || !response.body) return;
  setConnection(true);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, {stream: true});
    let index;
    while ((index = buffer.indexOf("\n\n")) !== -1) {
      eventFeedFrame(buffer.slice(0, index));
      buffer = buffer.slice(index + 2);
    }
  }
}
function pollEventFeed() {
  if (eventAbort || !state.token) { eventFeedSchedule(); return; }
  if (document.hidden) { eventFeedSchedule(2500); return; }
  eventAbort = new AbortController();
  connectEventFeed(eventAbort.signal)
    .catch(() => { /* Reconnect below; the connection indicator shows reachability. */ })
    .finally(() => { eventAbort = null; eventFeedSchedule(); });
}
function eventFeedSchedule(delay = 1200) {
  clearTimeout(eventReconnectTimer);
  eventReconnectTimer = setTimeout(pollEventFeed, delay);
}
document.addEventListener("kosika-session", () => {
  if (eventFeedBound) return;
  eventFeedBound = true;
  eventFeedSchedule(300);
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) { eventAbort?.abort(); }
  else if (eventFeedBound) { clearTimeout(eventReconnectTimer); eventFeedSchedule(150); }
});
