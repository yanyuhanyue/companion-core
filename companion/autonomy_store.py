"""
Autonomy store: database operations for tactical and strategic layers.
"""

import json
from datetime import datetime

from companion.autonomy import (
    ExplorationStrategy,
    InterestPortfolio,
    InterestPriority,
    PortfolioChange,
    ReflectionInsight,
)


class AutonomyStore:
    """Database operations for autonomy system."""

    def __init__(self, store):
        """Initialize with a Store instance."""
        self.store = store

    # ========================================================================
    # Exploration Strategies
    # ========================================================================

    def get_exploration_strategy(self, trait_id: str) -> ExplorationStrategy | None:
        """Get exploration strategy for a trait."""
        row = self.store.one(
            "SELECT * FROM exploration_strategies WHERE trait_id=?",
            (trait_id,)
        )
        if not row:
            return None
        return ExplorationStrategy(**row)

    def save_exploration_strategy(self, strategy: ExplorationStrategy):
        """Save or update exploration strategy."""
        self.store.execute(
            "INSERT OR REPLACE INTO exploration_strategies "
            "(trait_id,topic,current_depth,target_depth,sessions_invested,days_active,"
            "last_progress_at,continuation_threshold,pivot_threshold,learning_velocity,"
            "diminishing_returns,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                strategy.trait_id,
                strategy.topic,
                strategy.current_depth,
                strategy.target_depth,
                strategy.sessions_invested,
                strategy.days_active,
                strategy.last_progress_at,
                strategy.continuation_threshold,
                strategy.pivot_threshold,
                strategy.learning_velocity,
                1 if strategy.diminishing_returns else 0,
                strategy.updated_at,
            )
        )

    def list_exploration_strategies(self, limit: int = 100) -> list[ExplorationStrategy]:
        """List all exploration strategies."""
        rows = self.store.all(
            "SELECT * FROM exploration_strategies ORDER BY updated_at DESC LIMIT ?",
            (limit,)
        )
        return [ExplorationStrategy(**row) for row in rows]

    # ========================================================================
    # Interest Priorities
    # ========================================================================

    def get_interest_priority(self, trait_id: str) -> InterestPriority | None:
        """Get interest priority for a trait."""
        row = self.store.one(
            "SELECT * FROM interest_priorities WHERE trait_id=?",
            (trait_id,)
        )
        if not row:
            return None
        return InterestPriority(**row)

    def save_interest_priority(self, priority: InterestPriority):
        """Save or update interest priority."""
        self.store.execute(
            "INSERT OR REPLACE INTO interest_priorities "
            "(trait_id,topic,base_score,urgency_score,momentum_score,owner_signal_score,"
            "final_priority,last_explored_at,cooldown_until,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                priority.trait_id,
                priority.topic,
                priority.base_score,
                priority.urgency_score,
                priority.momentum_score,
                priority.owner_signal_score,
                priority.final_priority,
                priority.last_explored_at,
                priority.cooldown_until,
                priority.updated_at,
            )
        )

    def list_interest_priorities(
        self,
        exclude_cooldown: bool = False,
        limit: int = 100
    ) -> list[InterestPriority]:
        """List interest priorities."""
        if exclude_cooldown:
            now = datetime.now().isoformat()
            rows = self.store.all(
                "SELECT * FROM interest_priorities "
                "WHERE cooldown_until IS NULL OR cooldown_until<=? "
                "ORDER BY final_priority DESC LIMIT ?",
                (now, limit)
            )
        else:
            rows = self.store.all(
                "SELECT * FROM interest_priorities "
                "ORDER BY final_priority DESC LIMIT ?",
                (limit,)
            )
        return [InterestPriority(**row) for row in rows]

    # ========================================================================
    # Interest Portfolio
    # ========================================================================

    def get_portfolio(self, portfolio_id: str = "default") -> InterestPortfolio | None:
        """Get interest portfolio."""
        row = self.store.one(
            "SELECT * FROM interest_portfolio WHERE portfolio_id=?",
            (portfolio_id,)
        )
        if not row:
            return None

        return InterestPortfolio(
            core_interests=json.loads(row["core_interests_json"]),
            satellite_interests=json.loads(row["satellite_interests_json"]),
            exploratory_interests=json.loads(row["exploratory_interests_json"]),
            diversity_score=row["diversity_score"],
            balance_score=row["balance_score"],
            depth_distribution=json.loads(row["depth_distribution_json"]),
            rebalance_threshold=row["rebalance_threshold"],
            max_core_interests=row["max_core_interests"],
            max_satellite_interests=row["max_satellite_interests"],
            last_rebalanced_at=row["last_rebalanced_at"],
            updated_at=row["updated_at"],
        )

    def save_portfolio(self, portfolio: InterestPortfolio, portfolio_id: str = "default"):
        """Save or update interest portfolio."""
        self.store.execute(
            "INSERT OR REPLACE INTO interest_portfolio "
            "(portfolio_id,core_interests_json,satellite_interests_json,"
            "exploratory_interests_json,diversity_score,balance_score,"
            "depth_distribution_json,rebalance_threshold,max_core_interests,"
            "max_satellite_interests,last_rebalanced_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                portfolio_id,
                json.dumps(portfolio.core_interests, ensure_ascii=False),
                json.dumps(portfolio.satellite_interests, ensure_ascii=False),
                json.dumps(portfolio.exploratory_interests, ensure_ascii=False),
                portfolio.diversity_score,
                portfolio.balance_score,
                json.dumps(portfolio.depth_distribution, ensure_ascii=False),
                portfolio.rebalance_threshold,
                portfolio.max_core_interests,
                portfolio.max_satellite_interests,
                portfolio.last_rebalanced_at,
                portfolio.updated_at,
            )
        )

    def record_portfolio_change(self, change: PortfolioChange):
        """Record a portfolio change."""
        self.store.execute(
            "INSERT INTO portfolio_changes "
            "(change_id,change_type,trait_id,from_tier,to_tier,reason,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                change.change_type + "_" + change.trait_id + "_" + change.created_at[:10],
                change.change_type,
                change.trait_id,
                change.from_tier,
                change.to_tier,
                change.reason,
                change.created_at,
            )
        )

    def list_portfolio_changes(
        self,
        trait_id: str | None = None,
        limit: int = 50
    ) -> list[dict]:
        """List portfolio change history."""
        if trait_id:
            rows = self.store.all(
                "SELECT * FROM portfolio_changes WHERE trait_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (trait_id, limit)
            )
        else:
            rows = self.store.all(
                "SELECT * FROM portfolio_changes ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        return rows

    # ========================================================================
    # Reflection Insights
    # ========================================================================

    def save_reflection(self, insight: ReflectionInsight):
        """Save a reflection insight."""
        self.store.execute(
            "INSERT INTO reflection_insights "
            "(reflection_id,reflection_type,observation,evidence_json,"
            "interpretation,confidence,suggested_action,action_taken,"
            "created_at,reviewed_by_owner,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                insight.reflection_id,
                insight.reflection_type,
                insight.observation,
                json.dumps(insight.evidence, ensure_ascii=False),
                insight.interpretation,
                insight.confidence,
                insight.suggested_action,
                insight.action_taken,
                insight.created_at,
                1 if insight.reviewed_by_owner else 0,
                insight.created_at,
            )
        )

    def list_reflections(
        self,
        reflection_type: str | None = None,
        unreviewed_only: bool = False,
        limit: int = 50
    ) -> list[dict]:
        """List reflection insights."""
        if reflection_type and unreviewed_only:
            rows = self.store.all(
                "SELECT * FROM reflection_insights "
                "WHERE reflection_type=? AND reviewed_by_owner=0 "
                "ORDER BY created_at DESC LIMIT ?",
                (reflection_type, limit)
            )
        elif reflection_type:
            rows = self.store.all(
                "SELECT * FROM reflection_insights WHERE reflection_type=? "
                "ORDER BY created_at DESC LIMIT ?",
                (reflection_type, limit)
            )
        elif unreviewed_only:
            rows = self.store.all(
                "SELECT * FROM reflection_insights WHERE reviewed_by_owner=0 "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        else:
            rows = self.store.all(
                "SELECT * FROM reflection_insights ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
        return rows

    def mark_reflection_reviewed(self, reflection_id: str):
        """Mark a reflection as reviewed by owner."""
        self.store.execute(
            "UPDATE reflection_insights SET reviewed_by_owner=1,updated_at=? "
            "WHERE reflection_id=?",
            (datetime.now().isoformat(), reflection_id)
        )

    # ========================================================================
    # Initialization
    # ========================================================================

    def initialize_schema(self):
        """Initialize autonomy tables if they don't exist."""
        # Read and execute schema file
        import os
        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "db", "autonomy_schema.sql"
        )

        if os.path.exists(schema_path):
            with open(schema_path, encoding="utf-8") as f:
                schema_sql = f.read()

            # Execute each statement
            for statement in schema_sql.split(";"):
                statement = statement.strip()
                if statement:
                    self.store.execute(statement)
