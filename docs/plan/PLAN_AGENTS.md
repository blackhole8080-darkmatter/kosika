# PLAN_AGENTS.md

## Overview

This document outlines a concrete, step‑by‑step plan to expand Kosika’s agent ecosystem.  
The goal is to add a **Coder Agent** and its **Swarm**, introduce **Hands** that can interact with the screen, enable **Browser Hands** for web‑based tasks, manage **Permissions**, implement **Learned Routines**, and set up comprehensive **Kosika Logs**. Each step explains its importance, the files that will be modified or created, and clear indicators of completion.

---

## 1. Create the Coder Agent Skeleton  
**Why it matters:** Provides the core logic for writing, editing, and compiling code, the foundation for all future coding tasks.  
**Files touched:** `agents/coder_agent.py`, `agents/__init__.py`  
**How to tell it is done:**  
- `coder_agent.py` contains a class `CoderAgent` with methods `write_code`, `compile_code`, and `run_tests`.  
- Importable from `agents` package (`from agents import CoderAgent`).  
- Unit test `tests/test_coder_agent.py` passes.

---

## 2. Implement the Swarm Manager for the Coder Agent  
**Why it matters:** Enables parallel code generation, testing, and debugging, improving throughput and resilience.  
**Files touched:** `agents/coder_swarm.py`, `agents/__init__.py`  
**How to tell it is done:**  
- `CoderSwarm` class can spawn multiple `CoderAgent` instances.  
- Swarm can distribute a code task and aggregate results.  
- Integration test `tests/test_coder_swarm.py` confirms parallel execution.

---

## 3. Add Hand Agents for Screen Interaction  
**Why it matters:** Hands are the physical or virtual pointers that can click, drag, and type on the UI, allowing agents to manipulate the environment directly.  
**Files touched:** `agents/hand_agent.py`, `agents/__init__.py`  
**How to tell it is done:**  
- `HandAgent` exposes `move_to(x, y)`, `click()`, `type_text(text)`.  
- A demo script `demo/hand_demo.py` performs a simple UI interaction and logs success.

---

## 4. Implement Browser Hands for Web Interaction  
**Why it matters:** Enables agents to navigate, scrape, and automate web pages, expanding Kosika’s reach to online services.  
**Files touched:** `agents/browser_hand.py`, `agents/__init__.py`  
**How to tell it is done:**  
- `BrowserHand` can open a URL, click elements by selector, and extract page content.  
- Test `tests/test_browser_hand.py` opens a test page and verifies element interaction.

---

## 5. Design a Permissions System  
**Why it matters:** Safeguards against unauthorized actions, ensuring agents only perform tasks they are allowed to.  
**Files touched:** `config/permissions.yaml`, `agents/permission_manager.py`  
**How to tell it is done:**  
- `PermissionManager` loads `permissions.yaml` and validates actions.  
- A test `tests/test_permission_manager.py` confirms denial of disallowed actions.

---

## 6. Build Learned Routines Repository  
**Why it matters:** Stores reusable sequences of actions (e.g., “deploy to staging”) that agents can invoke, accelerating common workflows.  
**Files touched:** `routines/learned_routines.yaml`, `agents/routine_executor.py`  
**How to tell it is done:**  
- `RoutineExecutor` can load a routine by name and execute its steps.  
- Demo `demo/routine_demo.py` runs a simple routine and logs completion.

---

## 7. Integrate Kosika Logs with Structured Output  
**Why it matters:** Provides traceability, debugging, and auditability for all agent actions.  
**Files touched:** `logging/kosika_logger.py`, `agents/__init__.py`  
**How to tell it is done:**  
- `KosikaLogger` writes JSON‑structured logs to `logs/kosika.log`.  
- A sample log entry contains timestamp, agent ID, action, and status.  
- Log rotation is configured to keep the file size manageable.

---

## 8. Wire Agents to the Main Kosika Loop  
**Why it matters:** Ensures new agents are discovered, scheduled, and executed within the existing orchestration framework.  
**Files touched:** `kosika_main.py`, `agents/__init__.py`  
**How to tell it is done:**  
- `kosika_main.py` now imports `CoderAgent`, `HandAgent`, `BrowserHand`, and registers them with the scheduler.  
- Running `python kosika_main.py` shows agents listed in the startup log.

---

## 9. Add Automated Tests for End‑to‑End Agent Interaction  
**Why it matters:** Validates that agents can cooperate (e.g., CoderAgent writes code, HandAgent deploys it, BrowserHand verifies deployment).  
**Files touched:** `tests/test_agent_cooperation.py`  
**How to tell it is done:**  
- Test script orchestrates a full workflow and asserts success at each stage.  
- All tests pass in CI pipeline.

---

## 10. Document Agent Usage and Best Practices  
**Why it matters:** Provides developers and users with clear guidance on how to create, configure, and run new agents, ensuring maintainability.  
**Files touched:** `docs/agents.md`, `README.md` (section update)  
**How to tell it is done:**  
- `agents.md` contains a concise guide, example code snippets, and troubleshooting tips.  
- `README.md` references the new documentation and includes a quick‑start example.

---
