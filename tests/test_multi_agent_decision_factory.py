"""Tests for canvas DAG -> SupervisorDecision conversion."""

from __future__ import annotations

import pytest

from app.multi_agent.decision_factory import DecisionFactoryError, from_dag
from app.multi_agent.models import AgentRole


def _sample_dag() -> dict:
    return {
        "nodes": [
            {"id": "task_1", "role": "research", "description": "Search"},
            {"id": "task_2", "role": "writer", "description": "Write"},
            {"id": "task_3", "role": "reviewer", "description": "Review"},
        ],
        "edges": [
            {"id": "e1", "source": "task_1", "target": "task_2"},
            {"id": "e2", "source": "task_2", "target": "task_3"},
        ],
    }


def test_from_dag_linear_chain() -> None:
    decision = from_dag(_sample_dag())
    assert len(decision.subtasks) == 3
    assert decision.subtasks[0].id == "task_1"
    assert decision.subtasks[0].depends_on == ()
    assert decision.subtasks[1].depends_on == ("task_1",)
    assert decision.subtasks[2].depends_on == ("task_2",)


def test_from_dag_roles_mapped() -> None:
    decision = from_dag(_sample_dag())
    assert decision.subtasks[0].agent_role == AgentRole.RESEARCH
    assert decision.subtasks[1].agent_role == AgentRole.WRITER
    assert decision.subtasks[2].agent_role == AgentRole.REVIEWER


def test_from_dag_unknown_role_becomes_custom() -> None:
    dag = {
        "nodes": [{"id": "t1", "role": "analyst", "description": "Analyze"}],
        "edges": [],
    }
    decision = from_dag(dag)
    assert decision.subtasks[0].agent_role == AgentRole.CUSTOM


def test_from_dag_parallel_tasks() -> None:
    dag = {
        "nodes": [
            {"id": "a", "role": "research", "description": "A"},
            {"id": "b", "role": "research", "description": "B"},
            {"id": "c", "role": "writer", "description": "C"},
        ],
        "edges": [
            {"id": "e1", "source": "a", "target": "c"},
            {"id": "e2", "source": "b", "target": "c"},
        ],
    }
    decision = from_dag(dag)
    c = next(s for s in decision.subtasks if s.id == "c")
    assert set(c.depends_on) == {"a", "b"}


def test_from_dag_rejects_cycle() -> None:
    dag = {
        "nodes": [
            {"id": "a", "role": "research", "description": "A"},
            {"id": "b", "role": "writer", "description": "B"},
        ],
        "edges": [
            {"id": "e1", "source": "a", "target": "b"},
            {"id": "e2", "source": "b", "target": "a"},
        ],
    }
    with pytest.raises(DecisionFactoryError, match="cycle"):
        from_dag(dag)


def test_from_dag_rejects_empty_nodes() -> None:
    with pytest.raises(DecisionFactoryError, match="non-empty"):
        from_dag({"nodes": [], "edges": []})


def test_from_dag_rejects_missing_node() -> None:
    dag = {
        "nodes": [{"id": "a", "role": "research", "description": "A"}],
        "edges": [{"id": "e1", "source": "a", "target": "missing"}],
    }
    with pytest.raises(DecisionFactoryError, match="unknown node"):
        from_dag(dag)


def test_from_dag_rejects_too_many_nodes() -> None:
    dag = {
        "nodes": [
            {"id": f"t{i}", "role": "research", "description": "D"} for i in range(25)
        ],
        "edges": [],
    }
    with pytest.raises(DecisionFactoryError, match="maximum"):
        from_dag(dag)


def test_from_dag_rejects_non_dict() -> None:
    with pytest.raises(DecisionFactoryError, match="JSON object"):
        from_dag("not a dict")  # type: ignore[arg-type]


def test_from_dag_preserves_description() -> None:
    dag = {
        "nodes": [
            {
                "id": "t1",
                "role": "research",
                "description": "Find latest AI trends",
                "config": {"model": "gpt-4o", "max_steps": 5},
            }
        ],
        "edges": [],
    }
    decision = from_dag(dag)
    assert decision.subtasks[0].description == "Find latest AI trends"
    assert decision.reasoning == "Canvas configuration"
