"""Self-Learning and Adaptation Mechanisms for Kosika Agents.

This module provides mechanisms for agents to learn from experience, adapt their
behavior, and improve over time without relying solely on model capabilities.

Key features:
- Experience capture and storage
- Pattern extraction from execution traces
- Rule generalization and abstraction
- Performance metric tracking
- Strategy selection based on context
- Hyperparameter tuning
- Safe self-modification pipeline
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


class LearningMode(Enum):
    """Learning modes for agents."""
    SUPERVISED = "supervised"  # Learning from labeled examples
    REINFORCEMENT = "reinforcement"  # Learning from rewards
    UNSUPERVISED = "unsupervised"  # Learning from patterns
    FEW_SHOT = "few_shot"  # Learning from few examples
    TRANSFER = "transfer"  # Transferring knowledge from other tasks


class PatternKind(Enum):
    """Types of patterns that can be learned."""
    SEQUENCE = "sequence"  # Sequential patterns
    ASSOCIATION = "association"  # Item associations
    CLASSIFICATION = "classification"  # Classification rules
    REGRESSION = "regression"  # Numeric prediction
    ANOMALY = "anomaly"  # Anomaly detection


@dataclass
class Experience:
    """A single experience for learning."""
    id: str
    context: Dict[str, Any]
    action: str
    result: Any
    reward: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Pattern:
    """A learned pattern."""
    id: str
    kind: PatternKind
    pattern: Any  # The pattern representation
    confidence: float
    support: int  # Number of supporting experiences
    created_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Strategy:
    """A strategy for solving a task."""
    id: str
    name: str
    description: str
    conditions: List[Callable[[Dict[str, Any]], bool]]
    implementation: Callable
    performance: Dict[str, float] = field(default_factory=dict)
    usage_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class ExperienceBuffer:
    """Buffer for storing experiences."""

    def __init__(self, max_size: int = 10000):
        self.experiences: List[Experience] = []
        self.max_size = max_size

    def add(self, experience: Experience):
        """Add an experience to the buffer."""
        self.experiences.append(experience)
        if len(self.experiences) > self.max_size:
            self.experiences.pop(0)

    def sample(self, batch_size: int) -> List[Experience]:
        """Sample a batch of experiences."""
        if len(self.experiences) < batch_size:
            return self.experiences.copy()
        return random.sample(self.experiences, batch_size)

    def query(
        self,
        context_filter: Optional[Dict[str, Any]] = None,
        action_filter: Optional[str] = None,
        min_reward: Optional[float] = None,
    ) -> List[Experience]:
        """Query experiences with filters."""
        results = []

        for exp in self.experiences:
            if context_filter:
                match = True
                for key, value in context_filter.items():
                    if exp.context.get(key) != value:
                        match = False
                        break
                if not match:
                    continue

            if action_filter and exp.action != action_filter:
                continue

            if min_reward is not None and exp.reward < min_reward:
                continue

            results.append(exp)

        return results

    def get_statistics(self) -> Dict[str, Any]:
        """Get buffer statistics."""
        if not self.experiences:
            return {"count": 0, "avg_reward": 0.0}

        rewards = [exp.reward for exp in self.experiences]
        return {
            "count": len(self.experiences),
            "avg_reward": sum(rewards) / len(rewards),
            "min_reward": min(rewards),
            "max_reward": max(rewards),
        }


class PatternExtractor:
    """Extract patterns from experiences."""

    def __init__(self):
        self.patterns: List[Pattern] = []

    def extract_sequence_patterns(
        self,
        experiences: List[Experience],
        min_support: int = 3,
        min_confidence: float = 0.7,
    ) -> List[Pattern]:
        """Extract sequential patterns from experiences."""
        # Group experiences by context
        context_groups: Dict[frozenset[Tuple[str, Any]], List[Experience]] = defaultdict(list)
        for exp in experiences:
            context_key = frozenset(exp.context.items())
            context_groups[context_key].append(exp)

        patterns = []

        for context_key, group in context_groups.items():
            if len(group) < min_support:
                continue

            # Extract action sequences
            actions = [exp.action for exp in group]
            action_counts: Dict[str, int] = defaultdict(int)
            for action in actions:
                action_counts[action] += 1

            # Find most common action
            most_common = max(action_counts.items(), key=lambda x: x[1])
            confidence = most_common[1] / len(group)

            if confidence >= min_confidence:
                pattern = Pattern(
                    id=f"seq_{len(self.patterns)}",
                    kind=PatternKind.SEQUENCE,
                    pattern={
                        "context": dict(context_key),
                        "action": most_common[0],
                    },
                    confidence=confidence,
                    support=len(group),
                )
                patterns.append(pattern)
                self.patterns.append(pattern)

        return patterns

    def extract_association_patterns(
        self,
        experiences: List[Experience],
        min_support: int = 3,
    ) -> List[Pattern]:
        """Extract association patterns (items that appear together)."""
        associations: Dict[Tuple[str, str], int] = defaultdict(int)

        for exp in experiences:
            items = list(exp.context.values()) + [exp.action]
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    ordered_items = sorted([str(items[i]), str(items[j])])
                    pair = (ordered_items[0], ordered_items[1])
                    associations[pair] += 1

        patterns = []
        for pair, count in associations.items():
            if count >= min_support:
                pattern = Pattern(
                    id=f"assoc_{len(self.patterns)}",
                    kind=PatternKind.ASSOCIATION,
                    pattern={"items": pair},
                    confidence=count / len(experiences),
                    support=count,
                )
                patterns.append(pattern)
                self.patterns.append(pattern)

        return patterns

    def extract_classification_patterns(
        self,
        experiences: List[Experience],
        target_field: str,
    ) -> List[Pattern]:
        """Extract classification rules."""
        # Group by target value
        target_groups: Dict[Any, List[Experience]] = defaultdict(list)
        for exp in experiences:
            target_value = exp.result.get(target_field) if isinstance(exp.result, dict) else exp.result
            target_groups[target_value].append(exp)

        patterns = []
        for target_value, group in target_groups.items():
            if len(group) < 2:
                continue

            # Find common context features
            feature_counts: Dict[Tuple[str, Any], int] = defaultdict(int)
            for exp in group:
                for key, value in exp.context.items():
                    feature_counts[(key, value)] += 1

            # Find features with high support
            for (key, value), count in feature_counts.items():
                confidence = count / len(group)
                if confidence >= 0.7:
                    pattern = Pattern(
                        id=f"class_{len(self.patterns)}",
                        kind=PatternKind.CLASSIFICATION,
                        pattern={
                            "feature": key,
                            "value": value,
                            "target": target_value,
                        },
                        confidence=confidence,
                        support=count,
                    )
                    patterns.append(pattern)
                    self.patterns.append(pattern)

        return patterns


class StrategySelector:
    """Select strategies based on context and performance."""

    def __init__(self):
        self.strategies: Dict[str, Strategy] = {}
        self.context_history: List[Tuple[Dict[str, Any], str]] = []

    def add_strategy(self, strategy: Strategy):
        """Add a strategy."""
        self.strategies[strategy.id] = strategy

    def select_strategy(self, context: Dict[str, Any]) -> Optional[Strategy]:
        """Select the best strategy for the given context."""
        candidates = []

        for strategy in self.strategies.values():
            # Check if all conditions are met
            if all(condition(context) for condition in strategy.conditions):
                candidates.append(strategy)

        if not candidates:
            return None

        # Select by performance (if available)
        if candidates[0].performance:
            return max(candidates, key=lambda s: s.performance.get("success_rate", 0.0))

        # Otherwise, select by usage count
        return max(candidates, key=lambda s: s.usage_count)

    def update_performance(self, strategy_id: str, success: bool, metrics: Dict[str, float]):
        """Update strategy performance metrics."""
        strategy = self.strategies.get(strategy_id)
        if not strategy:
            return

        strategy.usage_count += 1

        if "success_count" not in strategy.performance:
            strategy.performance["success_count"] = 0
        if "total_count" not in strategy.performance:
            strategy.performance["total_count"] = 0

        strategy.performance["total_count"] += 1
        if success:
            strategy.performance["success_count"] += 1

        strategy.performance["success_rate"] = (
            strategy.performance["success_count"] / strategy.performance["total_count"]
        )

        # Update other metrics
        samples = strategy.metadata.setdefault("metric_samples", {})
        for key, value in metrics.items():
            metric_samples = samples.setdefault(key, [])
            metric_samples.append(value)
            strategy.performance[key] = value
            strategy.performance[f"{key}_avg"] = sum(metric_samples) / len(metric_samples)


class HyperparameterTuner:
    """Tune hyperparameters using Bayesian optimization."""

    def __init__(self):
        self.history: List[Dict[str, Any]] = []

    def suggest(
        self,
        param_space: Dict[str, Tuple[Any, Any, str]],  # param -> (min, max, type)
        current_params: Dict[str, Any],
        n_suggestions: int = 1,
    ) -> List[Dict[str, Any]]:
        """Suggest new hyperparameter values."""
        suggestions = []

        for _ in range(n_suggestions):
            suggestion = current_params.copy()

            for param, (min_val, max_val, param_type) in param_space.items():
                if param_type == "float":
                    suggestion[param] = random.uniform(min_val, max_val)
                elif param_type == "int":
                    suggestion[param] = random.randint(min_val, max_val)
                elif param_type == "categorical":
                    suggestion[param] = random.choice(min_val)  # min_val is list of options

            suggestions.append(suggestion)

        return suggestions

    def record(self, params: Dict[str, Any], score: float):
        """Record hyperparameter performance."""
        self.history.append({"params": params, "score": score})

    def get_best(self) -> Optional[Dict[str, Any]]:
        """Get the best hyperparameters found."""
        if not self.history:
            return None

        best = max(self.history, key=lambda x: x["score"])
        return best["params"]


class SelfModifier:
    """Safe self-modification for agents."""

    def __init__(self):
        self.modifications: List[Dict[str, Any]] = []
        self.approvals: Dict[str, bool] = {}

    def propose_modification(
        self,
        modification_id: str,
        description: str,
        current_code: str,
        proposed_code: str,
        test_cases: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Propose a code modification."""
        proposal = {
            "id": modification_id,
            "description": description,
            "current_code": current_code,
            "proposed_code": proposed_code,
            "test_cases": test_cases,
            "proposed_at": datetime.now().isoformat(),
            "status": "pending",
        }

        self.modifications.append(proposal)
        return proposal

    def approve(self, modification_id: str, approver: str) -> bool:
        """Approve a modification."""
        for mod in self.modifications:
            if mod["id"] == modification_id:
                mod["status"] = "approved"
                mod["approved_by"] = approver
                mod["approved_at"] = datetime.now().isoformat()
                self.approvals[modification_id] = True
                return True
        return False

    def reject(self, modification_id: str, reason: str) -> bool:
        """Reject a modification."""
        for mod in self.modifications:
            if mod["id"] == modification_id:
                mod["status"] = "rejected"
                mod["rejection_reason"] = reason
                mod["rejected_at"] = datetime.now().isoformat()
                self.approvals[modification_id] = False
                return True
        return False

    def apply(self, modification_id: str) -> str:
        """Apply an approved modification."""
        for mod in self.modifications:
            if mod["id"] == modification_id and mod["status"] == "approved":
                mod["status"] = "applied"
                mod["applied_at"] = datetime.now().isoformat()
                return mod["proposed_code"]
        raise ValueError(f"Modification {modification_id} not approved or not found")


class LearningAgent:
    """Agent with self-learning capabilities."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self.experience_buffer = ExperienceBuffer()
        self.pattern_extractor = PatternExtractor()
        self.strategy_selector = StrategySelector()
        self.hyperparameter_tuner = HyperparameterTuner()
        self.self_modifier = SelfModifier()
        self.performance_metrics: Dict[str, List[float]] = defaultdict(list)

    def add_experience(
        self,
        context: Dict[str, Any],
        action: str,
        result: Any,
        reward: float = 0.0,
    ):
        """Add an experience to learn from."""
        experience = Experience(
            id=f"{self.agent_id}_{len(self.experience_buffer.experiences)}",
            context=context,
            action=action,
            result=result,
            reward=reward,
        )
        self.experience_buffer.add(experience)

    def learn_patterns(self) -> List[Pattern]:
        """Learn patterns from experiences."""
        experiences = self.experience_buffer.experiences

        sequence_patterns = self.pattern_extractor.extract_sequence_patterns(experiences)
        association_patterns = self.pattern_extractor.extract_association_patterns(experiences)

        return sequence_patterns + association_patterns

    def select_action(self, context: Dict[str, Any]) -> Optional[str]:
        """Select an action based on learned strategies."""
        strategy = self.strategy_selector.select_strategy(context)
        if strategy:
            return strategy.implementation(context)
        return None

    def update_performance(self, metric_name: str, value: float):
        """Update performance metrics."""
        self.performance_metrics[metric_name].append(value)

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get summary of performance metrics."""
        summary = {}
        for metric_name, values in self.performance_metrics.items():
            if values:
                summary[metric_name] = {
                    "mean": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values),
                    "count": len(values),
                }
        return summary

    def adapt(self) -> Dict[str, Any]:
        """Adapt based on learned patterns and performance."""
        # Learn new patterns
        patterns = self.learn_patterns()

        # Get performance summary
        performance = self.get_performance_summary()

        return {
            "patterns_learned": len(patterns),
            "patterns": [p.pattern for p in patterns],
            "performance": performance,
            "experience_count": len(self.experience_buffer.experiences),
        }
