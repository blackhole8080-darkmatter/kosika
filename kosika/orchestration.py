"""Agent Orchestration and Workflow Primitives for Kosika.

This module provides high-level orchestration primitives for coordinating
multiple agents, managing workflows, and coordinating complex AI systems.

Key features:
- Agent team composition and coordination
- Workflow orchestration and scheduling
- Task delegation and distribution
- Agent communication patterns
- Resource management and allocation
- Error handling and recovery
- Performance monitoring and optimization
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class AgentRole(Enum):
    """Roles for agents in a team."""
    COORDINATOR = "coordinator"  # Orchestrates other agents
    SPECIALIST = "specialist"  # Domain expert
    REVIEWER = "reviewer"  # Reviews and validates
    EXECUTOR = "executor"  # Executes tasks
    MONITOR = "monitor"  # Monitors performance
    LEARNER = "learner"  # Learns and adapts


class TaskStatus(Enum):
    """Status of tasks in a workflow."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class WorkflowState(Enum):
    """States of a workflow."""
    INITIALIZED = "initialized"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class Agent:
    """An agent in the system."""
    id: str
    name: str
    role: AgentRole
    capabilities: List[str]
    status: str = "idle"
    current_task: Optional[str] = None
    performance: Dict[str, float] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def can_perform(self, task_type: str) -> bool:
        """Check if agent can perform a task type."""
        return task_type in self.capabilities

    def assign_task(self, task_id: str):
        """Assign a task to the agent."""
        self.current_task = task_id
        self.status = "busy"

    def complete_task(self):
        """Mark current task as complete."""
        self.current_task = None
        self.status = "idle"


@dataclass
class Task:
    """A task in a workflow."""
    id: str
    name: str
    task_type: str
    description: str
    required_capabilities: List[str]
    input_data: Dict[str, Any]
    dependencies: List[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: Optional[str] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_ready(self, completed_tasks: Set[str]) -> bool:
        """Check if task is ready to execute (dependencies met)."""
        return all(dep in completed_tasks for dep in self.dependencies)


@dataclass
class Workflow:
    """A workflow composed of tasks."""
    id: str
    name: str
    description: str
    tasks: List[Task]
    state: WorkflowState = WorkflowState.INITIALIZED
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        for task in self.tasks:
            if task.id == task_id:
                return task
        return None

    def get_ready_tasks(self) -> List[Task]:
        """Get tasks that are ready to execute."""
        completed = {t.id for t in self.tasks if t.status == TaskStatus.COMPLETED}
        return [t for t in self.tasks if t.status == TaskStatus.PENDING and t.is_ready(completed)]

    def is_complete(self) -> bool:
        """Check if workflow is complete."""
        return all(t.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]
                   for t in self.tasks)


class AgentTeam:
    """A team of agents working together."""

    def __init__(self, team_id: str):
        self.team_id = team_id
        self.agents: Dict[str, Agent] = {}
        self.communication_log: List[Dict[str, Any]] = []

    def add_agent(self, agent: Agent):
        """Add an agent to the team."""
        self.agents[agent.id] = agent

    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get an agent by ID."""
        return self.agents.get(agent_id)

    def get_available_agents(self, capability: Optional[str] = None) -> List[Agent]:
        """Get available agents, optionally filtered by capability."""
        available = [a for a in self.agents.values() if a.status == "idle"]
        if capability:
            available = [a for a in available if a.can_perform(capability)]
        return available

    def assign_task(self, task: Task) -> Optional[Agent]:
        """Assign a task to an available agent."""
        for cap in task.required_capabilities:
            agents = self.get_available_agents(cap)
            if agents:
                agent = agents[0]  # Simple: pick first available
                agent.assign_task(task.id)
                task.assigned_agent = agent.id
                return agent
        return None

    def communicate(self, from_agent: str, to_agent: str, message: Dict[str, Any]):
        """Send a message from one agent to another."""
        self.communication_log.append({
            "from": from_agent,
            "to": to_agent,
            "message": message,
            "timestamp": datetime.now().isoformat(),
        })


class WorkflowOrchestrator:
    """Orchestrates workflow execution across agents."""

    def __init__(self):
        self.workflows: Dict[str, Workflow] = {}
        self.teams: Dict[str, AgentTeam] = {}
        self.execution_history: List[Dict[str, Any]] = []

    def create_workflow(self, workflow: Workflow) -> str:
        """Create a new workflow."""
        self.workflows[workflow.id] = workflow
        return workflow.id

    def create_team(self, team: AgentTeam) -> str:
        """Create a new agent team."""
        self.teams[team.team_id] = team
        return team.team_id

    def execute_workflow(
        self,
        workflow_id: str,
        team_id: str,
        max_parallel_tasks: int = 3,
    ) -> Dict[str, Any]:
        """Execute a workflow with a team of agents."""
        workflow = self.workflows.get(workflow_id)
        team = self.teams.get(team_id)

        if not workflow or not team:
            raise ValueError("Workflow or team not found")

        workflow.state = WorkflowState.RUNNING
        workflow.started_at = datetime.now()

        completed_tasks: Set[str] = set()
        failed_tasks: Set[str] = set()

        while not workflow.is_complete():
            # Get ready tasks
            ready_tasks = workflow.get_ready_tasks()

            if not ready_tasks:
                # Check if there are running tasks
                running = [t for t in workflow.tasks if t.status == TaskStatus.RUNNING]
                if not running:
                    # No ready tasks and no running tasks - workflow stuck
                    workflow.state = WorkflowState.FAILED
                    break
                time.sleep(0.1)  # Wait for running tasks
                continue

            # Assign tasks to agents (respect parallelism limit)
            running_count = len([t for t in workflow.tasks if t.status == TaskStatus.RUNNING])
            tasks_to_start = ready_tasks[:max_parallel_tasks - running_count]

            for task in tasks_to_start:
                agent = team.assign_task(task)
                if agent:
                    task.status = TaskStatus.RUNNING
                    task.started_at = datetime.now()
                    # Simulate task execution
                    self._execute_task(task, agent)
                else:
                    task.status = TaskStatus.BLOCKED

            # Update completed tasks
            for task in workflow.tasks:
                if task.status == TaskStatus.RUNNING:
                    # Check if task is done (simulated)
                    if self._is_task_complete(task):
                        task.status = TaskStatus.COMPLETED
                        task.completed_at = datetime.now()
                        completed_tasks.add(task.id)
                        # Release agent
                        assigned_agent = (
                            team.get_agent(task.assigned_agent)
                            if task.assigned_agent is not None
                            else None
                        )
                        if assigned_agent:
                            assigned_agent.complete_task()
                    elif task.status == TaskStatus.FAILED:
                        failed_tasks.add(task.id)
                        assigned_agent = (
                            team.get_agent(task.assigned_agent)
                            if task.assigned_agent is not None
                            else None
                        )
                        if assigned_agent:
                            assigned_agent.complete_task()

        workflow.completed_at = datetime.now()

        if workflow.is_complete() and not failed_tasks:
            workflow.state = WorkflowState.COMPLETED
        else:
            workflow.state = WorkflowState.FAILED

        result = {
            "workflow_id": workflow_id,
            "state": workflow.state.value,
            "completed_tasks": len(completed_tasks),
            "failed_tasks": len(failed_tasks),
            "duration": (workflow.completed_at - workflow.started_at).total_seconds() if workflow.completed_at and workflow.started_at else 0,
        }

        self.execution_history.append(result)
        return result

    def _execute_task(self, task: Task, agent: Agent):
        """Execute a task (placeholder for actual execution)."""
        # In a real implementation, this would call the agent's execute method
        pass

    def _is_task_complete(self, task: Task) -> bool:
        """Check if a task is complete (placeholder)."""
        # In a real implementation, this would check the agent's status
        return True


class ResourceScheduler:
    """Schedule and manage resources for agents."""

    def __init__(self):
        self.resources: Dict[str, Dict[str, Any]] = {}
        self.allocations: Dict[str, List[str]] = defaultdict(list)  # resource -> task_ids

    def add_resource(self, resource_id: str, capacity: int, resource_type: str):
        """Add a resource."""
        self.resources[resource_id] = {
            "capacity": capacity,
            "type": resource_type,
            "available": capacity,
        }

    def allocate(self, task_id: str, resource_requirements: Dict[str, int]) -> bool:
        """Allocate resources for a task."""
        # Check if all resources are available
        for resource_id, amount in resource_requirements.items():
            if resource_id not in self.resources:
                return False
            if self.resources[resource_id]["available"] < amount:
                return False

        # Allocate
        for resource_id, amount in resource_requirements.items():
            self.resources[resource_id]["available"] -= amount
            self.allocations[resource_id].append(task_id)

        return True

    def release(self, task_id: str, resource_requirements: Dict[str, int]):
        """Release resources from a task."""
        for resource_id, amount in resource_requirements.items():
            if resource_id in self.resources:
                self.resources[resource_id]["available"] += amount
                if task_id in self.allocations[resource_id]:
                    self.allocations[resource_id].remove(task_id)


class CommunicationBus:
    """Communication bus for agent-to-agent messaging."""

    def __init__(self):
        self.channels: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.subscriptions: Dict[str, Set[str]] = defaultdict(set)  # channel -> agent_ids

    def publish(self, channel: str, message: Dict[str, Any], sender: str):
        """Publish a message to a channel."""
        self.channels[channel].append({
            "message": message,
            "sender": sender,
            "timestamp": datetime.now().isoformat(),
        })

    def subscribe(self, agent_id: str, channel: str):
        """Subscribe an agent to a channel."""
        self.subscriptions[channel].add(agent_id)

    def get_messages(self, agent_id: str, channel: str) -> List[Dict[str, Any]]:
        """Get messages for an agent from a channel."""
        if agent_id not in self.subscriptions[channel]:
            return []
        return self.channels[channel]

    def clear_channel(self, channel: str):
        """Clear messages from a channel."""
        self.channels[channel] = []


class ErrorRecovery:
    """Error detection and recovery mechanisms."""

    def __init__(self):
        self.error_log: List[Dict[str, Any]] = []
        self.recovery_strategies: Dict[str, Callable] = {}

    def register_recovery(self, error_type: str, strategy: Callable):
        """Register a recovery strategy for an error type."""
        self.recovery_strategies[error_type] = strategy

    def handle_error(self, error: Exception, context: Dict[str, Any]) -> bool:
        """Handle an error with appropriate recovery strategy."""
        error_type = type(error).__name__

        self.error_log.append({
            "error_type": error_type,
            "error_message": str(error),
            "context": context,
            "timestamp": datetime.now().isoformat(),
        })

        if error_type in self.recovery_strategies:
            try:
                return self.recovery_strategies[error_type](error, context)
            except Exception as recovery_error:
                self.error_log.append({
                    "error_type": "RecoveryFailed",
                    "error_message": str(recovery_error),
                    "context": {"original_error": error_type},
                    "timestamp": datetime.now().isoformat(),
                })
                return False

        return False

    def get_error_summary(self) -> Dict[str, Any]:
        """Get summary of errors."""
        error_counts: Dict[str, int] = defaultdict(int)
        for error in self.error_log:
            error_counts[error["error_type"]] += 1

        return {
            "total_errors": len(self.error_log),
            "error_types": dict(error_counts),
            "recent_errors": self.error_log[-10:] if self.error_log else [],
        }


class PerformanceMonitor:
    """Monitor and optimize agent and workflow performance."""

    def __init__(self):
        self.metrics: Dict[str, List[float]] = defaultdict(list)
        self.alerts: List[Dict[str, Any]] = []

    def record_metric(self, metric_name: str, value: float):
        """Record a performance metric."""
        self.metrics[metric_name].append(value)

    def get_metric_summary(self, metric_name: str) -> Dict[str, float]:
        """Get summary statistics for a metric."""
        values = self.metrics[metric_name]
        if not values:
            return {}

        return {
            "mean": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
            "count": len(values),
            "last": values[-1],
        }

    def check_thresholds(self, thresholds: Dict[str, Tuple[float, float]]) -> List[str]:
        """Check if metrics exceed thresholds."""
        alerts = []

        for metric_name, (min_val, max_val) in thresholds.items():
            summary = self.get_metric_summary(metric_name)
            if summary:
                if summary["last"] < min_val or summary["last"] > max_val:
                    alerts.append(f"{metric_name} out of range: {summary['last']}")

        return alerts

    def suggest_optimizations(self) -> List[str]:
        """Suggest optimizations based on metrics."""
        suggestions = []

        # Check for slow agents
        if "agent_latency" in self.metrics:
            summary = self.get_metric_summary("agent_latency")
            if summary and summary["mean"] > 5.0:  # 5 seconds threshold
                suggestions.append("Consider adding more agents to reduce latency")

        # Check for high error rates
        if "error_rate" in self.metrics:
            summary = self.get_metric_summary("error_rate")
            if summary and summary["mean"] > 0.1:  # 10% error rate
                suggestions.append("Review error handling and improve task validation")

        return suggestions
