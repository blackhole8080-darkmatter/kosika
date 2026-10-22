"""Comprehensive Agent Evaluation System.

Tests agent intelligence across 5 difficulty levels (0-5) with 25 exercises per level.
Exercises include: AI problems, project building, writing, server creation, database integration.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Tuple


class DifficultyLevel(Enum):
    """Difficulty levels for agent evaluation."""
    LEVEL_0 = 0  # Basic tasks, single step
    LEVEL_1 = 1  # Simple multi-step tasks
    LEVEL_2 = 2  # Moderate complexity, some reasoning
    LEVEL_3 = 3  # Complex tasks, requires planning
    LEVEL_4 = 4  # Advanced, requires optimization
    LEVEL_5 = 5  # Expert, multi-system integration


class ExerciseCategory(Enum):
    """Categories of exercises."""
    AI_PROBLEM = "ai_problem"
    PROJECT_BUILD = "project_build"
    WRITING = "writing"
    SERVER_CREATION = "server_creation"
    DATABASE_INTEGRATION = "database_integration"


class ExerciseResult(Enum):
    """Result of an exercise attempt."""
    PASSED = "passed"
    FAILED = "failed"
    PARTIAL = "partial"
    ERROR = "error"


@dataclass
class Exercise:
    """An exercise for testing agent capabilities."""
    id: str
    level: DifficultyLevel
    category: ExerciseCategory
    title: str
    description: str
    input_data: Dict[str, Any]
    expected_output: Any
    time_limit: int = 60  # seconds
    hints: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Attempt:
    """An attempt at an exercise."""
    exercise_id: str
    output: Any
    execution_time: float
    result: ExerciseResult
    score: float  # 0.0 to 1.0
    errors: List[str] = field(default_factory=list)
    hints_used: int = 0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class LevelProgress:
    """Progress through a difficulty level."""
    level: DifficultyLevel
    total_exercises: int
    passed: int
    failed: int
    partial: int
    average_score: float
    time_taken: float
    completed: bool = False


class ExerciseEvaluator(ABC):
    """Base class for exercise evaluators."""

    @abstractmethod
    def evaluate(self, exercise: Exercise, output: Any) -> Tuple[ExerciseResult, float, List[str]]:
        """Evaluate exercise output. Returns (result, score, errors)."""
        pass


class AgentEvaluator:
    """Evaluates agent performance across exercises."""

    def __init__(self):
        self.exercises: Dict[str, Exercise] = {}
        self.attempts: List[Attempt] = []
        self.level_progress: Dict[int, LevelProgress] = {}
        self.evaluators: Dict[ExerciseCategory, ExerciseEvaluator] = {}

    def register_exercise(self, exercise: Exercise):
        """Register an exercise."""
        self.exercises[exercise.id] = exercise

    def register_evaluator(self, category: ExerciseCategory, evaluator: ExerciseEvaluator):
        """Register an evaluator for a category."""
        self.evaluators[category] = evaluator

    def attempt_exercise(
        self,
        exercise_id: str,
        agent: Callable[[Dict[str, Any]], Any],
        use_hints: bool = False,
    ) -> Attempt:
        """Attempt an exercise with an agent."""
        exercise = self.exercises.get(exercise_id)
        if not exercise:
            raise ValueError(f"Exercise not found: {exercise_id}")

        start_time = time.time()
        hints_count = 0

        try:
            # Provide hints if requested
            input_data = exercise.input_data.copy()
            if use_hints and exercise.hints:
                input_data["hints"] = exercise.hints
                hints_count = len(exercise.hints)

            # Execute agent
            output = agent(input_data)

            # Evaluate result
            evaluator = self.evaluators.get(exercise.category)
            if evaluator:
                result, score, errors = evaluator.evaluate(exercise, output)
            else:
                # Default evaluation
                result, score, errors = self._default_evaluate(exercise, output)

            execution_time = time.time() - start_time

            attempt = Attempt(
                exercise_id=exercise_id,
                output=output,
                execution_time=execution_time,
                result=result,
                score=score,
                errors=errors,
                hints_used=hints_count,
            )

        except Exception as e:
            execution_time = time.time() - start_time
            attempt = Attempt(
                exercise_id=exercise_id,
                output=None,
                execution_time=execution_time,
                result=ExerciseResult.ERROR,
                score=0.0,
                errors=[str(e)],
                hints_used=hints_count,
            )

        self.attempts.append(attempt)
        return attempt

    def _default_evaluate(self, exercise: Exercise, output: Any) -> Tuple[ExerciseResult, float, List[str]]:
        """Default evaluation if no evaluator registered."""
        if output == exercise.expected_output:
            return ExerciseResult.PASSED, 1.0, []
        elif output is None:
            return ExerciseResult.FAILED, 0.0, ["No output"]
        else:
            # Partial credit for similar output
            similarity = self._calculate_similarity(output, exercise.expected_output)
            if similarity > 0.7:
                return ExerciseResult.PARTIAL, similarity, ["Output partially correct"]
            else:
                return ExerciseResult.FAILED, similarity, ["Output incorrect"]

    def _calculate_similarity(self, a: Any, b: Any) -> float:
        """Calculate similarity between two outputs."""
        if a == b:
            return 1.0
        if isinstance(a, str) and isinstance(b, str):
            # Simple string similarity
            words_a = set(a.lower().split())
            words_b = set(b.lower().split())
            if not words_a or not words_b:
                return 0.0
            intersection = len(words_a & words_b)
            union = len(words_a | words_b)
            return intersection / union if union > 0 else 0.0
        return 0.0

    def get_level_progress(self, level: DifficultyLevel) -> LevelProgress:
        """Get progress for a specific level."""
        level_exercises = [e for e in self.exercises.values() if e.level == level]
        level_attempts = [a for a in self.attempts if self.exercises[a.exercise_id].level == level]

        passed = sum(1 for a in level_attempts if a.result == ExerciseResult.PASSED)
        failed = sum(1 for a in level_attempts if a.result == ExerciseResult.FAILED)
        partial = sum(1 for a in level_attempts if a.result == ExerciseResult.PARTIAL)

        scores = [a.score for a in level_attempts]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        total_time = sum(a.execution_time for a in level_attempts)

        progress = LevelProgress(
            level=level,
            total_exercises=len(level_exercises),
            passed=passed,
            failed=failed,
            partial=partial,
            average_score=avg_score,
            time_taken=total_time,
            completed=passed == len(level_exercises),
        )

        self.level_progress[level.value] = progress
        return progress

    def can_advance_to_next_level(self, current_level: DifficultyLevel) -> bool:
        """Check if agent can advance to next level."""
        progress = self.get_level_progress(current_level)
        # Require 80% pass rate and average score > 0.7
        pass_rate = progress.passed / progress.total_exercises if progress.total_exercises > 0 else 0
        return pass_rate >= 0.8 and progress.average_score >= 0.7

    def get_summary(self) -> Dict[str, Any]:
        """Get overall summary of agent performance."""
        total_attempts = len(self.attempts)
        passed = sum(1 for a in self.attempts if a.result == ExerciseResult.PASSED)
        failed = sum(1 for a in self.attempts if a.result == ExerciseResult.FAILED)
        partial = sum(1 for a in self.attempts if a.result == ExerciseResult.PARTIAL)

        scores = [a.score for a in self.attempts]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        level_summaries = {}
        for level in DifficultyLevel:
            progress = self.get_level_progress(level)
            level_summaries[level.value] = {
                "passed": progress.passed,
                "total": progress.total_exercises,
                "average_score": progress.average_score,
                "completed": progress.completed,
            }

        return {
            "total_attempts": total_attempts,
            "passed": passed,
            "failed": failed,
            "partial": partial,
            "average_score": avg_score,
            "pass_rate": passed / total_attempts if total_attempts > 0 else 0,
            "level_progress": level_summaries,
        }


def load_exercises_from_file(filepath: str) -> List[Exercise]:
    """Load exercises from a JSON file."""
    with open(filepath, "r") as f:
        data = json.load(f)

    exercises = []
    for ex_data in data:
        exercise = Exercise(
            id=ex_data["id"],
            level=DifficultyLevel(ex_data["level"]),
            category=ExerciseCategory(ex_data["category"]),
            title=ex_data["title"],
            description=ex_data["description"],
            input_data=ex_data["input_data"],
            expected_output=ex_data["expected_output"],
            time_limit=ex_data.get("time_limit", 60),
            hints=ex_data.get("hints", []),
            metadata=ex_data.get("metadata", {}),
        )
        exercises.append(exercise)

    return exercises
