"""Which agent should get a request.

Studio can be asked something without the person choosing an agent first.
The agents' own descriptions are shown to a model, which names one. It can
name only an agent that exists. If no model can be asked, or its answer is
not one of them, the agent whose description shares the most words with the
request is used, and failing that the first.

This only chooses who is asked. The chosen agent then runs exactly as if the
person had opened it: the same grants, the same plan to approve, the same
session.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"[a-z0-9]+")
#: Words too common to tell one agent from another.
_COMMON = frozenset(
    "a an and are as at be but by can do does for from give has have how i if in "
    "is it its me my of on one or say so that the then this to up use what when "
    "which who with you your".split()
)
#: How much of an agent's description the model is shown.
MAX_ABOUT = 320

CHOOSE = (
    "You pass a person's request to the one helper that fits it best. You are "
    "given the helpers, each with a name and what it does. Answer with one "
    'JSON object and nothing else: {"agent": "NAME"}, using a name from the '
    "list exactly as written."
)


@dataclass(frozen=True)
class Candidate:
    """An agent a request could go to: its id, its title, and what it says
    about itself."""

    agent_id: str
    title: str
    about: str


@dataclass(frozen=True)
class Route:
    agent_id: str
    why: str


def _words(text: str) -> set[str]:
    return {word for word in _WORD_RE.findall(text.lower()) if word not in _COMMON}


def route_question(candidates: list[Candidate], request: str) -> tuple[str, str]:
    """What to ask a model so that it chooses: system words, then the ask."""

    menu = "\n".join(
        f"- {candidate.title}: {candidate.about[:MAX_ABOUT]}"
        for candidate in candidates
    )
    return CHOOSE + "\n\nThe helpers:\n" + menu, "Request: " + request


def read_route(candidates: list[Candidate], answer: str | None) -> Candidate | None:
    """The agent a model named, if it is one of the candidates."""

    try:
        named = json.loads(answer or "").get("agent")
    except (ValueError, AttributeError):
        return None
    if not isinstance(named, str):
        return None
    wanted = named.strip().lower()
    for candidate in candidates:
        if wanted in (candidate.title.lower(), candidate.agent_id.lower()):
            return candidate
    return None


def by_words(candidates: list[Candidate], request: str) -> Route | None:
    """The agent whose name and description share the most words with the
    request, with those words, or None when none shares any."""

    asked = _words(request)
    best: tuple[int, str, Candidate, list[str]] | None = None
    for candidate in candidates:
        shared = sorted(asked & _words(candidate.title + " " + candidate.about))
        if shared and (best is None or (-len(shared), candidate.agent_id) < best[:2]):
            best = (-len(shared), candidate.agent_id, candidate, shared)
    if best is None:
        return None
    return Route(
        best[2].agent_id,
        "its description shares these words with your request: "
        + ", ".join(best[3][:6]),
    )
