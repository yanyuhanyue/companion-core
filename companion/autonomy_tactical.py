"""
Autonomy engines: tactical layer algorithms for exploration control and scheduling.
"""

import json
from datetime import datetime, timedelta
from typing import Any

from companion.autonomy import (
    DEPTH_LEVELS,
    DepthDecision,
    ExplorationStrategy,
    InterestPriority,
    SchedulingPolicy,
    calculate_roi,
    days_between,
)

# ============================================================================
# Exploration Depth Controller
# ============================================================================

def should_continue_exploration(
    strategy: ExplorationStrategy,
    recent_outcomes: list[dict],
    now: str
) -> DepthDecision:
    """
    Decide whether to continue exploration at current depth.

    Args:
        strategy: Current exploration strategy
        recent_outcomes: Recent exploration outcomes (last 5-10 sessions)
        now: Current ISO timestamp

    Returns:
        DepthDecision with action and reasoning
    """

    # 1. Check diminishing returns
    if strategy.diminishing_returns:
        learning_rate = _calculate_learning_rate(recent_outcomes)
        if learning_rate < 0.3:
            return DepthDecision(
                action="pivot",
                reason="收益递减，建议转向其他话题",
                confidence=0.8,
                learning_rate=learning_rate
            )

    # 2. Check if target depth reached
    depth_order = ["surface", "shallow", "moderate", "deep", "expert"]
    current_idx = depth_order.index(strategy.current_depth)
    target_idx = depth_order.index(strategy.target_depth)

    if current_idx >= target_idx:
        if strategy.learning_velocity > 0.7:
            return DepthDecision(
                action="deepen",
                reason="超预期进展，建议加深探索",
                confidence=0.9,
                learning_rate=strategy.learning_velocity
            )
        else:
            return DepthDecision(
                action="pause",
                reason="已达目标深度",
                confidence=0.95
            )

    # 3. Check for stagnation
    days_since_progress = days_between(strategy.last_progress_at, now)
    if days_since_progress > 7:
        return DepthDecision(
            action="pivot",
            reason=f"已{days_since_progress}天无进展，建议暂停",
            confidence=0.85,
            days_since_progress=days_since_progress
        )

    # 4. Evaluate ROI
    roi = calculate_roi(strategy.sessions_invested, recent_outcomes)
    if roi < strategy.pivot_threshold:
        return DepthDecision(
            action="pause",
            reason="投入产出比过低",
            confidence=0.75,
            roi=roi
        )

    # 5. Check if ready to advance depth
    expected_sessions = DEPTH_LEVELS[strategy.current_depth]["sessions"]
    if strategy.sessions_invested >= expected_sessions * 1.2:  # 120% of expected
        if roi >= strategy.continuation_threshold:
            return DepthDecision(
                action="deepen",
                reason=f"已完成{strategy.sessions_invested}次探索，准备进入下一深度",
                confidence=0.85,
                roi=roi
            )

    # 6. Default: continue
    return DepthDecision(
        action="continue",
        reason="正常进展中",
        confidence=0.7,
        roi=roi
    )


def _calculate_learning_rate(outcomes: list[dict]) -> float:
    """
    Calculate recent learning rate from outcomes.

    Args:
        outcomes: List of exploration outcomes

    Returns:
        Learning rate (0-1)
    """
    if not outcomes:
        return 0.5  # Neutral default

    # Count successes in recent sessions
    recent = outcomes[-5:]  # Last 5 sessions
    successes = sum(1 for o in recent if o.get("status") == "SUCCESS")

    # Weight recent more heavily
    if len(recent) >= 3:
        very_recent = recent[-3:]
        recent_successes = sum(1 for o in very_recent if o.get("status") == "SUCCESS")
        return (successes / len(recent)) * 0.5 + (recent_successes / len(very_recent)) * 0.5
    else:
        return successes / len(recent) if recent else 0.0


def update_exploration_metrics(
    strategy: ExplorationStrategy,
    outcome: dict,
    now: str
) -> ExplorationStrategy:
    """
    Update exploration strategy metrics based on new outcome.

    Args:
        strategy: Current strategy
        outcome: Latest exploration outcome
        now: Current ISO timestamp

    Returns:
        Updated strategy
    """
    strategy.sessions_invested += 1

    # Update last progress time if successful
    if outcome.get("status") == "SUCCESS":
        strategy.last_progress_at = now

    # Recalculate learning velocity (exponential moving average)
    new_velocity = 1.0 if outcome.get("status") == "SUCCESS" else 0.0
    alpha = 0.3  # EMA smoothing factor
    strategy.learning_velocity = alpha * new_velocity + (1 - alpha) * strategy.learning_velocity

    # Check for diminishing returns (last 5 sessions all low velocity)
    # This would be based on actual outcome history in practice
    strategy.diminishing_returns = strategy.learning_velocity < 0.3

    strategy.updated_at = now

    return strategy


# ============================================================================
# Priority Scheduler
# ============================================================================

def select_next_exploration_target(
    active_interests: list[InterestPriority],
    recent_history: list[dict],
    owner_context: dict,
    now: str
) -> str | None:
    """
    Select the next interest to explore based on priorities.

    Args:
        active_interests: List of interests with priorities
        recent_history: Recent exploration history
        owner_context: Owner's recent activity and signals
        now: Current ISO timestamp

    Returns:
        trait_id of selected interest, or None if nothing available
    """

    # 1. Filter out interests in cooldown
    current_dt = datetime.fromisoformat(now)
    available = [
        i for i in active_interests
        if not i.cooldown_until or datetime.fromisoformat(i.cooldown_until) <= current_dt
    ]

    if not available:
        return None

    # 2. Calculate final priorities
    for interest in available:
        # Urgency: time-sensitive topics
        urgency = _calculate_urgency(interest, now)

        # Momentum: recent progress
        momentum = _calculate_momentum(interest, recent_history)

        # Owner signal: recently mentioned by owner
        owner_signal = _calculate_owner_signal(interest, owner_context)

        # Diversity penalty: recently explored topics
        diversity_penalty = _calculate_diversity_penalty(interest, recent_history)

        # Update scores
        interest.urgency_score = urgency
        interest.momentum_score = momentum
        interest.owner_signal_score = owner_signal

        # Calculate final priority
        interest.final_priority = (
            interest.base_score * 0.3 +
            urgency * SchedulingPolicy.RECENCY_WEIGHT +
            momentum * SchedulingPolicy.MOMENTUM_WEIGHT +
            owner_signal * SchedulingPolicy.OWNER_SIGNAL_WEIGHT -
            diversity_penalty * SchedulingPolicy.DIVERSITY_WEIGHT
        )

    # 3. Select highest priority
    selected = max(available, key=lambda x: x.final_priority)

    # 4. Check if cooldown should be applied
    consecutive_count = _count_consecutive_explorations(selected.trait_id, recent_history)
    if consecutive_count >= SchedulingPolicy.MAX_CONSECUTIVE_SESSIONS - 1:
        # Apply cooldown for next time
        cooldown_dt = current_dt + timedelta(hours=SchedulingPolicy.MIN_COOLDOWN_HOURS)
        selected.cooldown_until = cooldown_dt.isoformat()

    selected.last_explored_at = now
    selected.updated_at = now

    return selected.trait_id


def _calculate_urgency(interest: InterestPriority, now: str) -> float:
    """
    Calculate urgency score (0-100).

    Time-sensitive topics get higher scores.
    """
    # For now, use simple recency-based urgency
    last_explored = datetime.fromisoformat(interest.last_explored_at)
    current = datetime.fromisoformat(now)
    days_since = (current - last_explored).days

    # Linear decay: 100 at 0 days, 0 at 30+ days
    urgency = max(0, 100 - (days_since * 3.33))

    return urgency


def _calculate_momentum(interest: InterestPriority, history: list[dict]) -> float:
    """
    Calculate momentum score (0-100) based on recent progress.
    """
    # Find recent explorations of this interest
    recent = [h for h in history[-10:] if h.get("trait_id") == interest.trait_id]

    if not recent:
        return 0.0

    # Count successes
    successes = sum(1 for r in recent if r.get("status") == "SUCCESS")

    # Momentum is success rate * 100
    return (successes / len(recent)) * 100


def _calculate_owner_signal(interest: InterestPriority, context: dict) -> float:
    """
    Calculate owner signal score (0-100).

    Higher if owner recently mentioned or engaged with this topic.
    """
    # Check if topic appears in recent owner messages
    recent_mentions = context.get("recent_mentions", [])

    for mention in recent_mentions:
        if interest.topic.lower() in mention.get("text", "").lower():
            # Weight by recency
            hours_ago = mention.get("hours_ago", 999)
            if hours_ago < 1:
                return 100.0
            elif hours_ago < 6:
                return 80.0
            elif hours_ago < 24:
                return 60.0
            elif hours_ago < 72:
                return 40.0

    return 0.0


def _calculate_diversity_penalty(interest: InterestPriority, history: list[dict]) -> float:
    """
    Calculate diversity penalty (0-100).

    Higher penalty for recently over-explored topics.
    """
    # Count how many of the last 10 explorations were this topic
    recent = history[-10:] if len(history) >= 10 else history
    count = sum(1 for h in recent if h.get("trait_id") == interest.trait_id)

    if not recent:
        return 0.0

    # Penalty proportional to concentration
    concentration = count / len(recent)

    return concentration * 100


def _count_consecutive_explorations(trait_id: str, history: list[dict]) -> int:
    """
    Count consecutive explorations of the same topic from most recent.
    """
    count = 0
    for h in reversed(history):
        if h.get("trait_id") == trait_id:
            count += 1
        else:
            break
    return count


# ============================================================================
# Helper Functions
# ============================================================================

def encode_json(obj: Any) -> str:
    """Encode object to JSON string."""
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':'))


def decode_json(text: str) -> Any:
    """Decode JSON string to object."""
    return json.loads(text)
