"""
Life autonomy integration: connect tactical/strategic layers to Life loop decision-making.
"""

from datetime import datetime, timedelta
from typing import Any

from companion.autonomy import (
    ExplorationStrategy,
    InterestPriority,
    InterestPortfolio,
    PortfolioStrategy,
    sessions_to_depth,
)
from companion.autonomy_store import AutonomyStore
from companion.autonomy_tactical import (
    should_continue_exploration,
    update_exploration_metrics,
    select_next_exploration_target,
)
from companion.autonomy_strategic import (
    rebalance_portfolio,
    apply_portfolio_changes,
    generate_reflection,
)
from companion.util import uid


class LifeAutonomy:
    """
    Autonomy coordinator for Life loop.

    Provides tactical guidance (depth control, priority scheduling) and
    strategic management (portfolio optimization, self-reflection) to enhance
    long-term autonomous exploration.
    """

    def __init__(self, store, settings, clock):
        """
        Initialize autonomy coordinator.

        Args:
            store: Database store
            settings: Application settings
            clock: Clock function returning ISO timestamp
        """
        self.store = store
        self.settings = settings
        self.clock = clock
        self.autonomy_store = AutonomyStore(store)

    # ========================================================================
    # Tactical Layer: Decision Enhancement
    # ========================================================================

    def enhance_decision_context(self, decision_context: dict, sources: list[dict]) -> dict:
        """
        Enhance decision context with tactical guidance.

        Args:
            decision_context: Original decision context from life_context
            sources: Source events for this decision

        Returns:
            Enhanced context with tactical guidance
        """
        now = self.clock()

        # Add exploration depth guidance
        depth_guidance = []
        for interest in decision_context.get("self_interests", []):
            trait_id = interest.get("trait_id")
            if not trait_id:
                continue

            strategy = self.autonomy_store.get_exploration_strategy(trait_id)
            if strategy:
                # Get recent outcomes for this interest
                recent_outcomes = self._get_recent_outcomes(trait_id, limit=10)

                # Decide if should continue
                decision = should_continue_exploration(strategy, recent_outcomes, now)

                depth_guidance.append({
                    "trait_id": trait_id,
                    "topic": interest["topic"],
                    "current_depth": strategy.current_depth,
                    "action": decision.action,
                    "reason": decision.reason,
                    "confidence": decision.confidence,
                })

        # Add priority scheduling guidance
        selected_target = self._get_priority_target(decision_context, sources)

        enhanced = decision_context.copy()
        enhanced["autonomy_guidance"] = {
            "depth_control": depth_guidance,
            "suggested_target": selected_target,
            "generated_at": now,
        }

        return enhanced

    def _get_recent_outcomes(self, trait_id: str, limit: int = 10) -> list[dict]:
        """Get recent exploration outcomes for a trait."""
        # Query goals related to this trait
        rows = self.store.all(
            "SELECT g.goal_id,g.state,g.result_event_id,g.completed_at "
            "FROM goals g JOIN traits t USING(trait_id) "
            "WHERE t.trait_id=? AND g.state IN ('DONE','FAILED') "
            "ORDER BY g.completed_at DESC LIMIT ?",
            (trait_id, limit)
        )

        outcomes = []
        for row in rows:
            outcomes.append({
                "goal_id": row["goal_id"],
                "status": "SUCCESS" if row["state"] == "DONE" else "FAILED",
                "timestamp": row["completed_at"],
                "event_id": row["result_event_id"],
            })

        return outcomes

    def _get_priority_target(self, decision_context: dict, sources: list[dict]) -> dict | None:
        """Get highest priority exploration target."""
        now = self.clock()

        # Get all active priorities
        priorities = self.autonomy_store.list_interest_priorities(exclude_cooldown=True)

        if not priorities:
            return None

        # Get recent exploration history
        recent_history = self._get_recent_exploration_history(limit=20)

        # Get owner context from sources
        owner_context = self._build_owner_context(sources)

        # Select target
        selected_trait_id = select_next_exploration_target(
            priorities,
            recent_history,
            owner_context,
            now
        )

        if not selected_trait_id:
            return None

        # Find the selected priority
        selected = next((p for p in priorities if p.trait_id == selected_trait_id), None)

        if selected:
            return {
                "trait_id": selected.trait_id,
                "topic": selected.topic,
                "priority": selected.final_priority,
            }

        return None

    def _get_recent_exploration_history(self, limit: int = 20) -> list[dict]:
        """Get recent exploration history."""
        # Query recent goals
        rows = self.store.all(
            "SELECT g.goal_id,g.trait_id,g.state,g.completed_at "
            "FROM goals g "
            "WHERE g.state IN ('DONE','FAILED') AND g.completed_at IS NOT NULL "
            "ORDER BY g.completed_at DESC LIMIT ?",
            (limit,)
        )

        history = []
        for row in rows:
            history.append({
                "goal_id": row["goal_id"],
                "trait_id": row["trait_id"],
                "status": "SUCCESS" if row["state"] == "DONE" else "FAILED",
                "timestamp": row["completed_at"],
            })

        return history

    def _build_owner_context(self, sources: list[dict]) -> dict:
        """Build owner context from sources."""
        # Extract text from sources for mention detection
        recent_mentions = []

        for source in sources[-5:]:  # Last 5 sources
            event_id = source.get("event_id")
            if event_id:
                event = self.store.one(
                    "SELECT occurred_at,body FROM events WHERE event_id=?",
                    (event_id,)
                )
                if event:
                    occurred_at = datetime.fromisoformat(event["occurred_at"])
                    now = datetime.fromisoformat(self.clock())
                    hours_ago = (now - occurred_at).total_seconds() / 3600

                    recent_mentions.append({
                        "text": event.get("body", ""),
                        "hours_ago": hours_ago,
                    })

        return {"recent_mentions": recent_mentions}

    # ========================================================================
    # Post-Decision Updates
    # ========================================================================

    def update_after_decision(self, decision: dict, outcome: dict):
        """
        Update autonomy state after a Life decision.

        Args:
            decision: The Life decision that was made
            outcome: The outcome of executing that decision
        """
        now = self.clock()

        # Update exploration strategies based on outcome
        if decision.get("action") in ["EXPLORE", "CONTINUE"]:
            goal_id = decision.get("goal_id") or outcome.get("goal_id")
            if goal_id:
                trait_id = self._get_trait_for_goal(goal_id)
                if trait_id:
                    self._update_exploration_strategy(trait_id, outcome, now)

    def _get_trait_for_goal(self, goal_id: str) -> str | None:
        """Get trait_id associated with a goal."""
        row = self.store.one(
            "SELECT trait_id FROM goals WHERE goal_id=?",
            (goal_id,)
        )
        return row["trait_id"] if row else None

    def _update_exploration_strategy(self, trait_id: str, outcome: dict, now: str):
        """Update exploration strategy based on outcome."""
        strategy = self.autonomy_store.get_exploration_strategy(trait_id)

        if not strategy:
            # Initialize new strategy
            trait = self.store.one("SELECT topic FROM traits WHERE trait_id=?", (trait_id,))
            if not trait:
                return

            strategy = ExplorationStrategy(
                trait_id=trait_id,
                topic=trait["topic"],
                current_depth="surface",
                target_depth="moderate",
                sessions_invested=0,
                days_active=0,
                last_progress_at=now,
                learning_velocity=0.5,
                updated_at=now,
            )

        # Update metrics
        updated_strategy = update_exploration_metrics(strategy, outcome, now)

        # Check if depth should advance
        expected_sessions = {
            "surface": 1,
            "shallow": 3,
            "moderate": 8,
            "deep": 20,
            "expert": 50,
        }

        if updated_strategy.sessions_invested >= expected_sessions.get(updated_strategy.current_depth, 999):
            # Advance depth if learning velocity is good
            if updated_strategy.learning_velocity >= 0.6:
                depth_order = ["surface", "shallow", "moderate", "deep", "expert"]
                current_idx = depth_order.index(updated_strategy.current_depth)
                if current_idx < len(depth_order) - 1:
                    updated_strategy.current_depth = depth_order[current_idx + 1]

        self.autonomy_store.save_exploration_strategy(updated_strategy)

    # ========================================================================
    # Strategic Layer: Periodic Tasks
    # ========================================================================

    def run_portfolio_rebalancing(self) -> dict:
        """
        Run portfolio rebalancing.

        Returns:
            Summary of changes made
        """
        now = self.clock()

        # Get current portfolio
        portfolio = self.autonomy_store.get_portfolio()

        if not portfolio:
            # Initialize default portfolio
            portfolio = self._initialize_portfolio(now)
            self.autonomy_store.save_portfolio(portfolio)

        # Check if rebalancing is needed
        last_rebalanced = datetime.fromisoformat(portfolio.last_rebalanced_at)
        current = datetime.fromisoformat(now)
        days_since = (current - last_rebalanced).days

        if days_since < 7:  # Rebalance weekly
            return {"message": "Not yet time to rebalance", "days_since": days_since}

        # Get all interests
        all_interests = self._get_all_interests()

        # Get recent activity (last 60 days)
        recent_activity = self._get_activity_last_n_days(60)

        # Compute changes
        changes = rebalance_portfolio(portfolio, all_interests, recent_activity, now)

        # Apply changes
        updated_portfolio = apply_portfolio_changes(portfolio, changes, now)
        self.autonomy_store.save_portfolio(updated_portfolio)

        # Record changes
        for change in changes["promotions"] + changes["demotions"]:
            self.autonomy_store.record_portfolio_change(change)

        return {
            "promotions": len(changes["promotions"]),
            "demotions": len(changes["demotions"]),
            "removals": len(changes["removals"]),
            "reason": changes.get("reason", ""),
        }

    def run_self_reflection(self, period: str = "daily") -> list[dict]:
        """
        Run self-reflection for a time period.

        Args:
            period: "daily", "weekly", or "monthly"

        Returns:
            List of reflection insights
        """
        now = self.clock()

        # Get data for reflection period
        days_map = {"daily": 1, "weekly": 7, "monthly": 30}
        days = days_map.get(period, 1)

        interests = self._get_all_interests()
        activities = self._get_activity_last_n_days(days)
        owner_feedback = self._get_owner_feedback_last_n_days(days)

        # Generate insights
        insights = generate_reflection(period, interests, activities, owner_feedback, now)

        # Save insights
        for insight in insights:
            self.autonomy_store.save_reflection(insight)

        return [
            {
                "type": i.reflection_type,
                "observation": i.observation,
                "confidence": i.confidence,
            }
            for i in insights
        ]

    def _initialize_portfolio(self, now: str) -> InterestPortfolio:
        """Initialize a default portfolio."""
        return InterestPortfolio(
            core_interests=[],
            satellite_interests=[],
            exploratory_interests=[],
            diversity_score=0.0,
            balance_score=0.0,
            depth_distribution={},
            last_rebalanced_at=now,
            updated_at=now,
        )

    def _get_all_interests(self) -> list[dict]:
        """Get all tracked interests."""
        rows = self.store.all(
            "SELECT trait_id,topic,category FROM traits WHERE category='interest' "
            "ORDER BY updated_at DESC LIMIT 100"
        )
        return [dict(row) for row in rows]

    def _get_activity_last_n_days(self, days: int) -> list[dict]:
        """Get exploration activity from last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        rows = self.store.all(
            "SELECT g.goal_id,g.trait_id,g.state,g.completed_at "
            "FROM goals g "
            "WHERE g.completed_at>=? AND g.state IN ('DONE','FAILED') "
            "ORDER BY g.completed_at DESC",
            (cutoff,)
        )

        return [
            {
                "goal_id": row["goal_id"],
                "trait_id": row["trait_id"],
                "status": "SUCCESS" if row["state"] == "DONE" else "FAILED",
                "timestamp": row["completed_at"],
            }
            for row in rows
        ]

    def _get_owner_feedback_last_n_days(self, days: int) -> list[dict]:
        """Get owner feedback from last N days."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()

        # Get feedback from affect module
        from companion import affect

        rows = self.store.all(
            "SELECT event_id,occurred_at FROM events "
            "WHERE occurred_at>=? AND namespace='qq.private' "
            "ORDER BY occurred_at DESC LIMIT 100",
            (cutoff,)
        )

        feedback = []
        for row in rows:
            fb = affect.owner_feedback(self.store, self.settings, row["event_id"], self.clock())
            if fb:
                feedback.append({
                    "event_id": row["event_id"],
                    "timestamp": row["occurred_at"],
                    "feedback": fb,
                })

        return feedback
