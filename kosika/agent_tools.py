"""Bridge tools for Kosika to access agent frameworks.

This module provides tool implementations that Kosika can call to access
the skills, algorithms, knowledge, and learning frameworks.
"""

from .algorithms import SearchProblem, SearchStrategy, State, search
from .knowledge_graph import Entity, EntityType, get_knowledge_base
from .skills_framework import get_skill_registry


def execute_skill(skill_id: str, input_data: dict) -> dict:
    """Execute a skill from the registry."""
    registry = get_skill_registry()
    skill = registry.get(skill_id)

    if not skill:
        return {"error": f"Skill not found: {skill_id}"}

    try:
        result = skill.execute(input_data)
        return {"success": True, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


def run_search(initial: str, goal: str, strategy: str = "bfs") -> dict:
    """Run a search algorithm."""
    class SimpleSearchProblem(SearchProblem):
        def __init__(self, initial, goal):
            self.initial = initial
            self.goal = goal

        def get_initial_state(self):
            return State(data=self.initial, cost=0)

        def is_goal(self, state):
            return state.data == self.goal

        def get_actions(self, state):
            # Simple: can move to any letter
            return ["A", "B", "C", "D", "E"]

        def get_successor(self, state, action):
            return State(data=action, cost=state.cost + 1, parent=state, action=action)

        def get_cost(self, state, action, next_state):
            return 1

    problem = SimpleSearchProblem(initial, goal)

    strategy_map = {
        "bfs": SearchStrategy.BFS,
        "dfs": SearchStrategy.DFS,
        "a_star": SearchStrategy.A_STAR,
        "greedy": SearchStrategy.GREEDY,
    }

    try:
        result = search(problem, strategy_map.get(strategy, SearchStrategy.BFS))
        if result:
            path = [state.data for state, _ in result]
            return {"success": True, "path": path, "length": len(path)}
        else:
            return {"success": False, "error": "No path found"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def query_graph(entity_id: str, relation_type: str = "related_to") -> dict:
    """Query the knowledge graph."""
    kb = get_knowledge_base()
    graph = kb.get_active_graph()

    if not graph:
        # Create a default graph
        graph = kb.create_graph("default")

        # Add some test entities
        graph.add_entity(Entity(
            id="ai",
            name="Artificial Intelligence",
            entity_type=EntityType.CONCEPT,
            description="AI field"
        ))
        graph.add_entity(Entity(
            id="python",
            name="Python",
            entity_type=EntityType.CONCEPT,
            description="Programming language"
        ))
        graph.add_entity(Entity(
            id="kosika",
            name="Kosika",
            entity_type=EntityType.CONCEPT,
            description="AI programming language"
        ))

    try:
        entity = graph.get_entity(entity_id)
        if not entity:
            return {"success": False, "error": f"Entity not found: {entity_id}"}

        # Get related entities
        related = graph.get_relationships(entity_id, direction="outgoing")
        related_entities = [rel.target for rel in related]

        # Find path
        path = graph.find_path(entity_id, "ai")

        return {
            "success": True,
            "entity": entity.name,
            "related_entities": related_entities,
            "path": path if path else []
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def add_agent_experience(agent_id: str, context: dict, action: str, result: dict, reward: float) -> dict:
    """Add experience to a learning agent."""
    try:
        from .learning import LearningAgent

        # Get or create agent
        agent = LearningAgent(agent_id)
        agent.add_experience(context, action, result, reward)

        # Learn patterns
        adaptation = agent.adapt()

        return {
            "success": True,
            "patterns_learned": adaptation["patterns_learned"],
            "experience_count": len(agent.experience_buffer.experiences)
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_agent_performance(agent_id: str) -> dict:
    """Get agent performance summary."""
    try:
        from .learning import LearningAgent

        agent = LearningAgent(agent_id)
        summary = agent.get_performance_summary()

        return {
            "success": True,
            "performance": summary
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
