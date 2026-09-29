"""
Kosika Advanced Intelligence System

This module provides adaptive execution, error prediction, resource optimization,
and learning capabilities for the Kosika engine.
"""

import time
import threading
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Callable
from enum import Enum
import json
import statistics
import hashlib


class ExecutionStrategy(Enum):
    """Execution strategies."""
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    DISTRIBUTED = "distributed"
    HYBRID = "hybrid"


class ErrorSeverity(Enum):
    """Error severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ExecutionPatternType(Enum):
    """Types of execution patterns."""
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    FUNCTION_CALL = "function_call"
    WORKFLOW_STEP = "workflow_step"
    AGENT_STEP = "agent_step"


@dataclass
class ExecutionPattern:
    """A pattern of execution behavior."""
    pattern_id: str
    operation_type: str
    avg_duration: float
    avg_resource_usage: Dict[str, float]
    success_rate: float
    frequency: int
    last_seen: datetime
    characteristics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ErrorPrediction:
    """Prediction of potential errors."""
    error_type: str
    severity: ErrorSeverity
    probability: float
    location: str
    suggested_fix: str
    confidence: float


@dataclass
class ResourcePrediction:
    """Prediction of resource requirements."""
    cpu_estimate: float
    memory_estimate: float
    duration_estimate: float
    confidence: float
    suggested_allocation: Dict[str, float]


@dataclass
class ExecutionProfile:
    """Profile of an execution."""
    execution_id: str
    operation: str
    duration: float
    resource_usage: Dict[str, float]
    success: bool
    error: Optional[str] = None
    strategy: ExecutionStrategy = ExecutionStrategy.SEQUENTIAL
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class PatternExtractor:
    """Extract patterns from execution history."""

    def __init__(self):
        self.patterns: Dict[str, ExecutionPattern] = {}
        self.execution_history: List[ExecutionProfile] = []
        self.min_samples = 5

    def add_execution(self, profile: ExecutionProfile):
        """Add an execution profile to history."""
        self.execution_history.append(profile)
        self._update_patterns()

    def _update_patterns(self):
        """Update patterns based on execution history."""
        # Group by operation type
        operation_groups = defaultdict(list)
        for profile in self.execution_history:
            operation_groups[profile.operation].append(profile)

        # Extract patterns for each operation
        for operation, profiles in operation_groups.items():
            if len(profiles) >= self.min_samples:
                pattern = self._extract_pattern(operation, profiles)
                self.patterns[pattern.pattern_id] = pattern

    def _extract_pattern(self, operation: str, profiles: List[ExecutionProfile]) -> ExecutionPattern:
        """Extract a pattern from execution profiles."""
        durations = [p.duration for p in profiles]
        success_count = sum(1 for p in profiles if p.success)

        # Calculate average resource usage
        avg_resources = {}
        if profiles and profiles[0].resource_usage:
            for resource in profiles[0].resource_usage.keys():
                values = [p.resource_usage.get(resource, 0) for p in profiles if p.resource_usage]
                if values:
                    avg_resources[resource] = statistics.mean(values)

        pattern_id = hashlib.md5(operation.encode()).hexdigest()[:8]

        return ExecutionPattern(
            pattern_id=pattern_id,
            operation_type=operation,
            avg_duration=statistics.mean(durations),
            avg_resource_usage=avg_resources,
            success_rate=success_count / len(profiles),
            frequency=len(profiles),
            last_seen=max(p.timestamp for p in profiles),
            characteristics={
                "duration_std": statistics.stdev(durations) if len(durations) > 1 else 0,
                "min_duration": min(durations),
                "max_duration": max(durations)
            }
        )

    def get_pattern(self, operation: str) -> Optional[ExecutionPattern]:
        """Get pattern for an operation."""
        pattern_id = hashlib.md5(operation.encode()).hexdigest()[:8]
        return self.patterns.get(pattern_id)

    def get_similar_patterns(self, operation: str, threshold: float = 0.8) -> List[ExecutionPattern]:
        """Get patterns similar to the given operation."""
        target_pattern = self.get_pattern(operation)
        if not target_pattern:
            return []

        similar = []
        for pattern in self.patterns.values():
            if pattern.pattern_id != target_pattern.pattern_id:
                similarity = self._calculate_similarity(target_pattern, pattern)
                if similarity >= threshold:
                    similar.append((pattern, similarity))

        similar.sort(key=lambda x: x[1], reverse=True)
        return [p for p, _ in similar]

    def _calculate_similarity(self, pattern1: ExecutionPattern, pattern2: ExecutionPattern) -> float:
        """Calculate similarity between two patterns."""
        # Simple similarity based on resource usage
        if not pattern1.avg_resource_usage or not pattern2.avg_resource_usage:
            return 0.0

        similarity_scores = []
        for resource in set(pattern1.avg_resource_usage.keys()) & set(pattern2.avg_resource_usage.keys()):
            val1 = pattern1.avg_resource_usage[resource]
            val2 = pattern2.avg_resource_usage[resource]
            if val1 > 0 and val2 > 0:
                similarity = 1.0 - abs(val1 - val2) / max(val1, val2)
                similarity_scores.append(similarity)

        return statistics.mean(similarity_scores) if similarity_scores else 0.0


class ErrorPredictor:
    """Predict potential errors based on patterns and analysis."""

    def __init__(self):
        self.error_patterns: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.common_errors = {
            "memory_exhaustion": {
                "indicators": ["high_memory_usage", "large_allocation"],
                "severity": ErrorSeverity.HIGH,
                "fix": "Reduce memory allocation or increase available memory"
            },
            "timeout": {
                "indicators": ["long_duration", "slow_operation"],
                "severity": ErrorSeverity.MEDIUM,
                "fix": "Optimize operation or increase timeout"
            },
            "dependency_failure": {
                "indicators": ["external_call", "network_error"],
                "severity": ErrorSeverity.MEDIUM,
                "fix": "Add retry logic or fallback implementation"
            },
            "type_mismatch": {
                "indicators": ["schema_validation_error", "type_error"],
                "severity": ErrorSeverity.LOW,
                "fix": "Fix type annotations or add conversion"
            }
        }

    def analyze_code(self, code: str, context: Dict[str, Any]) -> List[ErrorPrediction]:
        """Analyze code for potential errors."""
        predictions = []

        # Simple static analysis patterns
        if "recursive" in code.lower() and "max_depth" not in code.lower():
            predictions.append(ErrorPrediction(
                error_type="stack_overflow",
                severity=ErrorSeverity.HIGH,
                probability=0.7,
                location="recursive_function",
                suggested_fix="Add recursion depth limit or convert to iterative approach",
                confidence=0.6
            ))

        if "while true" in code.lower() and "break" not in code.lower():
            predictions.append(ErrorPrediction(
                error_type="infinite_loop",
                severity=ErrorSeverity.CRITICAL,
                probability=0.8,
                location="infinite_loop",
                suggested_fix="Add break condition or timeout",
                confidence=0.7
            ))

        if "large_file" in code.lower() and "stream" not in code.lower():
            predictions.append(ErrorPrediction(
                error_type="memory_exhaustion",
                severity=ErrorSeverity.HIGH,
                probability=0.6,
                location="file_processing",
                suggested_fix="Use streaming instead of loading entire file",
                confidence=0.5
            ))

        return predictions

    def predict_from_pattern(self, pattern: ExecutionPattern) -> List[ErrorPrediction]:
        """Predict errors based on execution pattern."""
        predictions = []

        if pattern.success_rate < 0.8:
            predictions.append(ErrorPrediction(
                error_type="unreliable_operation",
                severity=ErrorSeverity.MEDIUM,
                probability=1.0 - pattern.success_rate,
                location=pattern.operation_type,
                suggested_fix="Add retry logic or improve error handling",
                confidence=0.8
            ))

        if pattern.avg_duration > 30.0:  # More than 30 seconds
            predictions.append(ErrorPrediction(
                error_type="timeout",
                severity=ErrorSeverity.MEDIUM,
                probability=0.5,
                location=pattern.operation_type,
                suggested_fix="Increase timeout or optimize operation",
                confidence=0.6
            ))

        if pattern.avg_resource_usage.get("memory_percent", 0) > 80:
            predictions.append(ErrorPrediction(
                error_type="memory_exhaustion",
                severity=ErrorSeverity.HIGH,
                probability=0.7,
                location=pattern.operation_type,
                suggested_fix="Optimize memory usage or increase allocation",
                confidence=0.7
            ))

        return predictions

    def predict_from_context(self, context: Dict[str, Any]) -> List[ErrorPrediction]:
        """Predict errors based on execution context."""
        predictions = []

        # Check resource constraints
        if context.get("memory_limit", 100) < context.get("estimated_memory", 50):
            predictions.append(ErrorPrediction(
                error_type="memory_exhaustion",
                severity=ErrorSeverity.HIGH,
                probability=0.8,
                location="resource_allocation",
                suggested_fix="Increase memory limit or optimize memory usage",
                confidence=0.7
            ))

        if context.get("time_limit", 60) < context.get("estimated_duration", 30):
            predictions.append(ErrorPrediction(
                error_type="timeout",
                severity=ErrorSeverity.MEDIUM,
                probability=0.6,
                location="resource_allocation",
                suggested_fix="Increase time limit or optimize operation",
                confidence=0.6
            ))

        return predictions


class ResourcePredictor:
    """Predict resource requirements for operations."""

    def __init__(self):
        self.resource_history: Dict[str, List[Dict[str, float]]] = defaultdict(list)
        self.baseline_requirements = {
            "simple_operation": {"cpu": 0.1, "memory": 100, "duration": 1.0},
            "complex_operation": {"cpu": 0.5, "memory": 500, "duration": 10.0},
            "io_operation": {"cpu": 0.2, "memory": 200, "duration": 5.0},
            "model_call": {"cpu": 0.8, "memory": 1000, "duration": 3.0}
        }

    def add_resource_data(self, operation: str, resource_data: Dict[str, float]):
        """Add resource usage data for an operation."""
        self.resource_history[operation].append(resource_data)

    def predict_resources(self, operation: str, context: Dict[str, Any]) -> ResourcePrediction:
        """Predict resource requirements for an operation."""
        historical_data = self.resource_history.get(operation, [])

        if len(historical_data) >= 3:
            # Use historical data for prediction
            cpu_values = [d.get("cpu", 0) for d in historical_data]
            memory_values = [d.get("memory", 0) for d in historical_data]
            duration_values = [d.get("duration", 0) for d in historical_data]

            cpu_estimate = statistics.mean(cpu_values)
            memory_estimate = statistics.mean(memory_values)
            duration_estimate = statistics.mean(duration_values)
            confidence = min(0.9, 0.5 + len(historical_data) * 0.1)
        else:
            # Use baseline requirements
            baseline = self._get_baseline(operation)
            cpu_estimate = baseline["cpu"]
            memory_estimate = baseline["memory"]
            duration_estimate = baseline["duration"]
            confidence = 0.3

        # Adjust based on context
        input_size = context.get("input_size", 1)
        scale_factor = min(10.0, max(0.1, input_size / 1000))  # Scale between 0.1x and 10x

        cpu_estimate *= scale_factor
        memory_estimate *= scale_factor
        duration_estimate *= scale_factor

        suggested_allocation = {
            "cpu": cpu_estimate * 1.5,  # Add 50% buffer
            "memory": memory_estimate * 1.5,
            "timeout": duration_estimate * 2.0
        }

        return ResourcePrediction(
            cpu_estimate=cpu_estimate,
            memory_estimate=memory_estimate,
            duration_estimate=duration_estimate,
            confidence=confidence,
            suggested_allocation=suggested_allocation
        )

    def _get_baseline(self, operation: str) -> Dict[str, float]:
        """Get baseline requirements for an operation."""
        # Simple heuristic based on operation name
        if "model" in operation.lower() or "ai" in operation.lower():
            return self.baseline_requirements["model_call"]
        elif "io" in operation.lower() or "file" in operation.lower():
            return self.baseline_requirements["io_operation"]
        elif "complex" in operation.lower() or "analyze" in operation.lower():
            return self.baseline_requirements["complex_operation"]
        else:
            return self.baseline_requirements["simple_operation"]


class AdaptiveExecutor:
    """Adaptive execution system that learns and optimizes."""

    def __init__(self):
        self.pattern_extractor = PatternExtractor()
        self.error_predictor = ErrorPredictor()
        self.resource_predictor = ResourcePredictor()
        self.execution_strategies: Dict[str, ExecutionStrategy] = {}
        self.performance_history: Dict[str, List[float]] = defaultdict(list)
        self.patterns: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def analyze_operation(self, operation: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze an operation and provide recommendations."""
        analysis = {
            "operation": operation,
            "pattern": self.pattern_extractor.get_pattern(operation),
            "error_predictions": [],
            "resource_prediction": None,
            "recommended_strategy": ExecutionStrategy.SEQUENTIAL,
            "confidence": 0.0
        }

        # Get error predictions
        pattern = self.pattern_extractor.get_pattern(operation)
        if pattern:
            analysis["error_predictions"].extend(self.error_predictor.predict_from_pattern(pattern))

        analysis["error_predictions"].extend(self.error_predictor.predict_from_context(context))

        # Predict resources
        analysis["resource_prediction"] = self.resource_predictor.predict_resources(operation, context)

        # Recommend strategy
        analysis["recommended_strategy"] = self._recommend_strategy(operation, context, analysis)

        # Calculate overall confidence
        pattern_confidence = 0.8 if pattern else 0.3
        resource_confidence = analysis["resource_prediction"].confidence
        analysis["confidence"] = (pattern_confidence + resource_confidence) / 2

        # Store pattern analysis for later retrieval
        if pattern:
            self.patterns[operation].append({
                "operation": operation,
                "avg_duration": pattern.avg_duration,
                "success_rate": pattern.success_rate,
                "frequency": pattern.frequency,
                "characteristics": pattern.characteristics,
            })

        return analysis

    def _recommend_strategy(self, operation: str, context: Dict[str, Any], analysis: Dict[str, Any]) -> ExecutionStrategy:
        """Recommend execution strategy based on analysis."""
        resource_pred = analysis["resource_prediction"]

        # Simple strategy selection logic
        if resource_pred.duration_estimate > 10.0 and context.get("parallel_available", False):
            return ExecutionStrategy.PARALLEL

        if resource_pred.duration_estimate > 60.0 and context.get("distributed_available", False):
            return ExecutionStrategy.DISTRIBUTED

        if context.get("has_dependencies", False) and context.get("parallel_available", False):
            return ExecutionStrategy.HYBRID

        return ExecutionStrategy.SEQUENTIAL

    def record_execution(self, profile: ExecutionProfile):
        """Record execution data for learning."""
        self.pattern_extractor.add_execution(profile)

        # Update performance history
        self.performance_history[profile.operation].append(profile.duration)

        # Update resource predictor
        if profile.resource_usage:
            resource_data = {
                "cpu": profile.resource_usage.get("cpu", 0),
                "memory": profile.resource_usage.get("memory", 0),
                "duration": profile.duration
            }
            self.resource_predictor.add_resource_data(profile.operation, resource_data)

    def get_performance_insights(self, operation: str) -> Dict[str, Any]:
        """Get performance insights for an operation."""
        durations = self.performance_history.get(operation, [])

        if not durations:
            return {"status": "insufficient_data"}

        return {
            "operation": operation,
            "executions": len(durations),
            "avg_duration": statistics.mean(durations),
            "min_duration": min(durations),
            "max_duration": max(durations),
            "duration_std": statistics.stdev(durations) if len(durations) > 1 else 0,
            "trend": self._calculate_trend(durations)
        }

    def _calculate_trend(self, values: List[float]) -> str:
        """Calculate performance trend."""
        if len(values) < 3:
            return "insufficient_data"

        recent = values[-5:]
        older = values[:-5] if len(values) > 5 else values[:len(values)//2]

        recent_avg = statistics.mean(recent)
        older_avg = statistics.mean(older)

        if recent_avg < older_avg * 0.9:
            return "improving"
        elif recent_avg > older_avg * 1.1:
            return "degrading"
        else:
            return "stable"


class LearningSystem:
    """Machine learning system for advanced predictions."""

    def __init__(self):
        self.model_performance: Dict[str, List[float]] = defaultdict(list)
        self.feature_importance: Dict[str, float] = {}
        self.training_data: List[Dict[str, Any]] = []

    def add_training_data(self, features: Dict[str, Any], target: float):
        """Add training data for model learning."""
        self.training_data.append({
            "features": features,
            "target": target,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    def train_simple_model(self, feature_name: str) -> Dict[str, float]:
        """Train a simple linear regression model."""
        if len(self.training_data) < 10:
            return {"status": "insufficient_data"}

        # Extract feature and target values
        x_values = []
        y_values = []

        for data in self.training_data:
            if feature_name in data["features"]:
                x_values.append(data["features"][feature_name])
                y_values.append(data["target"])

        if len(x_values) < 10:
            return {"status": "insufficient_data"}

        # Simple linear regression
        n = len(x_values)
        sum_x = sum(x_values)
        sum_y = sum(y_values)
        sum_xy = sum(x * y for x, y in zip(x_values, y_values))
        sum_x2 = sum(x * x for x in x_values)

        slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x * sum_x)
        intercept = (sum_y - slope * sum_x) / n

        # Calculate R-squared
        y_mean = statistics.mean(y_values)
        ss_total = sum((y - y_mean) ** 2 for y in y_values)
        ss_residual = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(x_values, y_values))
        r_squared = 1 - (ss_residual / ss_total) if ss_total > 0 else 0

        model = {
            "slope": slope,
            "intercept": intercept,
            "r_squared": r_squared,
            "feature": feature_name,
            "samples": n
        }

        self.feature_importance[feature_name] = abs(slope)  # Use absolute slope as importance

        return model

    def predict(self, features: Dict[str, Any]) -> Dict[str, float]:
        """Make predictions using trained models."""
        predictions = {}

        for feature_name, importance in self.feature_importance.items():
            if feature_name in features:
                # Simple prediction using linear relationship
                prediction = features[feature_name] * importance
                predictions[feature_name] = prediction

        return predictions

    def get_feature_importance(self) -> Dict[str, float]:
        """Get feature importance scores."""
        return self.feature_importance.copy()


class IntelligenceSystem:
    """Main intelligence system coordinating all components."""

    def __init__(self):
        self.adaptive_executor = AdaptiveExecutor()
        self.learning_system = LearningSystem()
        self.insights_cache: Dict[str, Dict[str, Any]] = {}
        self.cache_ttl = 300  # 5 minutes

    def analyze_and_optimize(self, operation: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze operation and provide optimization recommendations."""
        # Check cache first
        cache_key = f"{operation}_{hash(json.dumps(context, sort_keys=True))}"
        if cache_key in self.insights_cache:
            cached = self.insights_cache[cache_key]
            if (datetime.now(timezone.utc) - datetime.fromisoformat(cached["cached_at"])).total_seconds() < self.cache_ttl:
                return cached

        # Perform analysis
        analysis = self.adaptive_executor.analyze_operation(operation, context)

        # Add learning system insights
        if self.learning_system.feature_importance:
            analysis["feature_importance"] = self.learning_system.get_feature_importance()

        # Cache the result
        analysis["cached_at"] = datetime.now(timezone.utc).isoformat()
        self.insights_cache[cache_key] = analysis

        return analysis

    def record_execution_result(self, operation: str, duration: float, success: bool, resource_usage: Dict[str, float]):
        """Record execution result for learning."""
        profile = ExecutionProfile(
            execution_id=str(uuid.uuid4()),
            operation=operation,
            duration=duration,
            resource_usage=resource_usage,
            success=success
        )

        self.adaptive_executor.record_execution(profile)

        # Add to learning system
        features = resource_usage.copy()
        features["operation_hash"] = hashlib.md5(operation.encode()).hexdigest()[:8]
        self.learning_system.add_training_data(features, duration)

    def get_system_insights(self) -> Dict[str, Any]:
        """Get overall system insights."""
        return {
            "operations_analyzed": len(self.adaptive_executor.pattern_extractor.patterns),
            "training_samples": len(self.learning_system.training_data),
            "cached_insights": len(self.insights_cache),
            "feature_importance": self.learning_system.get_feature_importance(),
            "top_operations": self._get_top_operations()
        }

    def _get_top_operations(self) -> List[Dict[str, Any]]:
        """Get most frequently executed operations."""
        operation_counts = defaultdict(int)
        for profile in self.adaptive_executor.pattern_extractor.execution_history:
            operation_counts[profile.operation] += 1

        sorted_ops = sorted(operation_counts.items(), key=lambda x: x[1], reverse=True)
        return [{"operation": op, "count": count} for op, count in sorted_ops[:10]]


# Convenience function to create a complete intelligence system
def create_intelligence_system() -> IntelligenceSystem:
    """Create a complete intelligence system with default configuration."""
    return IntelligenceSystem()