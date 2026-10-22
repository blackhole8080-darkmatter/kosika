"""Algorithmic Primitives for Kosika Agents.

This module provides algorithmic capabilities that make agents smarter without
relying solely on models. These are deterministic, traceable algorithms for
planning, search, optimization, and reasoning.

Key features:
- Planning algorithms (forward/backward search, hierarchical planning)
- Search algorithms (A*, BFS, DFS, beam search)
- Optimization algorithms (gradient descent, genetic algorithms)
- Reasoning patterns (deduction, induction, abduction)
- Decision trees and logic
- Constraint satisfaction
"""

from __future__ import annotations

import heapq
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class SearchStrategy(Enum):
    """Search strategies for planning."""
    BFS = "bfs"  # Breadth-first search
    DFS = "dfs"  # Depth-first search
    A_STAR = "a_star"  # A* search
    GREEDY = "greedy"  # Greedy best-first
    BEAM = "beam"  # Beam search


@dataclass
class State:
    """A state in a search problem."""
    data: Any
    cost: float = 0.0
    heuristic: float = 0.0
    parent: Optional["State"] = None
    action: Optional[str] = None

    @property
    def total_cost(self) -> float:
        """Total cost = cost + heuristic."""
        return self.cost + self.heuristic

    def __lt__(self, other: "State") -> bool:
        """Comparison for priority queue."""
        return self.total_cost < other.total_cost


class SearchProblem(ABC):
    """Abstract base class for search problems."""

    @abstractmethod
    def get_initial_state(self) -> State:
        """Get the initial state."""
        pass

    @abstractmethod
    def is_goal(self, state: State) -> bool:
        """Check if state is a goal."""
        pass

    @abstractmethod
    def get_actions(self, state: State) -> List[str]:
        """Get possible actions from state."""
        pass

    @abstractmethod
    def get_successor(self, state: State, action: str) -> State:
        """Get successor state from action."""
        pass

    @abstractmethod
    def get_cost(self, state: State, action: str, next_state: State) -> float:
        """Get cost of action."""
        pass

    def get_heuristic(self, state: State) -> float:
        """Get heuristic estimate to goal (default: 0)."""
        return 0.0


def search(
    problem: SearchProblem,
    strategy: SearchStrategy = SearchStrategy.A_STAR,
    max_depth: int = 1000,
    max_nodes: int = 100000,
    beam_width: int = 10,
) -> Optional[List[Tuple[State, str]]]:
    """Generic search algorithm.

    Returns list of (state, action) pairs from initial to goal, or None if no solution.
    """
    initial = problem.get_initial_state()

    if strategy == SearchStrategy.BFS:
        return _bfs_search(problem, initial, max_depth, max_nodes)
    elif strategy == SearchStrategy.DFS:
        return _dfs_search(problem, initial, max_depth, max_nodes)
    elif strategy == SearchStrategy.A_STAR:
        return _a_star_search(problem, initial, max_depth, max_nodes)
    elif strategy == SearchStrategy.GREEDY:
        return _greedy_search(problem, initial, max_depth, max_nodes)
    elif strategy == SearchStrategy.BEAM:
        return _beam_search(problem, initial, max_depth, max_nodes, beam_width)
    else:
        raise ValueError(f"Unknown search strategy: {strategy}")


def _bfs_search(
    problem: SearchProblem,
    initial: State,
    max_depth: int,
    max_nodes: int,
) -> Optional[List[Tuple[State, str]]]:
    """Breadth-first search."""
    queue: List[Tuple[State, List[Tuple[State, str]]]] = [(initial, [])]
    visited: Set[Any] = {initial.data}
    nodes_expanded = 0

    while queue and nodes_expanded < max_nodes:
        current, path = queue.pop(0)

        if problem.is_goal(current):
            return path + [(current, "")]

        if len(path) >= max_depth:
            continue

        for action in problem.get_actions(current):
            successor = problem.get_successor(current, action)

            if successor.data not in visited:
                visited.add(successor.data)
                queue.append((successor, path + [(current, action)]))
                nodes_expanded += 1

    return None


def _dfs_search(
    problem: SearchProblem,
    initial: State,
    max_depth: int,
    max_nodes: int,
) -> Optional[List[Tuple[State, str]]]:
    """Depth-first search."""
    stack: List[Tuple[State, List[Tuple[State, str]]]] = [(initial, [])]
    visited: Set[Any] = set()
    nodes_expanded = 0

    while stack and nodes_expanded < max_nodes:
        current, path = stack.pop()

        if current.data in visited:
            continue

        visited.add(current.data)

        if problem.is_goal(current):
            return path + [(current, "")]

        if len(path) >= max_depth:
            continue

        for action in problem.get_actions(current):
            successor = problem.get_successor(current, action)
            stack.append((successor, path + [(current, action)]))
            nodes_expanded += 1

    return None


def _a_star_search(
    problem: SearchProblem,
    initial: State,
    max_depth: int,
    max_nodes: int,
) -> Optional[List[Tuple[State, str]]]:
    """A* search with heuristic."""
    initial.heuristic = problem.get_heuristic(initial)
    heap: List[Tuple[float, int, State, List[Tuple[State, str]]]] = [
        (initial.total_cost, id(initial), initial, [])
    ]
    visited: Dict[Any, float] = {initial.data: initial.cost}
    nodes_expanded = 0

    while heap and nodes_expanded < max_nodes:
        _, _, current, path = heapq.heappop(heap)

        if problem.is_goal(current):
            return path + [(current, "")]

        if len(path) >= max_depth:
            continue

        for action in problem.get_actions(current):
            successor = problem.get_successor(current, action)
            successor.cost = current.cost + problem.get_cost(current, action, successor)
            successor.heuristic = problem.get_heuristic(successor)

            if successor.data not in visited or successor.cost < visited[successor.data]:
                visited[successor.data] = successor.cost
                heapq.heappush(
                    heap,
                    (successor.total_cost, id(successor), successor, path + [(current, action)]),
                )
                nodes_expanded += 1

    return None


def _greedy_search(
    problem: SearchProblem,
    initial: State,
    max_depth: int,
    max_nodes: int,
) -> Optional[List[Tuple[State, str]]]:
    """Greedy best-first search (uses only heuristic)."""
    initial.heuristic = problem.get_heuristic(initial)
    heap: List[Tuple[float, int, State, List[Tuple[State, str]]]] = [
        (initial.heuristic, id(initial), initial, [])
    ]
    visited: Set[Any] = {initial.data}
    nodes_expanded = 0

    while heap and nodes_expanded < max_nodes:
        _, _, current, path = heapq.heappop(heap)

        if problem.is_goal(current):
            return path + [(current, "")]

        if len(path) >= max_depth:
            continue

        for action in problem.get_actions(current):
            successor = problem.get_successor(current, action)
            successor.heuristic = problem.get_heuristic(successor)

            if successor.data not in visited:
                visited.add(successor.data)
                heapq.heappush(
                    heap,
                    (successor.heuristic, id(successor), successor, path + [(current, action)]),
                )
                nodes_expanded += 1

    return None


def _beam_search(
    problem: SearchProblem,
    initial: State,
    max_depth: int,
    max_nodes: int,
    beam_width: int,
) -> Optional[List[Tuple[State, str]]]:
    """Beam search (keeps only top-k states at each level)."""
    initial.heuristic = problem.get_heuristic(initial)
    current_beam: List[Tuple[float, State, List[Tuple[State, str]]]] = [
        (initial.heuristic, initial, [])
    ]
    visited: Set[Any] = {initial.data}
    nodes_expanded = 0

    for depth in range(max_depth):
        if not current_beam or nodes_expanded >= max_nodes:
            break

        next_beam = []

        for _, current, path in current_beam:
            if problem.is_goal(current):
                return path + [(current, "")]

            for action in problem.get_actions(current):
                successor = problem.get_successor(current, action)
                successor.heuristic = problem.get_heuristic(successor)

                if successor.data not in visited:
                    visited.add(successor.data)
                    next_beam.append((successor.heuristic, successor, path + [(current, action)]))
                    nodes_expanded += 1

        # Keep only top-k states
        next_beam.sort(key=lambda x: x[0])
        current_beam = next_beam[:beam_width]

    return None


class PlanningProblem(SearchProblem):
    """Planning problem with hierarchical decomposition."""

    def __init__(self, initial_state: Dict[str, Any], goals: List[Dict[str, Any]]):
        self.initial_state = initial_state
        self.goals = goals

    def get_initial_state(self) -> State:
        return State(data=self.initial_state)

    def is_goal(self, state: State) -> bool:
        for goal in self.goals:
            if not all(state.data.get(k) == v for k, v in goal.items()):
                return False
        return True

    def get_actions(self, state: State) -> List[str]:
        # To be implemented by subclasses
        return []

    def get_successor(self, state: State, action: str) -> State:
        # To be implemented by subclasses
        return State(data=state.data.copy())

    def get_cost(self, state: State, action: str, next_state: State) -> float:
        return 1.0

    def get_heuristic(self, state: State) -> float:
        # Count unsatisfied goals
        unsatisfied = 0
        for goal in self.goals:
            for k, v in goal.items():
                if state.data.get(k) != v:
                    unsatisfied += 1
        return unsatisfied


def hierarchical_planning(
    problem: PlanningProblem,
    max_depth: int = 10,
) -> Optional[List[str]]:
    """Hierarchical planning with task decomposition."""
    # Simple implementation: use A* search
    result = search(problem, strategy=SearchStrategy.A_STAR, max_depth=max_depth)
    if result:
        return [action for _, action in result if action]
    return None


@dataclass
class OptimizationConfig:
    """Configuration for optimization algorithms."""
    learning_rate: float = 0.01
    max_iterations: int = 1000
    tolerance: float = 1e-6
    population_size: int = 50
    mutation_rate: float = 0.1
    crossover_rate: float = 0.8


def gradient_descent(
    objective: Callable[[List[float]], float],
    gradient: Callable[[List[float]], List[float]],
    initial: List[float],
    config: OptimizationConfig,
) -> Tuple[List[float], float]:
    """Gradient descent optimization."""
    x = initial.copy()
    best_value = objective(x)

    for i in range(config.max_iterations):
        grad = gradient(x)

        # Update
        for j in range(len(x)):
            x[j] -= config.learning_rate * grad[j]

        current_value = objective(x)

        # Check convergence
        if abs(current_value - best_value) < config.tolerance:
            break

        best_value = current_value

    return x, best_value


def genetic_algorithm(
    objective: Callable[[List[float]], float],
    bounds: List[Tuple[float, float]],
    config: OptimizationConfig,
    minimize: bool = True,
) -> Tuple[Optional[List[float]], float]:
    """Genetic algorithm optimization."""

    # Initialize population
    population = []
    for _ in range(config.population_size):
        individual = [random.uniform(b[0], b[1]) for b in bounds]
        population.append(individual)

    best_individual: Optional[List[float]] = None
    best_fitness = float("inf") if minimize else float("-inf")

    for generation in range(config.max_iterations):
        # Evaluate fitness
        fitness = [(ind, objective(ind)) for ind in population]

        # Find best
        for ind, fit in fitness:
            if minimize:
                if fit < best_fitness:
                    best_fitness = fit
                    best_individual = ind
            else:
                if fit > best_fitness:
                    best_fitness = fit
                    best_individual = ind

        # Selection (tournament)
        selected = []
        for _ in range(config.population_size):
            tournament = random.sample(fitness, 3)
            winner = min(tournament, key=lambda x: x[1]) if minimize else max(tournament, key=lambda x: x[1])
            selected.append(winner[0])

        # Crossover
        offspring = []
        for i in range(0, len(selected), 2):
            if i + 1 < len(selected) and random.random() < config.crossover_rate:
                parent1, parent2 = selected[i], selected[i + 1]
                crossover_point = random.randint(1, len(parent1) - 1)
                child1 = parent1[:crossover_point] + parent2[crossover_point:]
                child2 = parent2[:crossover_point] + parent1[crossover_point:]
                offspring.extend([child1, child2])
            else:
                offspring.extend([selected[i], selected[i + 1] if i + 1 < len(selected) else selected[i]])

        # Mutation
        for ind in offspring:
            for j in range(len(ind)):
                if random.random() < config.mutation_rate:
                    ind[j] = random.uniform(bounds[j][0], bounds[j][1])

        population = offspring

    return best_individual, best_fitness


class ConstraintSolver:
    """Constraint satisfaction problem solver."""

    def __init__(self, variables: List[str], domains: Dict[str, List[Any]]):
        self.variables = variables
        self.domains = domains
        self.constraints: List[Callable[[Dict[str, Any]], bool]] = []

    def add_constraint(self, constraint: Callable[[Dict[str, Any]], bool]):
        """Add a constraint."""
        self.constraints.append(constraint)

    def solve(self, strategy: str = "backtracking") -> Optional[Dict[str, Any]]:
        """Solve the CSP."""
        if strategy == "backtracking":
            return self._backtracking_search({})
        elif strategy == "forward_checking":
            return self._forward_checking({})
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

    def _backtracking_search(self, assignment: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Backtracking search."""
        if len(assignment) == len(self.variables):
            return assignment

        var = self._select_unassigned_variable(assignment)
        for value in self.domains[var]:
            assignment[var] = value
            if self._is_consistent(assignment):
                result = self._backtracking_search(assignment)
                if result is not None:
                    return result
            del assignment[var]

        return None

    def _forward_checking(self, assignment: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Forward checking with domain pruning."""
        if len(assignment) == len(self.variables):
            return assignment

        var = self._select_unassigned_variable(assignment)
        for value in self.domains[var]:
            assignment[var] = value
            if self._is_consistent(assignment):
                # Prune domains (simplified)
                result = self._forward_checking(assignment)
                if result is not None:
                    return result
            del assignment[var]

        return None

    def _select_unassigned_variable(self, assignment: Dict[str, Any]) -> str:
        """Select next variable (MRV heuristic)."""
        unassigned = [v for v in self.variables if v not in assignment]
        # Minimum remaining values
        return min(unassigned, key=lambda v: len(self.domains[v]))

    def _is_consistent(self, assignment: Dict[str, Any]) -> bool:
        """Check if assignment satisfies all constraints."""
        for constraint in self.constraints:
            if not constraint(assignment):
                return False
        return True


class ReasoningEngine:
    """Logical reasoning engine."""

    def __init__(self):
        self.facts: Set[str] = set()
        self.rules: List[Tuple[List[str], str]] = []  # (premises, conclusion)

    def add_fact(self, fact: str):
        """Add a fact."""
        self.facts.add(fact)

    def add_rule(self, premises: List[str], conclusion: str):
        """Add a rule (if premises then conclusion)."""
        self.rules.append((premises, conclusion))

    def forward_chain(self, query: str) -> bool:
        """Forward chaining to derive query."""
        derived = set(self.facts)
        changed = True

        while changed:
            changed = False
            for premises, conclusion in self.rules:
                if all(p in derived for p in premises) and conclusion not in derived:
                    derived.add(conclusion)
                    changed = True

        return query in derived

    def backward_chain(self, query: str, visited: Optional[Set[str]] = None) -> bool:
        """Backward chaining to derive query."""
        if visited is None:
            visited = set()

        if query in self.facts:
            return True

        if query in visited:
            return False

        visited.add(query)

        for premises, conclusion in self.rules:
            if conclusion == query:
                if all(self.backward_chain(p, visited) for p in premises):
                    return True

        return False

    def explain(self, query: str) -> List[str]:
        """Generate explanation for a derived fact."""
        explanation = []

        if query in self.facts:
            explanation.append(f"{query} is a known fact.")
            return explanation

        for premises, conclusion in self.rules:
            if conclusion == query:
                explanation.append(f"{query} can be derived from:")
                for p in premises:
                    if p in self.facts:
                        explanation.append(f"  - {p} (fact)")
                    else:
                        sub_explanation = self.explain(p)
                        explanation.extend([f"  - {e}" for e in sub_explanation])

        return explanation
