"""
Examples demonstrating smarter agents using Kosika frameworks.

These examples show how to make agents more powerful through:
- Algorithmic reasoning (not just model calls)
- Knowledge graphs for structured information
- Self-learning from experience
- Skill composition and reuse
- Multi-agent coordination
"""

from kosika.skills_framework import (
    Skill, SkillKind, SkillParameter, SkillOutput, SkillExample,
    get_skill_registry, create_skill
)
from kosika.mcp_generator import (
    MCPServer, MCPTool, MCPToolAccess, MCPToolKind,
    get_mcp_generator, create_mcp_tool
)
from kosika.algorithms import (
    SearchProblem, State, SearchStrategy, PlanningProblem,
    ConstraintSolver, ReasoningEngine, hierarchical_planning
)
from kosika.knowledge_graph import (
    Entity, EntityType, Relationship, RelationType,
    KnowledgeGraph, KnowledgeBase, get_knowledge_base
)
from kosika.learning import (
    LearningAgent, Experience, PatternKind,
    Strategy, StrategySelector
)
from kosika.orchestration import (
    Agent, AgentRole, Task, TaskStatus, Workflow,
    AgentTeam, WorkflowOrchestrator
)


# ============================================================================
# Example 1: Creating and Using Skills
# ============================================================================

def example_skills():
    """Example: Creating reusable skills for agents."""

    registry = get_skill_registry()

    # Create a search skill
    search_skill = create_skill(
        id="web_search",
        name="Web Search",
        kind=SkillKind.TOOL,
        description="Search the web for information",
        parameters=[
            {"name": "query", "type": "Text", "required": True, "description": "Search query"},
            {"name": "max_results", "type": "Number", "required": False, "default": 10, "description": "Max results"},
        ],
        output_type="Text[]",
        implementation=lambda query, max_results=10: [f"Result {i+1} for {query}" for i in range(max_results)]
    )

    registry.register(search_skill)

    # Create a reasoning skill
    reasoning_skill = create_skill(
        id="logical_reasoning",
        name="Logical Reasoning",
        kind=SkillKind.REASONING,
        description="Apply logical reasoning to a problem",
        parameters=[
            {"name": "premises", "type": "Text[]", "required": True, "description": "Premises"},
            {"name": "conclusion", "type": "Text", "required": True, "description": "Conclusion to test"},
        ],
        output_type="Bool",
        implementation=lambda premises, conclusion: len(premises) > 0  # Simplified
    )

    registry.register(reasoning_skill)

    # Use the skill
    results = search_skill.execute({"query": "AI programming languages", "max_results": 5})
    print(f"Search results: {results}")

    print("\n✓ Skills framework example complete")


# ============================================================================
# Example 2: MCP Server Generation
# ============================================================================

def example_mcp_server():
    """Example: Declaring and generating an MCP server."""

    generator = get_mcp_generator()

    # Create an MCP server for document analysis
    server = generator.create_server(
        id="document_analyzer",
        name="Document Analyzer",
        description="MCP server for analyzing documents",
        version="1.0.0"
    )

    # Add tools
    search_tool = create_mcp_tool(
        name="search_documents",
        description="Search through documents",
        access=MCPToolAccess.OBSERVE,
        parameters=[
            {"name": "query", "type": "string", "required": True, "description": "Search query"},
            {"name": "limit", "type": "number", "required": False, "default": 10},
        ]
    )

    extract_tool = create_mcp_tool(
        name="extract_entities",
        description="Extract entities from document",
        access=MCPToolAccess.OBSERVE,
        parameters=[
            {"name": "document_id", "type": "string", "required": True},
            {"name": "entity_types", "type": "array", "required": False},
        ]
    )

    server.add_tool(search_tool)
    server.add_tool(extract_tool)

    # Generate Python code
    python_code = generator.generate_python("document_analyzer")
    print("Generated Python MCP Server:")
    print(python_code[:500] + "...")

    # Generate manifest
    manifest = generator.generate_manifest("document_analyzer")
    print("\nGenerated MCP Manifest:")
    print(manifest[:300] + "...")

    print("\n✓ MCP server generation example complete")


# ============================================================================
# Example 3: Algorithmic Planning
# ============================================================================

class NavigationProblem(SearchProblem):
    """Example: Pathfinding problem using search algorithms."""

    def __init__(self, start: str, goal: str, graph: Dict[str, List[Tuple[str, float]]]):
        self.start = start
        self.goal = goal
        self.graph = graph

    def get_initial_state(self) -> State:
        return State(data=self.start, cost=0.0)

    def is_goal(self, state: State) -> bool:
        return state.data == self.goal

    def get_actions(self, state: State) -> List[str]:
        return [neighbor for neighbor, _ in self.graph.get(state.data, [])]

    def get_successor(self, state: State, action: str) -> State:
        # Find cost to neighbor
        for neighbor, cost in self.graph.get(state.data, []):
            if neighbor == action:
                return State(data=action, cost=state.cost + cost, parent=state, action=action)
        return State(data=action, cost=state.cost + 1.0, parent=state, action=action)

    def get_cost(self, state: State, action: str, next_state: State) -> float:
        return 1.0  # Simplified

    def get_heuristic(self, state: State) -> float:
        # Simple heuristic: distance (assume uniform for now)
        return 0 if state.data == self.goal else 1


def example_search():
    """Example: Using search algorithms for planning."""

    # Define a simple graph
    graph = {
        "A": [("B", 1), ("C", 4)],
        "B": [("C", 2), ("D", 5)],
        "C": [("D", 1)],
        "D": []
    }

    problem = NavigationProblem("A", "D", graph)

    # Use A* search
    from kosika.algorithms import search
    result = search(problem, strategy=SearchStrategy.A_STAR)

    if result:
        path = [state.data for state, _ in result]
        print(f"Found path: {' -> '.join(path)}")
    else:
        print("No path found")

    print("\n✓ Search algorithm example complete")


# ============================================================================
# Example 4: Knowledge Graph Reasoning
# ============================================================================

def example_knowledge_graph():
    """Example: Using knowledge graphs for reasoning."""

    kb = get_knowledge_base()
    graph = kb.create_graph("demo")

    # Add entities
    python = Entity(id="python", name="Python", entity_type=EntityType.CONCEPT,
                   description="Programming language")
    kosika = Entity(id="kosika", name="Kosika", entity_type=EntityType.CONCEPT,
                    description="AI programming language")
    ai = Entity(id="ai", name="Artificial Intelligence", entity_type=EntityType.CONCEPT,
                description="AI field")

    graph.add_entity(python)
    graph.add_entity(kosika)
    graph.add_entity(ai)

    # Add relationships
    graph.add_relationship(Relationship(
        id="r1", source="kosika", target="ai",
        relation_type=RelationType.RELATED_TO, weight=0.9
    ))
    graph.add_relationship(Relationship(
        id="r2", source="python", target="ai",
        relation_type=RelationType.RELATED_TO, weight=0.8
    ))
    graph.add_relationship(Relationship(
        id="r3", source="kosika", target="python",
        relation_type=RelationType.SIMILAR_TO, weight=0.7
    ))

    # Query related entities
    related = graph.get_relationships("kosika", direction="outgoing")
    print(f"Entities related to Kosika: {[rel.target for rel in related]}")

    # Find path
    path = graph.find_path("python", "kosika")
    print(f"Path from Python to Kosika: {path}")

    # Get statistics
    stats = graph.get_statistics()
    print(f"Knowledge graph stats: {stats}")

    print("\n✓ Knowledge graph example complete")


# ============================================================================
# Example 5: Self-Learning Agent
# ============================================================================

def example_learning_agent():
    """Example: Agent that learns from experience."""

    agent = LearningAgent("smart_agent")

    # Add experiences
    agent.add_experience(
        context={"task": "code_review", "language": "python"},
        action="use_linter",
        result={"bugs_found": 3, "time_taken": 5},
        reward=0.8
    )

    agent.add_experience(
        context={"task": "code_review", "language": "python"},
        action="manual_review",
        result={"bugs_found": 5, "time_taken": 15},
        reward=0.6
    )

    agent.add_experience(
        context={"task": "code_review", "language": "python"},
        action="use_linter",
        result={"bugs_found": 2, "time_taken": 4},
        reward=0.7
    )

    # Learn patterns
    adaptation = agent.adapt()
    print(f"Agent learned {adaptation['patterns_learned']} patterns")
    print(f"Patterns: {adaptation['patterns']}")

    # Update performance
    agent.update_performance("efficiency", 0.85)
    agent.update_performance("accuracy", 0.92)

    # Get performance summary
    summary = agent.get_performance_summary()
    print(f"Performance summary: {summary}")

    print("\n✓ Self-learning agent example complete")


# ============================================================================
# Example 6: Multi-Agent Orchestration
# ============================================================================

def example_orchestration():
    """Example: Coordinating multiple agents."""

    orchestrator = WorkflowOrchestrator()

    # Create a team
    team = AgentTeam("dev_team")
    team.add_agent(Agent(
        id="agent1", name="Coder", role=AgentRole.SPECIALIST,
        capabilities=["write_code", "refactor"]
    ))
    team.add_agent(Agent(
        id="agent2", name="Reviewer", role=AgentRole.REVIEWER,
        capabilities=["review", "test"]
    ))
    team.add_agent(Agent(
        id="agent3", name="Orchestrator", role=AgentRole.COORDINATOR,
        capabilities=["plan", "coordinate"]
    ))

    orchestrator.create_team(team)

    # Create a workflow
    workflow = Workflow(
        id="build_feature",
        name="Build Feature",
        description="Build a new feature with code and tests",
        tasks=[
            Task(
                id="task1", name="Plan", task_type="planning",
                description="Plan the feature",
                required_capabilities=["plan"],
                input_data={"feature": "authentication"}
            ),
            Task(
                id="task2", name="Code", task_type="coding",
                description="Write the code",
                required_capabilities=["write_code"],
                input_data={},
                dependencies=["task1"]
            ),
            Task(
                id="task3", name="Review", task_type="review",
                description="Review the code",
                required_capabilities=["review"],
                input_data={},
                dependencies=["task2"]
            ),
            Task(
                id="task4", name="Test", task_type="testing",
                description="Test the code",
                required_capabilities=["test"],
                input_data={},
                dependencies=["task2"]
            ),
        ]
    )

    orchestrator.create_workflow(workflow)

    # Execute
    result = orchestrator.execute_workflow("build_feature", "dev_team")
    print(f"Workflow execution result: {result}")

    print("\n✓ Multi-agent orchestration example complete")


# ============================================================================
# Example 7: Constraint Solving
# ============================================================================

def example_constraint_solving():
    """Example: Solving constraint satisfaction problems."""

    solver = ConstraintSolver(
        variables=["A", "B", "C"],
        domains={
            "A": [1, 2, 3],
            "B": [1, 2, 3],
            "C": [1, 2, 3]
        }
    )

    # Add constraints: all variables must be different
    solver.add_constraint(lambda assignment: len(set(assignment.values())) == len(assignment))

    # Add constraint: A < B < C
    solver.add_constraint(lambda assignment: assignment.get("A", 0) < assignment.get("B", 0))
    solver.add_constraint(lambda assignment: assignment.get("B", 0) < assignment.get("C", 0))

    solution = solver.solve(strategy="backtracking")
    print(f"Constraint solution: {solution}")

    print("\n✓ Constraint solving example complete")


# ============================================================================
# Example 8: Logical Reasoning
# ============================================================================

def example_reasoning():
    """Example: Logical reasoning engine."""

    engine = ReasoningEngine()

    # Add facts
    engine.add_fact("Kosika is a programming language")
    engine.add_fact("Kosika is designed for AI")
    engine.add_fact("Python is a programming language")

    # Add rules
    engine.add_rule(
        premises=["X is a programming language", "X is designed for AI"],
        conclusion="X is an AI programming language"
    )

    # Forward chaining
    can_derive = engine.forward_chain("Kosika is an AI programming language")
    print(f"Can derive 'Kosika is an AI programming language': {can_derive}")

    # Generate explanation
    explanation = engine.explain("Kosika is an AI programming language")
    print(f"Explanation: {explanation}")

    print("\n✓ Logical reasoning example complete")


# ============================================================================
# Example 9: Strategy Selection
# ============================================================================

def example_strategy_selection():
    """Example: Selecting strategies based on context."""

    selector = StrategySelector()

    # Define strategies
    strategy1 = Strategy(
        id="quick_search",
        name="Quick Search",
        description="Fast but less accurate search",
        conditions=[
            lambda ctx: ctx.get("time_constraint") == "tight",
            lambda ctx: ctx.get("accuracy_requirement") == "low"
        ],
        implementation=lambda ctx: "used quick search"
    )

    strategy2 = Strategy(
        id="thorough_search",
        name="Thorough Search",
        description="Slower but more accurate search",
        conditions=[
            lambda ctx: ctx.get("accuracy_requirement") == "high"
        ],
        implementation=lambda ctx: "used thorough search"
    )

    selector.add_strategy(strategy1)
    selector.add_strategy(strategy2)

    # Select strategy for context
    context1 = {"time_constraint": "tight", "accuracy_requirement": "low"}
    selected1 = selector.select_strategy(context1)
    print(f"Selected for context1: {selected1.name if selected1 else 'None'}")

    context2 = {"accuracy_requirement": "high"}
    selected2 = selector.select_strategy(context2)
    print(f"Selected for context2: {selected2.name if selected2 else 'None'}")

    # Update performance
    selector.update_performance("quick_search", success=True, metrics={"time": 2.5})
    selector.update_performance("thorough_search", success=True, metrics={"time": 8.3})

    print("\n✓ Strategy selection example complete")


# ============================================================================
# Main: Run All Examples
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Kosika Smart Agent Examples")
    print("=" * 60)

    print("\n1. Skills Framework")
    print("-" * 60)
    example_skills()

    print("\n2. MCP Server Generation")
    print("-" * 60)
    example_mcp_server()

    print("\n3. Algorithmic Planning")
    print("-" * 60)
    example_search()

    print("\n4. Knowledge Graph")
    print("-" * 60)
    example_knowledge_graph()

    print("\n5. Self-Learning Agent")
    print("-" * 60)
    example_learning_agent()

    print("\n6. Multi-Agent Orchestration")
    print("-" * 60)
    example_orchestration()

    print("\n7. Constraint Solving")
    print("-" * 60)
    example_constraint_solving()

    print("\n8. Logical Reasoning")
    print("-" * 60)
    example_reasoning()

    print("\n9. Strategy Selection")
    print("-" * 60)
    example_strategy_selection()

    print("\n" + "=" * 60)
    print("All examples completed successfully!")
    print("=" * 60)
