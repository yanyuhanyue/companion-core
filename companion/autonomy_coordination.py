"""
Multi-goal coordinator: conflict detection and resolution for concurrent exploration goals.
"""

import json
from typing import Any

from companion.autonomy import GoalConflict, ResourceAllocation
from companion.util import uid

# ============================================================================
# Conflict Detection
# ============================================================================

def detect_conflicts(active_goals: list[dict], now: str) -> list[GoalConflict]:
    """
    Detect conflicts between active goals.

    Args:
        active_goals: List of active exploration goals
        now: Current timestamp

    Returns:
        List of detected conflicts
    """
    conflicts = []

    # 1. Resource conflicts (too many concurrent goals)
    if len(active_goals) > 5:  # Max 5 concurrent goals
        conflicts.append(GoalConflict(
            goal_ids=[g["goal_id"] for g in active_goals],
            conflict_type="resource",
            severity="high",
            resolution_strategy="优先级分配",
            detected_at=now
        ))

    # 2. Prerequisite conflicts
    prereq_conflicts = _detect_prerequisite_conflicts(active_goals)
    conflicts.extend(prereq_conflicts)

    # 3. Exclusive conflicts (mutually exclusive topics)
    exclusive_conflicts = _detect_exclusive_conflicts(active_goals, now)
    conflicts.extend(exclusive_conflicts)

    return conflicts


def _detect_prerequisite_conflicts(goals: list[dict]) -> list[GoalConflict]:
    """Detect prerequisite dependency conflicts."""
    conflicts = []

    # Build dependency graph
    dependencies = {}
    for goal in goals:
        goal_id = goal["goal_id"]
        # In practice, would extract from goal metadata
        prereqs = goal.get("prerequisites", [])
        dependencies[goal_id] = prereqs

    # Detect cycles
    cycles = _find_cycles(dependencies)

    for cycle in cycles:
        conflicts.append(GoalConflict(
            goal_ids=cycle,
            conflict_type="prerequisite",
            severity="high",
            resolution_strategy="拓扑排序",
            detected_at=goal.get("created_at", "")
        ))

    return conflicts


def _detect_exclusive_conflicts(goals: list[dict], now: str) -> list[GoalConflict]:
    """Detect mutually exclusive goal conflicts."""
    conflicts = []

    # Group goals by topic
    topic_groups = {}
    for goal in goals:
        topic = goal.get("topic", "")
        if topic not in topic_groups:
            topic_groups[topic] = []
        topic_groups[topic].append(goal["goal_id"])

    # Check for over-concentration
    for topic, goal_ids in topic_groups.items():
        if len(goal_ids) > 2:  # More than 2 goals on same topic
            conflicts.append(GoalConflict(
                goal_ids=goal_ids,
                conflict_type="exclusive",
                severity="medium",
                resolution_strategy="合并或选择优先",
                detected_at=now
            ))

    return conflicts


def _find_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Find cycles in dependency graph (simplified)."""
    # Simplified cycle detection
    cycles = []

    for node in graph:
        visited = set()
        path = []

        if _has_cycle_dfs(node, graph, visited, path):
            cycles.append(path)

    return cycles


def _has_cycle_dfs(
    node: str,
    graph: dict[str, list[str]],
    visited: set[str],
    path: list[str]
) -> bool:
    """DFS to detect cycles."""
    if node in path:
        return True

    if node in visited:
        return False

    visited.add(node)
    path.append(node)

    for neighbor in graph.get(node, []):
        if _has_cycle_dfs(neighbor, graph, visited, path):
            return True

    path.pop()
    return False


# ============================================================================
# Conflict Resolution
# ============================================================================

def resolve_resource_conflict(goals: list[dict], total_budget: float = 100.0) -> list[ResourceAllocation]:
    """
    Resolve resource conflicts by allocating time budget.

    Args:
        goals: List of goals competing for resources
        total_budget: Total time budget (hours/week)

    Returns:
        List of resource allocations
    """
    # Sort by priority
    sorted_goals = sorted(goals, key=lambda g: g.get("priority", 0), reverse=True)

    allocations = []
    remaining_budget = total_budget

    for goal in sorted_goals:
        goal_id = goal["goal_id"]
        priority = goal.get("priority", 50)

        # Minimum allocation: 10% of total
        min_allocation = total_budget * 0.1

        # Ideal allocation based on estimated hours
        ideal_allocation = goal.get("estimated_hours", 10)

        # Actual allocation
        actual = min(
            ideal_allocation,
            remaining_budget,
            max(min_allocation, remaining_budget * 0.3)
        )

        if remaining_budget < min_allocation:
            # Not enough budget left
            break

        allocations.append(ResourceAllocation(
            goal_id=goal_id,
            allocated_hours=actual,
            priority=priority,
            rationale=f"分配 {actual:.1f}h 基于优先级 {priority:.0f}"
        ))

        remaining_budget -= actual

    return allocations


def resolve_prerequisite_conflict(goals: list[dict]) -> list[str]:
    """
    Resolve prerequisite conflicts via topological sort.

    Args:
        goals: List of goals with dependencies

    Returns:
        Ordered list of goal_ids
    """
    # Build dependency graph
    graph = {}
    in_degree = {}

    for goal in goals:
        goal_id = goal["goal_id"]
        prereqs = goal.get("prerequisites", [])
        graph[goal_id] = prereqs
        in_degree[goal_id] = len(prereqs)

    # Kahn's algorithm for topological sort
    queue = [g for g in graph if in_degree[g] == 0]
    result = []

    while queue:
        current = queue.pop(0)
        result.append(current)

        # Decrease in-degree for neighbors
        for goal_id, prereqs in graph.items():
            if current in prereqs:
                in_degree[goal_id] -= 1
                if in_degree[goal_id] == 0:
                    queue.append(goal_id)

    return result


def resolve_exclusive_conflict(goals: list[dict], strategy: str = "merge") -> dict:
    """
    Resolve exclusive conflicts.

    Args:
        goals: Mutually exclusive goals
        strategy: "merge" or "select_best"

    Returns:
        Resolution result
    """
    if strategy == "merge":
        # Merge into one comprehensive goal
        merged_goal = {
            "goal_id": uid("goal"),
            "topic": goals[0].get("topic", ""),
            "scope": _merge_scopes([g.get("scope", {}) for g in goals]),
            "priority": max(g.get("priority", 0) for g in goals),
            "source_event_ids": list(set(
                event_id
                for g in goals
                for event_id in g.get("source_event_ids", [])
            )),
        }

        return {
            "action": "merge",
            "merged_goal": merged_goal,
            "original_goals": [g["goal_id"] for g in goals],
        }

    else:  # select_best
        # Select highest priority goal
        best = max(goals, key=lambda g: g.get("priority", 0))

        return {
            "action": "select",
            "selected_goal": best["goal_id"],
            "deferred_goals": [g["goal_id"] for g in goals if g["goal_id"] != best["goal_id"]],
        }


def _merge_scopes(scopes: list[dict]) -> dict:
    """Merge multiple goal scopes."""
    # Simplified: take the broadest scope
    operations = [s.get("operation") for s in scopes if s]

    if "read_plan" in operations:
        # Merge URLs from all read_plan scopes
        all_urls = []
        for scope in scopes:
            if scope.get("operation") == "read_plan":
                all_urls.extend(scope.get("urls", []))

        return {
            "operation": "read_plan",
            "urls": list(set(all_urls))[:3]  # Max 3 URLs
        }

    # Otherwise, use first scope
    return scopes[0] if scopes else {}


# ============================================================================
# Coordination Strategy
# ============================================================================

def coordinate_goals(
    active_goals: list[dict],
    conflicts: list[GoalConflict],
    now: str
) -> dict:
    """
    Coordinate multiple goals by resolving conflicts.

    Args:
        active_goals: Active goals
        conflicts: Detected conflicts
        now: Current timestamp

    Returns:
        Coordination plan
    """
    plan = {
        "resource_allocations": [],
        "execution_order": [],
        "merged_goals": [],
        "deferred_goals": [],
        "timestamp": now,
    }

    for conflict in conflicts:
        conflict_goals = [
            g for g in active_goals
            if g["goal_id"] in conflict.goal_ids
        ]

        if conflict.conflict_type == "resource":
            # Allocate resources
            allocations = resolve_resource_conflict(conflict_goals)
            plan["resource_allocations"].extend(allocations)

        elif conflict.conflict_type == "prerequisite":
            # Order by dependencies
            order = resolve_prerequisite_conflict(conflict_goals)
            plan["execution_order"] = order

        elif conflict.conflict_type == "exclusive":
            # Merge or select
            resolution = resolve_exclusive_conflict(conflict_goals, strategy="merge")
            if resolution["action"] == "merge":
                plan["merged_goals"].append(resolution["merged_goal"])
            else:
                plan["deferred_goals"].extend(resolution["deferred_goals"])

    return plan
