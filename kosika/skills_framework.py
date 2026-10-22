"""Skill Development Framework for Kosika Agents.

This framework makes it easy to create, package, and use skills for AI agents.
Skills are reusable capabilities that agents can learn and apply.

Key features:
- Declarative skill definition
- Automatic parameter validation
- Skill composition and chaining
- Skill learning from examples
- Skill versioning and compatibility
- Built-in skill templates
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


class SkillKind(Enum):
    """Types of skills."""
    TOOL = "tool"  # External tool or capability
    ALGORITHM = "algorithm"  # Algorithmic procedure
    KNOWLEDGE = "knowledge"  # Knowledge retrieval
    REASONING = "reasoning"  # Reasoning pattern
    PLANNING = "planning"  # Planning strategy
    LEARNING = "learning"  # Learning mechanism
    COMMUNICATION = "communication"  # Communication pattern


class SkillStatus(Enum):
    """Skill lifecycle status."""
    DRAFT = "draft"
    TESTING = "testing"
    VALIDATED = "validated"
    DEPRECATED = "deprecated"


@dataclass
class SkillParameter:
    """A parameter for a skill."""
    name: str
    type: str  # Text, Number, Bool, record type, etc.
    required: bool = True
    default: Any = None
    description: str = ""
    constraints: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillOutput:
    """The output of a skill."""
    type: str
    description: str = ""
    examples: List[Any] = field(default_factory=list)


@dataclass
class SkillExample:
    """An example of skill usage."""
    input: Dict[str, Any]
    output: Any
    description: str = ""


@dataclass
class Skill:
    """A reusable agent skill."""
    id: str
    name: str
    kind: SkillKind
    description: str
    parameters: List[SkillParameter]
    output: SkillOutput
    implementation: Optional[Callable] = None
    examples: List[SkillExample] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    version: str = "1.0.0"
    status: SkillStatus = SkillStatus.DRAFT
    author: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def validate_input(self, input_data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate input parameters."""
        errors = []

        for param in self.parameters:
            if param.required and param.name not in input_data:
                errors.append(f"Missing required parameter: {param.name}")
                continue

            if param.name in input_data:
                value = input_data[param.name]
                # Type check
                if not self._check_type(value, param.type):
                    errors.append(f"Parameter {param.name} has wrong type: expected {param.type}")

                # Constraint checks
                for constraint_name, constraint_value in param.constraints.items():
                    if not self._check_constraint(value, constraint_name, constraint_value):
                        errors.append(
                            f"Parameter {param.name} violates constraint {constraint_name}"
                        )

        return len(errors) == 0, errors

    def _check_type(self, value: Any, expected_type: str) -> bool:
        """Check if value matches expected type."""
        if expected_type == "Number":
            return isinstance(value, (int, float))
        type_map = {
            "Text": str,
            "Bool": bool,
            "Text[]": list,
            "Number[]": list,
        }

        expected = type_map.get(expected_type)
        return True if expected is None else isinstance(value, expected)

    def _check_constraint(self, value: Any, constraint: str, constraint_value: Any) -> bool:
        """Check if value satisfies a constraint."""
        if constraint == "min" and isinstance(value, (int, float)):
            return value >= constraint_value
        if constraint == "max" and isinstance(value, (int, float)):
            return value <= constraint_value
        if constraint == "min_length" and isinstance(value, (str, list)):
            return len(value) >= constraint_value
        if constraint == "max_length" and isinstance(value, (str, list)):
            return len(value) <= constraint_value
        if constraint == "pattern" and isinstance(value, str):
            import re
            return bool(re.match(constraint_value, value))
        if constraint == "enum":
            return value in constraint_value
        return True

    def execute(self, input_data: Dict[str, Any]) -> Any:
        """Execute the skill with given input."""
        is_valid, errors = self.validate_input(input_data)
        if not is_valid:
            raise ValueError(f"Invalid input: {', '.join(errors)}")

        if self.implementation is None:
            raise NotImplementedError(f"Skill {self.id} has no implementation")

        return self.implementation(**input_data)

    def to_dict(self) -> Dict[str, Any]:
        """Convert skill to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind.value,
            "description": self.description,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "required": p.required,
                    "default": p.default,
                    "description": p.description,
                    "constraints": p.constraints,
                }
                for p in self.parameters
            ],
            "output": {
                "type": self.output.type,
                "description": self.output.description,
                "examples": self.output.examples,
            },
            "examples": [
                {"input": e.input, "output": e.output, "description": e.description}
                for e in self.examples
            ],
            "dependencies": self.dependencies,
            "version": self.version,
            "status": self.status.value,
            "author": self.author,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Skill":
        """Create skill from dictionary."""
        parameters = [
            SkillParameter(
                name=p["name"],
                type=p["type"],
                required=p.get("required", True),
                default=p.get("default"),
                description=p.get("description", ""),
                constraints=p.get("constraints", {}),
            )
            for p in data.get("parameters", [])
        ]

        output = SkillOutput(
            type=data["output"]["type"],
            description=data["output"].get("description", ""),
            examples=data["output"].get("examples", []),
        )

        examples = [
            SkillExample(
                input=e["input"],
                output=e["output"],
                description=e.get("description", ""),
            )
            for e in data.get("examples", [])
        ]

        return cls(
            id=data["id"],
            name=data["name"],
            kind=SkillKind(data["kind"]),
            description=data["description"],
            parameters=parameters,
            output=output,
            examples=examples,
            dependencies=data.get("dependencies", []),
            version=data.get("version", "1.0.0"),
            status=SkillStatus(data.get("status", "draft")),
            author=data.get("author", ""),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )


class SkillRegistry:
    """Registry for managing skills."""

    def __init__(self):
        self.skills: Dict[str, Skill] = {}
        self.skill_templates: Dict[str, Skill] = {}

    def register(self, skill: Skill) -> str:
        """Register a skill."""
        self.skills[skill.id] = skill
        return skill.id

    def get(self, skill_id: str) -> Optional[Skill]:
        """Get a skill by ID."""
        return self.skills.get(skill_id)

    def query(
        self,
        kind: Optional[SkillKind] = None,
        tags: Optional[List[str]] = None,
        status: Optional[SkillStatus] = None,
    ) -> List[Skill]:
        """Query skills with filters."""
        results = []
        for skill in self.skills.values():
            if kind and skill.kind != kind:
                continue
            if tags and not any(tag in skill.tags for tag in tags):
                continue
            if status and skill.status != status:
                continue
            results.append(skill)
        return results

    def add_template(self, skill: Skill):
        """Add a skill template."""
        self.skill_templates[skill.id] = skill

    def create_from_template(self, template_id: str, **overrides) -> Skill:
        """Create a new skill from a template."""
        template = self.skill_templates.get(template_id)
        if not template:
            raise ValueError(f"Template not found: {template_id}")

        # Create a copy with overrides
        skill_data = template.to_dict()
        skill_data.update(overrides)
        skill_data["id"] = overrides.get("id", f"{template_id}_{datetime.now().timestamp()}")

        return Skill.from_dict(skill_data)

    def export_skill(self, skill_id: str) -> str:
        """Export skill as JSON."""
        skill = self.get(skill_id)
        if not skill:
            raise ValueError(f"Skill not found: {skill_id}")
        return json.dumps(skill.to_dict(), indent=2)

    def import_skill(self, skill_json: str) -> str:
        """Import skill from JSON."""
        skill = Skill.from_dict(json.loads(skill_json))
        return self.register(skill)


# Global skill registry
_global_registry: Optional[SkillRegistry] = None


def get_skill_registry() -> SkillRegistry:
    """Get the global skill registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = SkillRegistry()
        _initialize_builtin_templates()
    return _global_registry


def _initialize_builtin_templates():
    """Initialize built-in skill templates."""
    registry = get_skill_registry()

    # Template: Tool skill
    tool_template = Skill(
        id="template_tool",
        name="Tool Template",
        kind=SkillKind.TOOL,
        description="Template for creating tool skills",
        parameters=[
            SkillParameter(name="input", type="Text", required=True, description="Input to the tool"),
        ],
        output=SkillOutput(type="Text", description="Tool output"),
    )
    registry.add_template(tool_template)

    # Template: Algorithm skill
    algorithm_template = Skill(
        id="template_algorithm",
        name="Algorithm Template",
        kind=SkillKind.ALGORITHM,
        description="Template for creating algorithmic skills",
        parameters=[
            SkillParameter(name="data", type="Text[]", required=True, description="Input data"),
            SkillParameter(name="config", type="Text{}", required=False, description="Configuration"),
        ],
        output=SkillOutput(type="Text", description="Algorithm result"),
    )
    registry.add_template(algorithm_template)

    # Template: Knowledge skill
    knowledge_template = Skill(
        id="template_knowledge",
        name="Knowledge Template",
        kind=SkillKind.KNOWLEDGE,
        description="Template for creating knowledge retrieval skills",
        parameters=[
            SkillParameter(name="query", type="Text", required=True, description="Knowledge query"),
            SkillParameter(name="context", type="Text", required=False, description="Additional context"),
        ],
        output=SkillOutput(type="Text[]", description="Retrieved knowledge items"),
    )
    registry.add_template(knowledge_template)


def create_skill(
    id: str,
    name: str,
    kind: SkillKind,
    description: str,
    parameters: List[Dict[str, Any]],
    output_type: str,
    implementation: Optional[Callable] = None,
) -> Skill:
    """Helper function to create a skill."""
    skill_params = [
        SkillParameter(
            name=p["name"],
            type=p["type"],
            required=p.get("required", True),
            default=p.get("default"),
            description=p.get("description", ""),
            constraints=p.get("constraints", {}),
        )
        for p in parameters
    ]

    skill = Skill(
        id=id,
        name=name,
        kind=kind,
        description=description,
        parameters=skill_params,
        output=SkillOutput(type=output_type),
        implementation=implementation,
    )

    return skill
