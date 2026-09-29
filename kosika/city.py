"""A city of the agents declared across many Kosika programs.

``kosika design`` renders one program at a time. This module is the
multi-program view: it reads every ``.kos`` file under a root, folds agents
that appear in more than one program into a single resident, groups
residents into the swarms that declare them, and emits one design manifest
for the whole city.

Rendering is deliberately *not* reimplemented here. The manifest goes
through :mod:`kosika.design`, so the city is drawn by the same renderer, the
same themes, and the same views as every other design, and agent
prohibitions appear as walls because the design renderer draws them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ast import AgentCall, AgentDecl, Program, SwarmDecl, WorkflowDecl
from .design import DesignError, render_design_svg, validate_design
from .parser import parse as parse_program

CITY_SCHEMA = "kosika.agent-city/v1"

KOS_SUFFIX = ".kos"
MAX_CITY_PROGRAMS = 500
MAX_CITY_SOURCE_BYTES = 1_000_000

PLAZA = "Public Plaza"

NODE_WIDTH = 160
NODE_HEIGHT = 64
COLUMN_PITCH = 200
ROW_PITCH = 118
COLUMNS = 4
DISTRICT_HEADER = 40
CANVAS_MARGIN = 90
MIN_CANVAS_WIDTH = 1200
MIN_CANVAS_HEIGHT = 800

# Metropolitan layout. Districts sit in a grid of blocks; inside a block the
# agents line an avenue in the order their workflow runs them, and the tools
# they are allowed to use sit below as infrastructure.
DISTRICTS_PER_ROW = 2
AVENUE_TITLE_BAND = 132
INFRASTRUCTURE_BAND = 104
DISTRICT_GAP_X = 120
DISTRICT_GAP_Y = 150
CITY_MOTORWAY = 64

# The city has a name of its own, separate from the project directory it was
# built from: every render of the agent metropolis is called Maya unless a
# caller overrides it.
CITY_NAME = "Maya"


class CityError(ValueError):
    """Raised when an agent city cannot be built from the given programs."""


@dataclass(frozen=True)
class Resident:
    """One agent, folded across every program that declares it."""

    name: str
    district: str
    can: tuple[str, ...]
    cannot: tuple[str, ...]
    model: str | None
    max_steps: int
    programs: tuple[str, ...]


@dataclass(frozen=True)
class District:
    """A swarm, or the plaza for agents that belong to no swarm."""

    name: str
    kind: str
    workflow: str | None
    max_rounds: int
    approval_required: bool
    members: tuple[str, ...]
    pipeline: tuple[str, ...] = ()


def _pipeline_of(
    workflow: WorkflowDecl | None, residents: dict[str, Resident]
) -> tuple[str, ...]:
    """The order a district's agents actually run in.

    A swarm names its members but not their sequence; the sequence lives in
    the workflow the swarm points at, one step per agent call. Reading it
    from the AST is what makes the city map's roads true rather than drawn.
    """
    if workflow is None:
        return ()
    ordered: list[str] = []
    for step in workflow.steps:
        call = step.call
        if not isinstance(call, AgentCall):
            continue
        if call.agent_name in residents and call.agent_name not in ordered:
            ordered.append(call.agent_name)
    # Members the workflow never calls still belong, appended in name order.
    return tuple(ordered)


@dataclass(frozen=True)
class City:
    """A whole city, derived from every parsed program."""

    project: str
    residents: tuple[Resident, ...]
    districts: tuple[District, ...]
    tools: tuple[str, ...]
    models: tuple[str, ...]
    records: tuple[str, ...]
    programs: tuple[str, ...]
    name: str = CITY_NAME

    def resident(self, name: str) -> Resident | None:
        """Look up one resident by agent name."""
        for person in self.residents:
            if person.name == name:
                return person
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CITY_SCHEMA,
            "project": self.project,
            "name": self.name,
            "residents": [
                {
                    "name": person.name,
                    "district": person.district,
                    "can": list(person.can),
                    "cannot": list(person.cannot),
                    "model": person.model,
                    "max_steps": person.max_steps,
                    "programs": list(person.programs),
                }
                for person in self.residents
            ],
            "districts": [
                {
                    "name": district.name,
                    "kind": district.kind,
                    "workflow": district.workflow,
                    "max_rounds": district.max_rounds,
                    "approval_required": district.approval_required,
                    "members": list(district.members),
                }
                for district in self.districts
            ],
            "tools": list(self.tools),
            "models": list(self.models),
            "records": list(self.records),
            "programs": list(self.programs),
        }

    def to_text(self) -> str:
        lines = [f"{self.name}, an agent city for {self.project}", ""]
        for district in self.districts:
            mark = " (approval required)" if district.approval_required else ""
            rounds = f", {district.max_rounds} round(s)" if district.max_rounds else ""
            lines.append(f"  {district.name} [{district.kind}]{mark}{rounds}")
            for name in district.members:
                person = self.resident(name)
                if person is None:
                    continue
                tools = ", ".join(person.can) if person.can else "no tools"
                walls = ", ".join(person.cannot) if person.cannot else "no walls"
                model = f" on {person.model}" if person.model else ""
                lines.append(f"    {person.name}{model}: can {tools} / cannot {walls}")
            lines.append("")
        lines.append(
            f"  {len(self.residents)} agent(s), {len(self.districts)} district(s), "
            f"{len(self.tools)} tool(s), {len(self.models)} model(s)"
        )
        return "\n".join(lines) + "\n"

    def to_manifest(self) -> dict[str, Any]:
        """Build one design manifest for the whole city.

        Districts become ``swarm`` nodes, residents become ``agent`` nodes
        carrying their prohibitions, and the tools an agent may use become
        ``tool`` nodes below the avenue. Consecutive agents in a district's
        workflow are joined by ``calls`` edges, so the map's roads are the
        order the workflow actually runs them in rather than a drawing.
        """
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        blocks: list[tuple[District, list[Resident]]] = []

        for district in self.districts:
            members = [
                person
                for name in district.members
                for person in self.residents
                if person.name == name
            ]
            if not members:
                continue
            order = {name: index for index, name in enumerate(district.pipeline)}
            members.sort(
                key=lambda person: (order.get(person.name, len(order)), person.name)
            )
            blocks.append((district, members))

        if not blocks:
            raise CityError("No agents to draw; the city is empty")

        widest = max(len(members) for _, members in blocks)
        avenue_width = widest * COLUMN_PITCH
        block_width = avenue_width + 80
        origin_x = CANVAS_MARGIN + CITY_MOTORWAY
        origin_y = CANVAS_MARGIN

        for block_index, (district, members) in enumerate(blocks):
            column = block_index % DISTRICTS_PER_ROW
            row = block_index // DISTRICTS_PER_ROW
            base_x = origin_x + column * (block_width + DISTRICT_GAP_X)
            base_y = origin_y + row * (
                AVENUE_TITLE_BAND + NODE_HEIGHT + INFRASTRUCTURE_BAND + DISTRICT_GAP_Y
            )
            swarm_id = f"swarm.{_slug(district.name)}"

            nodes.append(
                {
                    "id": swarm_id,
                    "label": district.name,
                    "kind": "swarm",
                    "position": {"x": float(base_x), "y": float(base_y)},
                    "size": {"width": float(NODE_WIDTH), "height": float(NODE_HEIGHT)},
                    "max_rounds": district.max_rounds,
                    "approval_required": district.approval_required,
                    "workflow": district.workflow,
                }
            )
            if district.approval_required:
                approval_id = f"approval.{_slug(district.name)}"
                nodes.append(
                    {
                        "id": approval_id,
                        "label": f"Approval: {district.name}",
                        "kind": "approval",
                        "position": {
                            "x": float(base_x + COLUMN_PITCH),
                            "y": float(base_y),
                        },
                        "size": {
                            "width": float(NODE_WIDTH),
                            "height": float(NODE_HEIGHT),
                        },
                    }
                )
                edges.append(
                    {
                        "id": f"{swarm_id}.requires.{approval_id}",
                        "source": swarm_id,
                        "target": approval_id,
                        "kind": "requires_approval",
                    }
                )

            avenue_y = base_y + AVENUE_TITLE_BAND
            previous_id: str | None = None
            for slot, person in enumerate(members):
                node_id = f"agent.{_slug(person.name)}"
                nodes.append(
                    {
                        "id": node_id,
                        "label": person.name,
                        "kind": "agent",
                        "position": {
                            "x": float(base_x + slot * COLUMN_PITCH),
                            "y": float(avenue_y),
                        },
                        "size": {
                            "width": float(NODE_WIDTH),
                            "height": float(NODE_HEIGHT),
                        },
                        "can": list(person.can),
                        "cannot": list(person.cannot),
                        "max_steps": person.max_steps,
                    }
                )
                edges.append(
                    {
                        "id": f"{swarm_id}.contains.{node_id}",
                        "source": swarm_id,
                        "target": node_id,
                        "kind": "contains",
                    }
                )
                # The road: the workflow runs these agents in this order.
                if previous_id is not None:
                    edges.append(
                        {
                            "id": f"{node_id}.after.{previous_id}",
                            "source": previous_id,
                            "target": node_id,
                            "kind": "calls",
                        }
                    )
                previous_id = node_id

            # Infrastructure: the tools this district's agents may use.
            tools_used: list[str] = []
            for person in members:
                for tool in person.can:
                    if tool in self.tools and tool not in tools_used:
                        tools_used.append(tool)
            for slot, tool in enumerate(sorted(tools_used)[:COLUMNS]):
                tool_id = f"tool.{_slug(tool)}"
                nodes.append(
                    {
                        "id": tool_id,
                        "label": tool,
                        "kind": "tool",
                        "position": {
                            "x": float(base_x + slot * COLUMN_PITCH),
                            "y": float(avenue_y + NODE_HEIGHT + INFRASTRUCTURE_BAND),
                        },
                        "size": {
                            "width": float(NODE_WIDTH),
                            "height": float(NODE_HEIGHT // 2),
                        },
                    }
                )
                for person in members:
                    if tool in person.can:
                        edges.append(
                            {
                                "id": f"agent.{_slug(person.name)}.uses.{tool_id}",
                                "source": f"agent.{_slug(person.name)}",
                                "target": tool_id,
                                "kind": "uses",
                            }
                        )

        rows = -(-len(blocks) // DISTRICTS_PER_ROW)
        width = max(
            CANVAS_MARGIN * 2
            + CITY_MOTORWAY * 2
            + DISTRICTS_PER_ROW * block_width
            + (DISTRICTS_PER_ROW - 1) * DISTRICT_GAP_X,
            MIN_CANVAS_WIDTH,
        )
        height = max(
            origin_y
            + rows
            * (AVENUE_TITLE_BAND + NODE_HEIGHT + INFRASTRUCTURE_BAND + DISTRICT_GAP_Y),
            MIN_CANVAS_HEIGHT,
        )
        return {
            "design": {
                "id": f"city.{self.project}",
                "name": f"{self.project} agent city",
                "canvas": {"width": float(width), "height": float(height)},
            },
            "nodes": nodes,
            "edges": edges,
            "views": [],
        }


def _stable_digest(text: str) -> str:
    """A stable identifier; ``hash`` is salted per process and unusable."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _slug(text: str) -> str:
    """Turn a label into a design identifier.

    Design identifiers reject spaces and punctuation. Agent names are
    already Kosika identifiers, but the plaza and any future district label
    are human text, so every derived identifier is slugged.
    """
    cleaned = "".join(
        character if character.isalnum() or character == "_" else "_"
        for character in text
    ).strip("_")
    if not cleaned:
        # Nothing survived slugging, so fall back to content-addressed text.
        return f"n{_stable_digest(text)}"
    return cleaned


def _collect_programs(root: str | Path) -> list[Path]:
    base = Path(root)
    if base.is_file():
        if base.suffix != KOS_SUFFIX:
            raise CityError(f"Expected a {KOS_SUFFIX} program, got {base.name}")
        return [base]
    if not base.is_dir():
        raise CityError(f"City root does not exist: {root}")
    found = sorted(
        path
        for path in base.rglob(f"*{KOS_SUFFIX}")
        if path.is_file() and ".venv" not in path.parts
    )
    if not found:
        raise CityError(f"No {KOS_SUFFIX} programs found under {root}")
    return found[:MAX_CITY_PROGRAMS]


def _merge_resident(
    seen: dict[str, Resident], decl: AgentDecl, program_name: str
) -> None:
    """Fold one declaration into the city, unioning repeated agents.

    ``monitor`` is declared in four example programs and ``researcher`` in
    two. They are the same resident seen through different programs, so the
    city keeps one entry and records every program that declares it.

    ``max_steps`` takes the maximum rather than the last value. A program
    that stays silent gets the language default, so unioning could raise an
    explicit ``max_steps: 2`` to that default; for a step ceiling the
    conservative direction is the safe one.
    """
    existing = seen.get(decl.name)
    if existing is None:
        seen[decl.name] = Resident(
            name=decl.name,
            district=PLAZA,
            can=tuple(sorted(set(decl.can))),
            cannot=tuple(sorted(set(decl.cannot))),
            model=decl.model,
            max_steps=decl.max_steps,
            programs=(program_name,),
        )
        return
    seen[decl.name] = Resident(
        name=existing.name,
        district=existing.district,
        can=tuple(sorted(set(existing.can) | set(decl.can))),
        cannot=tuple(sorted(set(existing.cannot) | set(decl.cannot))),
        model=existing.model or decl.model,
        max_steps=max(existing.max_steps, decl.max_steps),
        programs=tuple(sorted(set(existing.programs) | {program_name})),
    )


def _place_in_districts(
    residents: dict[str, Resident], swarms: dict[str, SwarmDecl]
) -> None:
    """Move every named swarm member into its district; leave the rest public."""
    for swarm_name in sorted(swarms):
        swarm = swarms[swarm_name]
        for member in sorted(swarm.agents):
            person = residents.get(member)
            if person is None:
                continue
            if person.district == PLAZA:
                residents[member] = Resident(
                    name=person.name,
                    district=swarm_name,
                    can=person.can,
                    cannot=person.cannot,
                    model=person.model,
                    max_steps=person.max_steps,
                    programs=person.programs,
                )


def build_city(root: str | Path, *, name: str | None = None) -> City:
    """Parse every Kosika program under ``root`` into a city."""
    # Bound before the parse loop, which reuses ``name`` for program names.
    city_name = CITY_NAME if name is None else name
    paths = _collect_programs(root)
    residents: dict[str, Resident] = {}
    swarms: dict[str, SwarmDecl] = {}
    # Workflow names are only unique inside one program. Two example programs
    # both define ``review``, so a swarm's workflow has to be resolved in the
    # program that declared the swarm, or a district inherits another
    # department's pipeline.
    swarm_program: dict[str, str] = {}
    program_workflows: dict[str, dict[str, WorkflowDecl]] = {}
    tools: set[str] = set()
    models: set[str] = set()
    records: set[str] = set()
    program_names: list[str] = []
    budget = MAX_CITY_SOURCE_BYTES

    for path in paths:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CityError(f"Cannot read program {path}: {exc}") from exc
        if len(raw) > budget:
            continue
        budget -= len(raw)
        try:
            program: Program = parse_program(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise CityError(f"Program {path} is not valid UTF-8: {exc}") from exc
        except Exception as exc:  # parser errors vary by construct
            raise CityError(f"Cannot parse program {path}: {exc}") from exc

        name = path.name
        # Keyed by full path, not name: ``examples/city/research.kos`` and
        # ``examples/composed/research.kos`` are different programs that
        # share a basename, and keying by name made one overwrite the other.
        key = str(path)
        program_names.append(name)
        for agent_decl in program.agents:
            _merge_resident(residents, agent_decl, name)
        for swarm_decl in program.swarms:
            if swarm_decl.name not in swarms:
                swarms[swarm_decl.name] = swarm_decl
                swarm_program[swarm_decl.name] = key
        program_workflows[key] = {
            workflow_decl.name: workflow_decl for workflow_decl in program.workflows
        }
        tools.update(tool.name for tool in program.tools)
        models.update(model.name for model in program.models)
        records.update(record.name for record in program.records)

    _place_in_districts(residents, swarms)

    districts: list[District] = []
    for swarm_name in sorted(swarms):
        swarm = swarms[swarm_name]
        districts.append(
            District(
                name=swarm.name,
                kind="swarm",
                workflow=swarm.workflow,
                max_rounds=swarm.max_rounds,
                approval_required=bool(getattr(swarm, "approval_required", True)),
                members=tuple(
                    sorted(member for member in swarm.agents if member in residents)
                ),
                pipeline=_pipeline_of(
                    program_workflows.get(swarm_program.get(swarm.name), {}).get(
                        swarm.workflow
                    ),
                    residents,
                ),
            )
        )

    public = tuple(
        sorted(name for name, person in residents.items() if person.district == PLAZA)
    )
    if public:
        districts.append(
            District(
                name=PLAZA,
                kind="open",
                workflow=None,
                max_rounds=0,
                approval_required=False,
                members=public,
                pipeline=(),
            )
        )

    return City(
        project=Path(root).name,
        residents=tuple(residents[name] for name in sorted(residents)),
        districts=tuple(districts),
        tools=tuple(sorted(tools)),
        models=tuple(sorted(models)),
        records=tuple(sorted(records)),
        programs=tuple(sorted(program_names)),
        name=city_name,
    )


def render_city_svg(city: City, *, view: str = "network", theme: str = "lab") -> str:
    """Draw the city through the design renderer.

    The city owns no drawing code. Building a manifest and handing it to
    :func:`kosika.design.render_design_svg` keeps one renderer, one set of
    themes, and one implementation of how an agent's walls are drawn.
    """
    try:
        manifest = validate_design(city.to_manifest())
    except DesignError as exc:
        raise CityError(f"City design manifest is invalid: {exc}") from exc
    return render_design_svg(manifest, view=view, theme=theme)
