"""
Kosika Meta-Agent System

This module provides intelligent agent, tool, and model selection
for optimizing AI system execution.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskType(Enum):
    """Types of tasks that can be handled."""

    DOCUMENT_ANALYSIS = "document_analysis"
    CODE_GENERATION = "code_generation"
    DATA_PROCESSING = "data_processing"
    WEB_SCRAPING = "web_scraping"
    IMAGE_ANALYSIS = "image_analysis"
    TEXT_GENERATION = "text_generation"
    QUESTION_ANSWERING = "question_answering"
    REASONING = "reasoning"
    MULTIMODAL = "multimodal"


@dataclass
class AgentCapability:
    """Represents an agent's capabilities."""

    name: str
    task_types: list[TaskType]
    required_tools: list[str]
    preferred_models: list[str]
    cost_per_execution: float
    avg_latency: float
    quality_score: float  # 0.0 to 1.0
    max_input_size: int
    reliability: float  # 0.0 to 1.0


@dataclass
class ModelCapability:
    """Represents a model's capabilities."""

    name: str
    provider: str
    task_types: list[TaskType]
    cost_per_1k_tokens: float
    max_tokens: int
    avg_latency: float
    quality_score: float
    supports_vision: bool
    supports_function_calling: bool


@dataclass
class ToolCapability:
    """Represents a tool's capabilities."""

    name: str
    operation: str
    required_inputs: list[str]
    output_types: list[str]
    cost_per_call: float
    avg_latency: float
    reliability: float


@dataclass
class TaskContext:
    """Context for a task to be executed."""

    task_description: str
    task_type: TaskType
    input_size: int
    urgency: str  # "low", "medium", "high"
    quality_requirement: str  # "low", "medium", "high"
    privacy_level: str  # "public", "private", "confidential"
    budget_limit: Optional[float] = None
    user_preferences: dict[str, Any] = field(default_factory=dict)


@dataclass
class SelectionResult:
    """Result of agent/model/tool selection."""

    selected_agent: str
    selected_model: str
    selected_tools: list[str]
    configuration: dict[str, Any]
    estimated_cost: float
    estimated_latency: float
    confidence_score: float
    alternatives: list[dict[str, Any]]
    reasoning: str


class MetaAgent:
    """Intelligent agent, model, and tool selection system."""

    def __init__(self):
        self.available_agents: dict[str, AgentCapability] = {}
        self.available_models: dict[str, ModelCapability] = {}
        self.available_tools: dict[str, ToolCapability] = {}
        self._initialize_default_capabilities()

    def _initialize_default_capabilities(self):
        """Initialize with some default capabilities."""
        # Default models
        self.available_models["gpt-4"] = ModelCapability(
            name="gpt-4",
            provider="openai",
            task_types=[
                TaskType.TEXT_GENERATION,
                TaskType.QUESTION_ANSWERING,
                TaskType.REASONING,
                TaskType.CODE_GENERATION,
                TaskType.DOCUMENT_ANALYSIS,
                TaskType.WEB_SCRAPING,
            ],
            cost_per_1k_tokens=0.03,
            max_tokens=8192,
            avg_latency=2.0,
            quality_score=0.95,
            supports_vision=False,
            supports_function_calling=True,
        )

        self.available_models["gpt-3.5-turbo"] = ModelCapability(
            name="gpt-3.5-turbo",
            provider="openai",
            task_types=[TaskType.TEXT_GENERATION, TaskType.QUESTION_ANSWERING],
            cost_per_1k_tokens=0.002,
            max_tokens=4096,
            avg_latency=0.5,
            quality_score=0.85,
            supports_vision=False,
            supports_function_calling=True,
        )

        self.available_models["claude-3-opus"] = ModelCapability(
            name="claude-3-opus",
            provider="anthropic",
            task_types=[
                TaskType.TEXT_GENERATION,
                TaskType.QUESTION_ANSWERING,
                TaskType.REASONING,
                TaskType.CODE_GENERATION,
                TaskType.DOCUMENT_ANALYSIS,
            ],
            cost_per_1k_tokens=0.015,
            max_tokens=200000,
            avg_latency=3.0,
            quality_score=0.92,
            supports_vision=True,
            supports_function_calling=True,
        )

        # Default tools
        self.available_tools["web-search"] = ToolCapability(
            name="web-search",
            operation="search",
            required_inputs=["query"],
            output_types=["search_results"],
            cost_per_call=0.001,
            avg_latency=1.0,
            reliability=0.95,
        )

        self.available_tools["file-reader"] = ToolCapability(
            name="file-reader",
            operation="read",
            required_inputs=["file_path"],
            output_types=["file_content"],
            cost_per_call=0.0,
            avg_latency=0.1,
            reliability=0.99,
        )

        # Default agents
        self.available_agents["document-analyzer"] = AgentCapability(
            name="document-analyzer",
            task_types=[TaskType.DOCUMENT_ANALYSIS],
            required_tools=["file-reader"],
            preferred_models=["gpt-4", "claude-3-opus"],
            cost_per_execution=0.05,
            avg_latency=5.0,
            quality_score=0.90,
            max_input_size=10000000,  # 10MB
            reliability=0.95,
        )

        self.available_agents["code-assistant"] = AgentCapability(
            name="code-assistant",
            task_types=[TaskType.CODE_GENERATION, TaskType.WEB_SCRAPING],
            required_tools=["web-search"],
            preferred_models=["gpt-4", "claude-3-opus"],
            cost_per_execution=0.08,
            avg_latency=8.0,
            quality_score=0.88,
            max_input_size=50000,
            reliability=0.92,
        )

        self.available_agents["general-assistant"] = AgentCapability(
            name="general-assistant",
            task_types=[
                TaskType.TEXT_GENERATION,
                TaskType.QUESTION_ANSWERING,
                TaskType.REASONING,
            ],
            required_tools=[],
            preferred_models=["gpt-3.5-turbo", "gpt-4"],
            cost_per_execution=0.01,
            avg_latency=2.0,
            quality_score=0.85,
            max_input_size=10000,
            reliability=0.90,
        )

    def register_agent(self, agent: AgentCapability):
        """Register a new agent capability."""
        self.available_agents[agent.name] = agent

    def register_model(self, model: ModelCapability):
        """Register a new model capability."""
        self.available_models[model.name] = model

    def register_tool(self, tool: ToolCapability):
        """Register a new tool capability."""
        self.available_tools[tool.name] = tool

    def analyze_task(self, task_description: str) -> TaskType:
        """Analyze a task description to determine its type."""
        task_lower = task_description.lower()

        if any(
            keyword in task_lower for keyword in ["document", "pdf", "analyze text"]
        ):
            return TaskType.DOCUMENT_ANALYSIS
        elif any(
            keyword in task_lower for keyword in ["code", "programming", "function"]
        ):
            return TaskType.CODE_GENERATION
        elif any(keyword in task_lower for keyword in ["web", "scrape", "crawl"]):
            return TaskType.WEB_SCRAPING
        elif any(keyword in task_lower for keyword in ["image", "picture", "visual"]):
            return TaskType.IMAGE_ANALYSIS
        elif any(
            keyword in task_lower for keyword in ["question", "answer", "explain"]
        ):
            return TaskType.QUESTION_ANSWERING
        elif any(keyword in task_lower for keyword in ["write", "generate", "create"]):
            return TaskType.TEXT_GENERATION
        elif any(keyword in task_lower for keyword in ["reason", "think", "analyze"]):
            return TaskType.REASONING
        else:
            return TaskType.TEXT_GENERATION  # Default

    def select_agent(self, context: TaskContext) -> list[str]:
        """Select suitable agents for the task."""
        suitable_agents = []

        for agent_name, agent in self.available_agents.items():
            if context.task_type in agent.task_types:
                if context.input_size <= agent.max_input_size:
                    suitable_agents.append(agent_name)

        return suitable_agents

    def select_model(self, context: TaskContext, preferred_models: list[str]) -> str:
        """Select the best model for the task."""
        candidates = []

        for model_name in preferred_models:
            if model_name in self.available_models:
                model = self.available_models[model_name]
                if context.task_type in model.task_types:
                    candidates.append(model)

        if not candidates:
            # Fallback to any model that supports the task type
            for model in self.available_models.values():
                if context.task_type in model.task_types:
                    candidates.append(model)

        if not candidates:
            raise ValueError(f"No model available for task type: {context.task_type}")

        # Score candidates based on context
        scored_candidates = []
        for model in candidates:
            score = 0.0

            # Quality requirement
            if context.quality_requirement == "high":
                score += model.quality_score * 2.0
            elif context.quality_requirement == "medium":
                score += model.quality_score * 1.0
            else:
                score += model.quality_score * 0.5

            # Urgency
            if context.urgency == "high":
                score += (1.0 / model.avg_latency) * 2.0
            elif context.urgency == "medium":
                score += (1.0 / model.avg_latency) * 1.0

            # Budget
            if context.budget_limit:
                if model.cost_per_1k_tokens * 10 <= context.budget_limit:
                    score += 1.0
                else:
                    score -= 2.0

            # Privacy
            if context.privacy_level == "confidential":
                # Prefer local models (simulated by higher score for certain providers)
                if model.provider in ["local", "self-hosted"]:
                    score += 2.0

            scored_candidates.append((score, model))

        # Select highest-scoring model
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        return scored_candidates[0][1].name

    def select_tools(
        self, context: TaskContext, required_tools: list[str]
    ) -> list[str]:
        """Select required tools for the task."""
        available_tools = []

        for tool_name in required_tools:
            if tool_name in self.available_tools:
                available_tools.append(tool_name)

        return available_tools

    def estimate_execution(
        self, agent: str, model: str, tools: list[str], context: TaskContext
    ) -> tuple[float, float]:
        """Estimate cost and latency for execution."""
        agent_cap = self.available_agents.get(agent)
        model_cap = self.available_models.get(model)

        if not agent_cap or not model_cap:
            raise ValueError(f"Agent or model not found: {agent}, {model}")

        # Base cost from agent
        cost = agent_cap.cost_per_execution

        # Add model cost (estimate based on input size)
        estimated_tokens = min(context.input_size // 4, model_cap.max_tokens)
        model_cost = (estimated_tokens / 1000) * model_cap.cost_per_1k_tokens
        cost += model_cost

        # Add tool costs
        for tool_name in tools:
            if tool_name in self.available_tools:
                cost += self.available_tools[tool_name].cost_per_call

        # Estimate latency
        latency = agent_cap.avg_latency + model_cap.avg_latency
        for tool_name in tools:
            if tool_name in self.available_tools:
                latency += self.available_tools[tool_name].avg_latency

        return cost, latency

    def select(
        self, task_description: str, context: Optional[TaskContext] = None
    ) -> SelectionResult:
        """Main selection method for agent, model, and tools."""
        # Analyze task
        task_type = self.analyze_task(task_description)

        # Create context if not provided
        if context is None:
            context = TaskContext(
                task_description=task_description,
                task_type=task_type,
                input_size=1000,
                urgency="medium",
                quality_requirement="medium",
                privacy_level="public",
                budget_limit=None,
                user_preferences={},
            )
        else:
            context.task_type = task_type

        # Select suitable agents
        suitable_agents = self.select_agent(context)

        if not suitable_agents:
            raise ValueError(f"No suitable agent found for task: {task_description}")

        # Select best agent
        selected_agent = suitable_agents[0]
        agent_cap = self.available_agents[selected_agent]

        # Select model
        selected_model = self.select_model(context, agent_cap.preferred_models)

        # Select tools
        selected_tools = self.select_tools(context, agent_cap.required_tools)

        # Estimate execution
        estimated_cost, estimated_latency = self.estimate_execution(
            selected_agent, selected_model, selected_tools, context
        )

        # Generate alternatives
        alternatives = []
        for agent_name in suitable_agents[1:3]:  # Top 2 alternatives
            agent_alt = self.available_agents[agent_name]
            try:
                model_alt = self.select_model(context, agent_alt.preferred_models)
                cost_alt, latency_alt = self.estimate_execution(
                    agent_name, model_alt, selected_tools, context
                )
                alternatives.append(
                    {
                        "agent": agent_name,
                        "model": model_alt,
                        "tools": selected_tools,
                        "estimated_cost": cost_alt,
                        "estimated_latency": latency_alt,
                        "trade_offs": "Alternative with different cost/latency profile",
                    }
                )
            except ValueError:
                continue

        # Generate configuration
        configuration = {
            "agent": selected_agent,
            "model": selected_model,
            "tools": selected_tools,
            "task_type": task_type.value,
            "privacy_level": context.privacy_level,
            "timeout": estimated_latency * 2,  # Safety margin
        }

        # Generate reasoning
        reasoning = f"Selected {selected_agent} agent with {selected_model} model based on task type '{task_type.value}'. "
        reasoning += f"Quality requirement: {context.quality_requirement}, urgency: {context.urgency}. "
        reasoning += (
            f"Estimated cost: ${estimated_cost:.4f}, latency: {estimated_latency:.1f}s"
        )

        # Calculate confidence score
        confidence_score = min(0.95, agent_cap.quality_score * agent_cap.reliability)

        return SelectionResult(
            selected_agent=selected_agent,
            selected_model=selected_model,
            selected_tools=selected_tools,
            configuration=configuration,
            estimated_cost=estimated_cost,
            estimated_latency=estimated_latency,
            confidence_score=confidence_score,
            alternatives=alternatives,
            reasoning=reasoning,
        )


# Convenience function
def select_optimal_configuration(
    task_description: str, **context_kwargs
) -> SelectionResult:
    """Convenience function for selecting optimal configuration."""
    meta_agent = MetaAgent()

    context = TaskContext(
        task_description=task_description,
        task_type=meta_agent.analyze_task(task_description),
        input_size=context_kwargs.get("input_size", 1000),
        urgency=context_kwargs.get("urgency", "medium"),
        quality_requirement=context_kwargs.get("quality_requirement", "medium"),
        privacy_level=context_kwargs.get("privacy_level", "public"),
        budget_limit=context_kwargs.get("budget_limit"),
        user_preferences=context_kwargs.get("user_preferences", {}),
    )

    return meta_agent.select(task_description, context)
