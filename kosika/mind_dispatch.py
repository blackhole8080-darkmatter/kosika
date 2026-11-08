# kosika/mind_dispatch.py
"""Model-free perceive dispatcher (Mind layer, Slice B).

The brain decides which maths capability a request maps to, then executes it
on the skill bus and returns the real result. Reuses routing.by_words; never
asks a model. This is the Mind spec's perceive stage applied to the maths
component — the first component of the eventual registry, not the registry
itself.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from .brain_skills import MATHS, MEMORY
from .routing import Candidate, by_words
from .skills_framework import SkillMessage, SkillRegistry

_CANDIDATES = [
    Candidate(
        "visualize", "Draw the knowledge graph",
        "draw visualize visualise picture diagram graph show render taxonomy "
        "hierarchy picture of what you know",
    ),
    Candidate(
        "query", "Query the knowledge graph",
        "query list catalogue facts links relations is-a parent grandparent "
        "know knowledge everything what are which",
    ),
    Candidate(
        "infer", "Derive new facts",
        "infer derive transitive subsumption prove hierarchy rules entail",
    ),
    Candidate(
        "recall", "Recall remembered facts",
        "recall remember memory forget facts you know remembered",
    ),
]


SUBSUMPTION = {
    "id": "subsumption",
    "name": "is-a transitivity",
    "pattern": "(and (match ?x is-a ?y) (match ?y is-a ?z))",
    "action": {"head": "?x", "relation": "is-a", "tail": "?z"},
}
_RULE_TRIGGERS = ("transitive", "is-a", "subsumption", "hierarchy")


_REL_RE = re.compile(r"\b([a-z]+(?:-[a-z]+)+)\b")


def _query_payload(request: str) -> Dict[str, Any]:
    match = _REL_RE.search(request.lower())
    if match:
        relation = match.group(1)
        return {"query": f"(query (where (match ?x {relation} ?y)))"}
    return {"query": "(query (where (match ?x ?r ?y)))"}


def _message(
    capability: str, request: str
) -> Tuple[Optional[str], Optional[str], Any]:
    if capability == "visualize":
        return MATHS, "visualize", {"title": request}
    if capability == "query":
        return MATHS, "query", _query_payload(request)
    if capability == "infer":
        if any(word in request.lower() for word in _RULE_TRIGGERS):
            return MATHS, "infer", {"rules": [SUBSUMPTION]}
        return None, None, "infer needs a named rule (try 'transitive' or 'is-a')"
    if capability == "recall":
        return MEMORY, "recall", {}
    raise ValueError(f"no message mapping for capability {capability!r}")


def dispatch(registry: SkillRegistry, request: str) -> Dict[str, Any]:
    route = by_words(_CANDIDATES, request)
    if route is None:
        return {
            "capability": None, "why": "", "ok": False, "value": None,
            "problem": f"no capability matched request {request!r}",
        }
    recipient, type_, payload = _message(route.agent_id, request)
    if recipient is None:
        return {
            "capability": route.agent_id, "why": route.why, "ok": False,
            "value": None, "problem": payload,
        }
    reply = registry.route(SkillMessage("mind", recipient, type_, payload))
    return {
        "capability": route.agent_id,
        "why": route.why,
        "ok": reply.type == "result",
        "value": reply.payload.get("value"),
        "problem": reply.payload.get("problem", ""),
    }