"""A census of the Kosika platform on this computer, for Studio's Brain page.

Every part found, and the names of the parts it uses:

* each agent in the agents folder (a folder with a main.kos), and from its
  .kos files: the std modules it imports, the system tools and MCP servers
  its tools come from, its memories, and its models (all reached through
  the router);
* the router and the models it lists in models/router.json (ids only;
  keys are never read);
* the Brain, which every agent importing std/brain_core.kos senses and
  records through, and its timeline;
* the coder swarm, from its work folder, and Studio, with the pages it
  opens.

app_assets/brain.kos draws the list with std/designs_network.kos
(net25_census). Nothing here decides how the platform looks; it only says
what is there.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

IMPORT = re.compile(r'^\s*import\s+"(?:\.\./)*(?:kosika/)?std/([\w]+)\.kos"', re.M)
SOURCE = re.compile(r'^\s*source\s+(system|mcp)\s+"([^"]+)"', re.M)
MODEL = re.compile(r"^\s*model\s+\w+", re.M)
MEMORY = re.compile(r"^\s*memory\s+(\w+)", re.M)

#: std modules that are the Brain itself.
BRAIN_MODULES = {"brain", "brain_core", "brain_net", "brain_view"}
#: std modules that are Kosika maths, drawn with as a Design is.
MATHS_MODULES = {"math", "maths", "stats", "nn", "algorithms", "scene", "layers"}
#: Agent folders with a name of their own in Studio.
NAMED = {"assistant": "Kosi", "ai": "Kosika AI", "router": "router agent"}
#: A kit's model, by the router's own names.
KIT_MODELS = {"kosika-router": "router"}
KIT = re.compile(r'\{\s*name:\s*"([^"]+)"(.*?)\}', re.S)
SERVER_LINE = re.compile(r'"([\w-]+)\|([^|"]+)\|')
#: What a system tool does, for the page's hover.
SYSTEM_ABOUT = {
    "web": "reads a web page",
    "files": "reads and writes files in a granted folder",
    "shell": "runs a command, after a yes",
    "notify": "tells you something",
    "agents": "asks another agent",
    "log": "keeps a note of what happened",
    "services": "starts and stops local services",
    "outside": "acts outside Kosika, after a yes",
    "kosika": "runs Kosika itself",
}


def _name(folder: str) -> str:
    return NAMED.get(folder, folder)


def _about(main: Path) -> str:
    """The first comment line of an agent's main.kos, short."""

    try:
        for line in main.read_text(encoding="utf-8", errors="replace").splitlines()[:12]:
            said = line.strip().lstrip("/").strip()
            if line.strip().startswith("//") and said:
                # "main.kos: the assistant ..." reads better without the file name.
                said = re.sub(r"^[\w./-]+\.kos\s*[:—-]\s*", "", said)
                return said[:110]
    except OSError:
        pass
    return "an agent"


def agent_parts(folder: Path, router: bool) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """The agents in a folder, and every part they name."""

    agents: list[dict[str, Any]] = []
    named: dict[str, dict[str, Any]] = {}

    def part(name: str, kind: str, about: str) -> str:
        named.setdefault(name, {"name": name, "kind": kind, "about": about, "uses": []})
        return name

    try:
        found = sorted(p for p in folder.iterdir() if (p / "main.kos").is_file())
    except OSError:
        found = []
    for home in found:
        uses: list[str] = []
        text = ""
        for source in sorted(home.rglob("*.kos")):
            # Runs and memories are what an agent did, not what it is.
            if {"runs", "memory", "__pycache__"} & set(source.relative_to(home).parts):
                continue
            try:
                text += source.read_text(encoding="utf-8", errors="replace") + "\n"
            except OSError:
                continue
        for module in IMPORT.findall(text):
            if module in BRAIN_MODULES:
                uses.append(part("Brain", "brain", "the one intelligence layer: senses, recalls, thinks, checks, records"))
            elif module.startswith("designs") or module in MATHS_MODULES:
                uses.append(part(f"std/{module}", "design", "Kosika maths and Designs: every number and point computed"))
            else:
                uses.append(part(f"std/{module}", "skill", f"Kosika's standard library: {module}"))
        for kind, name in SOURCE.findall(text):
            if kind == "mcp":
                uses.append(part(f"{name} (MCP)", "mcp", f"an MCP server the agent's tools reach ({name})"))
            else:
                uses.append(part(name, "skill", SYSTEM_ABOUT.get(name, f"the {name} tool")))
        for memory in sorted(set(MEMORY.findall(text))):
            uses.append(part(f"{_name(home.name)} {memory}", "memory", f"what {_name(home.name)} remembers ({memory}), recalled by meaning"))
        if router and MODEL.search(text):
            uses.append("router")
        # A hands kit: each kit is an agent with a cursor of its own, given
        # skills, MCP servers and a model by name.
        servers_about = dict(SERVER_LINE.findall(text))
        kits = home / "kits.kos"
        try:
            kit_text = kits.read_text(encoding="utf-8", errors="replace") if kits.is_file() else ""
        except OSError:
            kit_text = ""
        for kit, body in KIT.findall(kit_text):
            if "servers:" not in body:
                continue
            kit_uses: list[str] = []
            for field, kind in (("skills", "skill"), ("servers", "mcp")):
                listed = re.search(field + r":\s*\[(.*?)\]", body, re.S)
                for name in re.findall(r'"([^"]+)"', listed.group(1) if listed else ""):
                    about = servers_about.get(name, f"an MCP server ({name})") if kind == "mcp" else f"a hands skill: {name}"
                    kit_uses.append(part(f"{name} (MCP)" if kind == "mcp" else f"skill {name}", kind, about))
            model = re.search(r'model:\s*"([^"]+)"', body)
            if model:
                wanted = KIT_MODELS.get(model.group(1), model.group(1))
                kit_uses.append(part(wanted, "model", "a model a hands kit is given"))
            kit_name = f"{kit} (hands)"
            named[kit_name] = {"name": kit_name, "kind": "agent", "about": "a hands agent with its own cursor and kit",
                               "uses": sorted(set(kit_uses))}
            uses.append(kit_name)
        agents.append({"name": _name(home.name), "kind": "agent", "about": _about(home / "main.kos"),
                       "uses": sorted(set(uses))})
    return agents, named


def model_parts(router_json: Path) -> list[dict[str, Any]]:
    """The router and the models it lists: ids only, never keys."""

    try:
        config = json.loads(router_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    slots = config.get("models") if isinstance(config, dict) else None
    if not isinstance(slots, dict):
        return []
    models: dict[str, list[str]] = {}
    for slot, value in slots.items():
        model = value.get("id") if isinstance(value, dict) else None
        if isinstance(model, str) and model:
            models.setdefault(model, []).append(slot)
    router = {"name": "router", "kind": "model",
              "about": "Kosika's model router: free cloud models first, then local ones, and the next when one fails",
              "uses": sorted(models)}
    return [router] + [{"name": model, "kind": "model", "about": "a model the router picks for: " + ", ".join(found), "uses": []}
                       for model, found in sorted(models.items())]


def swarm_part(work: Path) -> dict[str, Any] | None:
    """The coder swarm, if it has a work folder here."""

    from . import coders

    if not work.is_dir():
        return None
    try:
        runs = coders._runs(work)
    except OSError:
        runs = []
    names = sorted({coders._name(coders._agent_of(run)) for run in runs[-60:]} - {""})
    return {"name": "Coder swarm", "kind": "swarm",
            "about": f"{len(runs)} runs writing Kosika's standard library" + (f" ({', '.join(names)})" if names else ""),
            "uses": ["router", "coder", "std/stats", "std/text"]}


def census(agents_folder: Path | None = None, router_json: Path | None = None,
           work: Path | None = None) -> list[dict[str, Any]]:
    """Every part of the platform found on this computer, as PlatformPart records."""

    from .agents_home import agents_folder as default_agents
    from .coders import work_folder
    from .models_page import router_file

    router_json = router_json or router_file()
    models = model_parts(router_json)
    agents, named = agent_parts(agents_folder or default_agents(), bool(models))
    parts: dict[str, dict[str, Any]] = {}
    for item in models + agents:
        parts.setdefault(item["name"], item)
    for name, item in named.items():
        parts.setdefault(name, item)
    swarm = swarm_part(work or work_folder())
    if swarm is not None:
        for wanted, kind in (("std/stats", "design"), ("std/text", "skill")):
            parts.setdefault(wanted, {"name": wanted, "kind": kind, "about": "a std module the swarm writes in Kosika", "uses": []})
        parts[swarm["name"]] = swarm
    # The Brain is always there: Studio has its page, and its timeline is
    # where every part that records an episode keeps it.
    parts.setdefault("Brain", {"name": "Brain", "kind": "brain",
                               "about": "the one intelligence layer: senses, recalls, thinks, checks, records", "uses": []})
    if "Brain" in parts:
        parts["Brain"]["uses"] = sorted(set(parts["Brain"]["uses"]) | ({"router"} & parts.keys()) | {"Brain timeline"})
        parts["Brain timeline"] = {"name": "Brain timeline", "kind": "memory",
                                   "about": "every episode a Kosika part kept, and how it went", "uses": []}
    studio_opens = [name for name in ("Kosi", "Kosika AI", "study", "Coder swarm", "Brain", "router") if name in parts]
    parts["Studio"] = {"name": "Studio", "kind": "studio",
                       "about": "Kosika Studio: Home, Kosi, Coders, Kosika AI, Study, Designs and the Brain",
                       "uses": studio_opens}
    if "lab" in parts and "Coder swarm" in parts:
        parts["lab"]["uses"] = sorted(set(parts["lab"]["uses"]) | {"Coder swarm"})
    return list(parts.values())
