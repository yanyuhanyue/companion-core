"""
Long-term autonomy system: tactical and strategic layers for self-directed exploration.

This module implements the tactical layer (exploration depth control, priority scheduling,
multi-goal coordination) and strategic layer (interest portfolio optimization, self-reflection)
on top of the existing Life loop execution layer.
"""

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field, model_validator

from companion.protocol import StrictModel

# ============================================================================
# Tactical Layer: Exploration Depth Control
# ============================================================================

class ExplorationStrategy(StrictModel):
    """Strategy for exploring a single interest topic."""

    trait_id: str = Field(max_length=128)
    topic: str = Field(max_length=200)

    # Depth parameters
    current_depth: Literal["surface", "shallow", "moderate", "deep", "expert"]
    target_depth: Literal["surface", "shallow", "moderate", "deep", "expert"]

    # State tracking
    sessions_invested: int = Field(ge=0)  # Number of exploration sessions
    days_active: int = Field(ge=0)        # Days with activity
    last_progress_at: str                  # ISO timestamp of last progress

    # Strategy parameters
    continuation_threshold: float = Field(ge=0.0, le=1.0, default=0.6)
    pivot_threshold: float = Field(ge=0.0, le=1.0, default=0.3)

    # Effectiveness metrics
    learning_velocity: float = Field(ge=0.0, le=1.0)  # Learning rate
    diminishing_returns: bool = False                  # Hit diminishing returns?

    updated_at: str


class DepthDecision(StrictModel):
    """Decision about exploration depth continuation."""

    action: Literal["continue", "pivot", "pause", "deepen"]
    reason: str = Field(max_length=200)
    confidence: float = Field(ge=0.0, le=1.0)

    # Supporting data
    learning_rate: float | None = None
    days_since_progress: int | None = None
    roi: float | None = None


# Depth level definitions
DEPTH_LEVELS = {
    "surface": {"sessions": 1, "description": "浅尝辄止，了解基本概念"},
    "shallow": {"sessions": 3, "description": "初步理解，掌握基础知识"},
    "moderate": {"sessions": 8, "description": "中等深度，能够独立探索"},
    "deep": {"sessions": 20, "description": "深入理解，形成系统知识"},
    "expert": {"sessions": 50, "description": "专家级别，能够创新和教授"},
}


# ============================================================================
# Tactical Layer: Priority Scheduling
# ============================================================================

class InterestPriority(StrictModel):
    """Priority scoring for an interest."""

    trait_id: str = Field(max_length=128)
    topic: str = Field(max_length=200)

    # Priority components (0-100 each)
    base_score: float = Field(ge=0.0, le=100.0)          # Base interest strength
    urgency_score: float = Field(ge=0.0, le=100.0)       # Time sensitivity
    momentum_score: float = Field(ge=0.0, le=100.0)      # Recent progress
    owner_signal_score: float = Field(ge=0.0, le=100.0)  # Owner's implicit preference

    # Final priority (computed)
    final_priority: float = Field(ge=0.0, le=100.0)

    # Scheduling state
    last_explored_at: str
    cooldown_until: str | None = None  # Cooldown period to avoid over-concentration

    updated_at: str


class SchedulingPolicy:
    """Scheduling policy parameters."""

    # Time slice allocation
    MAX_CONSECUTIVE_SESSIONS = 3  # Max consecutive sessions for same topic
    MIN_COOLDOWN_HOURS = 24       # Min cooldown period

    # Diversity parameters
    DIVERSITY_WEIGHT = 0.3
    RECENCY_WEIGHT = 0.2
    MOMENTUM_WEIGHT = 0.3
    OWNER_SIGNAL_WEIGHT = 0.2


# ============================================================================
# Tactical Layer: Multi-Goal Coordination
# ============================================================================

class GoalConflict(StrictModel):
    """Detected conflict between goals."""

    goal_ids: list[str] = Field(min_length=2, max_length=10)
    conflict_type: Literal["resource", "prerequisite", "exclusive", "sequential"]
    severity: Literal["low", "medium", "high"]
    resolution_strategy: str = Field(max_length=200)
    detected_at: str


class ResourceAllocation(StrictModel):
    """Resource allocation decision."""

    goal_id: str = Field(max_length=128)
    allocated_hours: float = Field(ge=0.0)
    priority: float = Field(ge=0.0, le=100.0)
    rationale: str = Field(max_length=200)


# ============================================================================
# Strategic Layer: Interest Portfolio
# ============================================================================

class InterestPortfolio(StrictModel):
    """Portfolio of interests at different commitment levels."""

    # Portfolio structure
    core_interests: list[str] = Field(max_length=5)       # 3-5 core interests
    satellite_interests: list[str] = Field(max_length=10) # 5-10 satellite interests
    exploratory_interests: list[str] = Field(max_length=20) # 10-20 exploratory

    # Portfolio characteristics
    diversity_score: float = Field(ge=0.0, le=1.0)
    balance_score: float = Field(ge=0.0, le=1.0)
    depth_distribution: dict[str, int]  # {depth: count}

    # Rebalancing parameters
    rebalance_threshold: float = Field(ge=0.0, le=1.0, default=0.3)
    max_core_interests: int = Field(default=5, ge=3, le=7)
    max_satellite_interests: int = Field(default=10, ge=5, le=15)

    last_rebalanced_at: str
    updated_at: str


class PortfolioChange(StrictModel):
    """A change in portfolio structure."""

    change_type: Literal["promotion", "demotion", "removal"]
    trait_id: str = Field(max_length=128)
    from_tier: Literal["core", "satellite", "exploratory", "none"]
    to_tier: Literal["core", "satellite", "exploratory", "removed"]
    reason: str = Field(max_length=200)
    created_at: str


class PortfolioStrategy:
    """Portfolio management strategy parameters."""

    # Allocation ratios
    CORE_TIME_ALLOCATION = 0.6      # 60% to core
    SATELLITE_TIME_ALLOCATION = 0.3  # 30% to satellite
    EXPLORATORY_TIME_ALLOCATION = 0.1 # 10% to exploratory

    # Promotion/demotion rules
    PROMOTE_TO_CORE_SESSIONS = 20
    PROMOTE_TO_SATELLITE_SESSIONS = 8
    DEMOTE_INACTIVE_DAYS = 30

    # Diversity constraints
    MIN_CATEGORY_DIVERSITY = 3


# ============================================================================
# Strategic Layer: Self-Reflection
# ============================================================================

class ReflectionInsight(StrictModel):
    """An insight from self-reflection."""

    reflection_id: str = Field(max_length=128)
    reflection_type: Literal["progress", "pattern", "adjustment", "discovery"]

    # Observation
    observation: str = Field(max_length=500)
    evidence: list[str] = Field(max_length=20)  # event_ids

    # Analysis
    interpretation: str = Field(max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)

    # Action
    suggested_action: str | None = Field(default=None, max_length=200)
    action_taken: str | None = Field(default=None, max_length=200)

    created_at: str
    reviewed_by_owner: bool = False


class ReflectionSchedule:
    """Reflection schedule configuration."""

    DAILY = {
        "frequency": "daily",
        "focus": ["progress", "pattern"],
        "depth": "shallow"
    }

    WEEKLY = {
        "frequency": "weekly",
        "focus": ["progress", "pattern", "adjustment"],
        "depth": "moderate"
    }

    MONTHLY = {
        "frequency": "monthly",
        "focus": ["progress", "pattern", "adjustment", "discovery"],
        "depth": "deep"
    }


# ============================================================================
# Utility Functions
# ============================================================================

def depth_to_sessions(depth: str) -> int:
    """Convert depth level to expected session count."""
    return DEPTH_LEVELS.get(depth, {}).get("sessions", 0)


def sessions_to_depth(sessions: int) -> str:
    """Convert session count to depth level."""
    if sessions >= 50:
        return "expert"
    elif sessions >= 20:
        return "deep"
    elif sessions >= 8:
        return "moderate"
    elif sessions >= 3:
        return "shallow"
    else:
        return "surface"


def calculate_roi(sessions_invested: int, outcomes: list[dict]) -> float:
    """
    Calculate return on investment for exploration.

    Args:
        sessions_invested: Number of sessions spent
        outcomes: List of exploration outcomes with success indicators

    Returns:
        ROI score (0-1)
    """
    if sessions_invested == 0:
        return 0.0

    successful = sum(1 for o in outcomes if o.get("status") == "SUCCESS")
    return min(1.0, successful / sessions_invested)


def days_between(timestamp1: str, timestamp2: str) -> int:
    """Calculate days between two ISO timestamps."""
    dt1 = datetime.fromisoformat(timestamp1)
    dt2 = datetime.fromisoformat(timestamp2)
    return abs((dt2 - dt1).days)
