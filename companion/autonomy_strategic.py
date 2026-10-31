"""
Autonomy engines: strategic layer algorithms for portfolio optimization and self-reflection.
"""

from datetime import datetime

from companion.autonomy import (
    InterestPortfolio,
    PortfolioChange,
    PortfolioStrategy,
    ReflectionInsight,
    ReflectionSchedule,
)
from companion.util import uid

# ============================================================================
# Interest Portfolio Optimizer
# ============================================================================

def rebalance_portfolio(
    portfolio: InterestPortfolio,
    all_interests: list[dict],
    recent_activity: list[dict],
    now: str
) -> dict:
    """
    Rebalance interest portfolio based on activity patterns.

    Args:
        portfolio: Current portfolio state
        all_interests: All tracked interests with metrics
        recent_activity: Recent exploration activity (last 60 days)
        now: Current ISO timestamp

    Returns:
        {
            "promotions": [PortfolioChange],
            "demotions": [PortfolioChange],
            "removals": [str],  # trait_ids
            "reason": str
        }
    """

    changes = {
        "promotions": [],
        "demotions": [],
        "removals": [],
        "reason": ""
    }

    # 1. Check promotion conditions
    for interest in all_interests:
        trait_id = interest["trait_id"]
        sessions = _count_sessions(trait_id, recent_activity)
        current_tier = _get_tier(trait_id, portfolio)

        # Exploratory -> Satellite
        if (current_tier == "exploratory"
            and sessions >= PortfolioStrategy.PROMOTE_TO_SATELLITE_SESSIONS):
            if len(portfolio.satellite_interests) < portfolio.max_satellite_interests:
                changes["promotions"].append(PortfolioChange(
                    change_type="promotion",
                    trait_id=trait_id,
                    from_tier="exploratory",
                    to_tier="satellite",
                    reason=f"达到 {sessions} 个探索会话",
                    created_at=now
                ))

        # Satellite -> Core
        elif current_tier == "satellite" and sessions >= PortfolioStrategy.PROMOTE_TO_CORE_SESSIONS:
            if len(portfolio.core_interests) < portfolio.max_core_interests:
                changes["promotions"].append(PortfolioChange(
                    change_type="promotion",
                    trait_id=trait_id,
                    from_tier="satellite",
                    to_tier="core",
                    reason=f"达到 {sessions} 个探索会话，深度投入",
                    created_at=now
                ))

    # 2. Check demotion conditions (inactivity)
    for trait_id in portfolio.core_interests + portfolio.satellite_interests:
        days_inactive = _days_since_last_activity(trait_id, recent_activity, now)

        if days_inactive > PortfolioStrategy.DEMOTE_INACTIVE_DAYS:
            current_tier = _get_tier(trait_id, portfolio)
            new_tier = "satellite" if current_tier == "core" else "exploratory"

            changes["demotions"].append(PortfolioChange(
                change_type="demotion",
                trait_id=trait_id,
                from_tier=current_tier,
                to_tier=new_tier,
                reason=f"超过 {days_inactive} 天无活动",
                created_at=now
            ))

    # 3. Remove long-inactive exploratory interests
    for trait_id in portfolio.exploratory_interests:
        days_inactive = _days_since_last_activity(trait_id, recent_activity, now)

        if days_inactive > PortfolioStrategy.DEMOTE_INACTIVE_DAYS * 2:
            changes["removals"].append(trait_id)

    # 4. Check diversity
    diversity = _calculate_portfolio_diversity(portfolio, all_interests)
    if diversity < PortfolioStrategy.MIN_CATEGORY_DIVERSITY:
        changes["reason"] = f"多样性不足 ({diversity} < {PortfolioStrategy.MIN_CATEGORY_DIVERSITY})"

    return changes


def apply_portfolio_changes(
    portfolio: InterestPortfolio,
    changes: dict,
    now: str
) -> InterestPortfolio:
    """
    Apply changes to portfolio.

    Args:
        portfolio: Current portfolio
        changes: Changes from rebalance_portfolio
        now: Current timestamp

    Returns:
        Updated portfolio
    """

    # Apply promotions
    for change in changes["promotions"]:
        _remove_from_tier(portfolio, change.trait_id, change.from_tier)
        _add_to_tier(portfolio, change.trait_id, change.to_tier)

    # Apply demotions
    for change in changes["demotions"]:
        _remove_from_tier(portfolio, change.trait_id, change.from_tier)
        _add_to_tier(portfolio, change.trait_id, change.to_tier)

    # Apply removals
    for trait_id in changes["removals"]:
        _remove_from_tier(portfolio, trait_id, "exploratory")

    portfolio.last_rebalanced_at = now
    portfolio.updated_at = now

    return portfolio


def _get_tier(trait_id: str, portfolio: InterestPortfolio) -> str:
    """Get the tier of an interest in the portfolio."""
    if trait_id in portfolio.core_interests:
        return "core"
    elif trait_id in portfolio.satellite_interests:
        return "satellite"
    elif trait_id in portfolio.exploratory_interests:
        return "exploratory"
    else:
        return "none"


def _add_to_tier(portfolio: InterestPortfolio, trait_id: str, tier: str):
    """Add interest to a tier."""
    if tier == "core" and trait_id not in portfolio.core_interests:
        portfolio.core_interests.append(trait_id)
    elif tier == "satellite" and trait_id not in portfolio.satellite_interests:
        portfolio.satellite_interests.append(trait_id)
    elif tier == "exploratory" and trait_id not in portfolio.exploratory_interests:
        portfolio.exploratory_interests.append(trait_id)


def _remove_from_tier(portfolio: InterestPortfolio, trait_id: str, tier: str):
    """Remove interest from a tier."""
    if tier == "core" and trait_id in portfolio.core_interests:
        portfolio.core_interests.remove(trait_id)
    elif tier == "satellite" and trait_id in portfolio.satellite_interests:
        portfolio.satellite_interests.remove(trait_id)
    elif tier == "exploratory" and trait_id in portfolio.exploratory_interests:
        portfolio.exploratory_interests.remove(trait_id)


def _count_sessions(trait_id: str, activity: list[dict]) -> int:
    """Count exploration sessions for an interest."""
    return sum(1 for a in activity if a.get("trait_id") == trait_id)


def _days_since_last_activity(trait_id: str, activity: list[dict], now: str) -> int:
    """Calculate days since last activity for an interest."""
    relevant = [a for a in activity if a.get("trait_id") == trait_id]

    if not relevant:
        return 999  # Very large number if no activity

    last_activity = max(relevant, key=lambda x: x.get("timestamp", ""))
    last_dt = datetime.fromisoformat(last_activity["timestamp"])
    current_dt = datetime.fromisoformat(now)

    return (current_dt - last_dt).days


def _calculate_portfolio_diversity(portfolio: InterestPortfolio, all_interests: list[dict]) -> int:
    """
    Calculate category diversity in portfolio.

    Returns:
        Number of unique categories represented
    """
    all_trait_ids = (
        portfolio.core_interests +
        portfolio.satellite_interests +
        portfolio.exploratory_interests
    )

    categories = set()
    for interest in all_interests:
        if interest["trait_id"] in all_trait_ids:
            categories.add(interest.get("category", "unknown"))

    return len(categories)


# ============================================================================
# Self-Reflection Engine
# ============================================================================

def generate_reflection(
    time_period: str,
    interests: list[dict],
    activities: list[dict],
    owner_feedback: list[dict],
    now: str
) -> list[ReflectionInsight]:
    """
    Generate reflection insights for a time period.

    Args:
        time_period: "daily", "weekly", or "monthly"
        interests: All tracked interests
        activities: Exploration activities in the period
        owner_feedback: Owner feedback in the period
        now: Current timestamp

    Returns:
        List of reflection insights
    """

    insights = []
    schedule = getattr(ReflectionSchedule, time_period.upper(), ReflectionSchedule.DAILY)

    # 1. Progress review
    if "progress" in schedule["focus"]:
        progress_insights = _reflect_on_progress(interests, activities, now)
        insights.extend(progress_insights)

    # 2. Pattern discovery
    if "pattern" in schedule["focus"]:
        pattern_insights = _detect_patterns(activities, interests, now)
        insights.extend(pattern_insights)

    # 3. Strategy adjustment suggestions
    if "adjustment" in schedule["focus"]:
        adjustment_insights = _suggest_adjustments(interests, activities, owner_feedback, now)
        insights.extend(adjustment_insights)

    # 4. New discoveries (monthly only)
    if "discovery" in schedule["focus"]:
        discovery_insights = _identify_discoveries(interests, activities, now)
        insights.extend(discovery_insights)

    return insights


def _reflect_on_progress(
    interests: list[dict],
    activities: list[dict],
    now: str
) -> list[ReflectionInsight]:
    """Generate progress reflection insights."""
    insights = []

    for interest in interests:
        trait_id = interest["trait_id"]
        topic = interest["topic"]

        # Count recent sessions
        recent_sessions = [a for a in activities if a.get("trait_id") == trait_id]

        if len(recent_sessions) >= 3:  # Significant activity
            successes = sum(1 for s in recent_sessions if s.get("status") == "SUCCESS")
            success_rate = successes / len(recent_sessions)

            if success_rate >= 0.7:  # High success rate
                insights.append(ReflectionInsight(
                    reflection_id=uid("reflection"),
                    reflection_type="progress",
                    observation=f"在 {topic} 方面取得显著进展",
                    evidence=[s.get("event_id", "") for s in recent_sessions],
                    interpretation=(f"过去 {len(recent_sessions)} 次探索中有 {successes} 次成功，"
                                    "学习效率高"),
                    confidence=min(0.95, success_rate),
                    created_at=now
                ))

    return insights


def _detect_patterns(
    activities: list[dict],
    interests: list[dict],
    now: str
) -> list[ReflectionInsight]:
    """Detect patterns in exploration behavior."""
    insights = []

    # Pattern 1: Time-of-day preference
    time_pattern = _analyze_time_pattern(activities)
    if time_pattern["strength"] > 0.7:
        insights.append(ReflectionInsight(
            reflection_id=uid("reflection"),
            reflection_type="pattern",
            observation=time_pattern["description"],
            evidence=time_pattern["evidence_ids"],
            interpretation=time_pattern["interpretation"],
            confidence=time_pattern["strength"],
            suggested_action=time_pattern.get("suggestion"),
            created_at=now
        ))

    # Pattern 2: Topic clustering
    cluster_pattern = _analyze_topic_clustering(activities, interests)
    if cluster_pattern["strength"] > 0.7:
        insights.append(ReflectionInsight(
            reflection_id=uid("reflection"),
            reflection_type="pattern",
            observation=cluster_pattern["description"],
            evidence=cluster_pattern["evidence_ids"],
            interpretation=cluster_pattern["interpretation"],
            confidence=cluster_pattern["strength"],
            created_at=now
        ))

    return insights


def _suggest_adjustments(
    interests: list[dict],
    activities: list[dict],
    owner_feedback: list[dict],
    now: str
) -> list[ReflectionInsight]:
    """Suggest strategy adjustments based on performance."""
    insights = []

    # Check for over-concentration
    trait_counts = {}
    for activity in activities[-20:]:  # Last 20 activities
        trait_id = activity.get("trait_id")
        if trait_id:
            trait_counts[trait_id] = trait_counts.get(trait_id, 0) + 1

    if trait_counts:
        max_count = max(trait_counts.values())
        if max_count > 10:  # More than 50% concentration
            dominant_trait = max(trait_counts, key=trait_counts.get)
            dominant_topic = next(
                (i["topic"] for i in interests if i["trait_id"] == dominant_trait), "未知",
            )

            insights.append(ReflectionInsight(
                reflection_id=uid("reflection"),
                reflection_type="adjustment",
                observation=f"探索活动过度集中在 {dominant_topic}",
                evidence=[a.get("event_id", "") for a in activities
                          if a.get("trait_id") == dominant_trait],
                interpretation=f"最近20次探索中有{max_count}次关于同一话题，可能缺乏多样性",
                confidence=0.85,
                suggested_action="建议增加其他话题的探索，保持组合平衡",
                created_at=now
            ))

    return insights


def _identify_discoveries(
    interests: list[dict],
    activities: list[dict],
    now: str
) -> list[ReflectionInsight]:
    """Identify new discoveries or emerging interests."""
    insights = []

    # Look for rapidly growing interests
    for interest in interests:
        trait_id = interest["trait_id"]
        topic = interest["topic"]

        # Count sessions in last 30 days
        sessions = [a for a in activities if a.get("trait_id") == trait_id]

        if len(sessions) >= 5:  # Minimum activity threshold
            # Check if this is a new interest (first session recent)
            first_session = min(sessions, key=lambda x: x.get("timestamp", ""))
            first_dt = datetime.fromisoformat(first_session["timestamp"])
            current_dt = datetime.fromisoformat(now)
            days_since_first = (current_dt - first_dt).days

            if days_since_first <= 30:  # New within last month
                insights.append(ReflectionInsight(
                    reflection_id=uid("reflection"),
                    reflection_type="discovery",
                    observation=f"新发现兴趣领域：{topic}",
                    evidence=[s.get("event_id", "") for s in sessions],
                    interpretation=(f"在过去 {days_since_first} 天内进行了 {len(sessions)} 次探索，"
                                    "显示出强烈兴趣"),
                    confidence=0.8,
                    created_at=now
                ))

    return insights


def _analyze_time_pattern(activities: list[dict]) -> dict:
    """Analyze time-of-day patterns in activities."""
    if not activities:
        return {"strength": 0.0}

    # Group by hour of day
    hour_counts = {}
    for activity in activities:
        timestamp = activity.get("timestamp")
        if timestamp:
            dt = datetime.fromisoformat(timestamp)
            hour = dt.hour
            hour_counts[hour] = hour_counts.get(hour, 0) + 1

    if not hour_counts:
        return {"strength": 0.0}

    # Find peak hour
    peak_hour = max(hour_counts, key=hour_counts.get)
    peak_count = hour_counts[peak_hour]
    total = sum(hour_counts.values())

    concentration = peak_count / total

    if concentration >= 0.5:  # 50% or more in one hour
        return {
            "strength": concentration,
            "description": f"探索活动集中在 {peak_hour}:00-{peak_hour+1}:00",
            "evidence_ids": [a.get("event_id", "") for a in activities],
            "interpretation": f"{int(concentration*100)}% 的探索发生在这个时间段",
            "suggestion": "可以利用这个高效时段进行深度探索"
        }

    return {"strength": 0.0}


def _analyze_topic_clustering(activities: list[dict], interests: list[dict]) -> dict:
    """Analyze topic clustering patterns."""
    # Simplified clustering: check if consecutive activities are related
    consecutive_same = 0
    max_consecutive = 0

    prev_trait = None
    for activity in activities:
        curr_trait = activity.get("trait_id")
        if curr_trait == prev_trait:
            consecutive_same += 1
            max_consecutive = max(max_consecutive, consecutive_same)
        else:
            consecutive_same = 1
        prev_trait = curr_trait

    if max_consecutive >= 3:
        return {
            "strength": min(1.0, max_consecutive / 5),
            "description": "倾向于连续探索同一话题",
            "evidence_ids": [a.get("event_id", "") for a in activities],
            "interpretation": f"最长连续探索同一话题 {max_consecutive} 次，显示专注特征"
        }

    return {"strength": 0.0}
