# Kosika Studio – Next Components Roadmap

Below are eight high‑impact components that should be added to Kosika Studio.  They are ordered by utility (most useful first) and include a brief description, justification, target page, affected source files, required Kosika Designs functions, and acceptance criteria.

---

## 1. Command Palette
**What:** A searchable overlay (⌘ K / Ctrl K) that lets users run any Studio command, open files, or navigate pages.
**Why:** Power users need a fast, keyboard‑first way to access functionality without leaving the keyboard. It speeds up workflows and mirrors the experience of modern IDEs.
**Page:** Global – available on every page (header overlay).
**Changes:**
- `kosika/app_assets/index.html` – add a container element (`<div id="command‑palette"></div>`).
- `desktop/app/main.go` – expose a new endpoint `/cmd-palette` that returns the list of commands.
**Design Functions:** `designs_ui.kos` – `uiOverlay`, `uiSearchBox`; `designs_palette.kos` – `paletteAccent`; `designs_motion.kos` – `fadeIn`, `slideDown`.
**Done When:** The palette opens on the shortcut, filters commands as you type, and executing a command closes it.

---

## 2. Live Agents Panel
**What:** A floating panel that shows each active AI agent with its pointer colour and a live preview of what it is typing.
**Why:** When multiple agents collaborate, users need to see who is doing what in real‑time, preventing confusion.
**Page:** Dashboard (`/dashboard`).
**Changes:**
- `desktop/README.md` – document the new UI component.
- `kosika/app_assets/index.html` – add `<aside id="agents‑panel"></aside>`.
**Design Functions:** `designs_ui.kos` – `uiPanel`, `uiAvatar`; `designs_motion.kos` – `pulse`, `slideIn`.
**Done When:** Each agent appears with a coloured cursor icon and a scrolling text preview that updates live.

---

## 3. Run Timeline
**What:** A visual timeline that records each step of a run (e.g., prompts, responses, tool calls) with timestamps and status icons.
**Why:** Users need to audit and debug complex runs; a timeline gives a clear, chronological view.
**Page:** Run detail page (`/run/:id`).
**Changes:**
- `desktop/app/main.go` – add a new route `/run/:id/timeline` that returns JSON timeline data.
- `kosika/app_assets/index.html` – embed `<section id="run‑timeline"></section>`.
**Design Functions:** `designs_ui.kos` – `uiTimeline`, `uiBadge`; `designs_motion.kos` – `slideFade`.
**Done When:** The timeline displays each step with time, type, and success/failure indicator, and expands/collapses details.

---

## 4. Diff Review Card
**What:** A card that shows a diff between two versions of a text block with **Keep** and **Drop** actions.
**Why:** Editing prompts or generated content often requires reviewing changes; a dedicated UI speeds this up.
**Page:** Prompt editor (`/editor`).
**Changes:**
- `kosika/app_assets/index.html` – insert `<article class="diff‑card"></article>`.
- `desktop/app/main.go` – provide an API `/diff` that returns a structured diff.
**Design Functions:** `designs_ui.kos` – `uiCard`, `uiButton`; `designs_motion.kos` – `highlightFlash`.
**Done When:** The card shows added/removed lines, and clicking **Keep** or **Drop** updates the underlying content accordingly.

---

## 5. Notifications System
**What:** A toast‑style notification stack for success, error, and info messages.
**Why:** Immediate feedback is essential for actions like saving, model switching, or errors.
**Page:** Global (any page).
**Changes:**
- `kosika/app_assets/index.html` – add `<div id="notifications"></div>`.
- `desktop/app/main.go` – expose a WebSocket endpoint `/ws/notifications`.
**Design Functions:** `designs_ui.kos` – `uiToast`; `designs_motion.kos` – `fadeSlide`.
**Done When:** Notifications appear, auto‑dismiss after a configurable timeout, and are dismissible manually.

---

## 6. Model Picker
**What:** A dropdown or modal that lets the user select which LLM model to use for the current session.
**Why:** Different tasks benefit from different models; users should switch without leaving the page.
**Page:** Settings (`/settings`) and editor toolbar.
**Changes:**
- `kosika/app_assets/index.html` – add `<select id="model‑picker"></select>`.
- `desktop/app/main.go` – endpoint `/models` returning available models; `/set-model` to persist choice.
**Design Functions:** `designs_ui.kos` – `uiSelect`, `uiOption`; `designs_motion.kos` – `dropdownOpen`.
**Done When:** The picker lists all models, the current selection persists across reloads, and changing it updates the session.

---

## 7. Session Exporter
**What:** A button that bundles the current session (prompts, outputs, settings) into a downloadable JSON file.
**Why:** Users need to backup, share, or migrate their work.
**Page:** Session view (`/session/:id`).
**Changes:**
- `kosika/app_assets/index.html` – add `<button id="export‑session">Export</button>`.
- `desktop/app/main.go` – route `/session/:id/export` returning a JSON blob.
**Design Functions:** `designs_ui.kos` – `uiButton`; `designs_motion.kos` – `pulse` on hover.
**Done When:** Clicking the button triggers a file download named `session-<id>.json` containing all relevant data.

---

## 8. Inline Help Tooltip
**What:** Context‑aware tooltip that appears on hover/focus for UI elements, offering concise explanations.
**Why:** Reduces onboarding friction and helps new users discover hidden features.
**Page:** Everywhere – attached to elements via `data‑help="…"`.
**Changes:**
- `kosika/app_assets/index.html` – include a global `<div id="help‑tooltip"></div>`.
- `desktop/app/main.go` – optional endpoint `/help/:id` for dynamic content.
**Design Functions:** `designs_ui.kos` – `uiTooltip`; `designs_motion.kos` – `fadeIn`.
**Done When:** Hovering or focusing an element shows the tooltip positioned correctly, and it hides on mouseout/blur.

---

*All component proposals reference the real files listed in the prompt and the actual design functions from the Kosika Designs library.*
