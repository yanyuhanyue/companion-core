-- Long-term autonomy system: database schema
-- Tactical and strategic layer tables

-- ============================================================================
-- Tactical Layer: Exploration Strategies
-- ============================================================================

CREATE TABLE IF NOT EXISTS exploration_strategies (
    trait_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,

    -- Depth
    current_depth TEXT NOT NULL CHECK(current_depth IN ('surface','shallow','moderate','deep','expert')),
    target_depth TEXT NOT NULL CHECK(target_depth IN ('surface','shallow','moderate','deep','expert')),

    -- State
    sessions_invested INTEGER NOT NULL DEFAULT 0,
    days_active INTEGER NOT NULL DEFAULT 0,
    last_progress_at TEXT NOT NULL,

    -- Parameters
    continuation_threshold REAL NOT NULL DEFAULT 0.6,
    pivot_threshold REAL NOT NULL DEFAULT 0.3,

    -- Metrics
    learning_velocity REAL NOT NULL DEFAULT 0.5,
    diminishing_returns INTEGER NOT NULL DEFAULT 0,

    updated_at TEXT NOT NULL,

    FOREIGN KEY (trait_id) REFERENCES traits(trait_id)
);

CREATE INDEX IF NOT EXISTS idx_exploration_strategies_updated
    ON exploration_strategies(updated_at);

-- ============================================================================
-- Tactical Layer: Interest Priorities
-- ============================================================================

CREATE TABLE IF NOT EXISTS interest_priorities (
    trait_id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,

    -- Priority components
    base_score REAL NOT NULL DEFAULT 50.0,
    urgency_score REAL NOT NULL DEFAULT 0.0,
    momentum_score REAL NOT NULL DEFAULT 0.0,
    owner_signal_score REAL NOT NULL DEFAULT 0.0,

    -- Final priority
    final_priority REAL NOT NULL DEFAULT 0.0,

    -- Scheduling
    last_explored_at TEXT NOT NULL,
    cooldown_until TEXT,

    updated_at TEXT NOT NULL,

    FOREIGN KEY (trait_id) REFERENCES traits(trait_id)
);

CREATE INDEX IF NOT EXISTS idx_interest_priorities_final
    ON interest_priorities(final_priority DESC);
CREATE INDEX IF NOT EXISTS idx_interest_priorities_cooldown
    ON interest_priorities(cooldown_until);

-- ============================================================================
-- Tactical Layer: Goal Conflicts
-- ============================================================================

CREATE TABLE IF NOT EXISTS goal_conflicts (
    conflict_id TEXT PRIMARY KEY,
    goal_ids_json TEXT NOT NULL,  -- JSON array of goal_ids

    conflict_type TEXT NOT NULL CHECK(conflict_type IN ('resource','prerequisite','exclusive','sequential')),
    severity TEXT NOT NULL CHECK(severity IN ('low','medium','high')),
    resolution_strategy TEXT NOT NULL,

    detected_at TEXT NOT NULL,
    resolved_at TEXT,

    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_goal_conflicts_detected
    ON goal_conflicts(detected_at);

-- ============================================================================
-- Strategic Layer: Interest Portfolio
-- ============================================================================

CREATE TABLE IF NOT EXISTS interest_portfolio (
    portfolio_id TEXT PRIMARY KEY DEFAULT 'default',

    -- Structure (JSON arrays of trait_ids)
    core_interests_json TEXT NOT NULL,
    satellite_interests_json TEXT NOT NULL,
    exploratory_interests_json TEXT NOT NULL,

    -- Characteristics
    diversity_score REAL NOT NULL DEFAULT 0.0,
    balance_score REAL NOT NULL DEFAULT 0.0,
    depth_distribution_json TEXT NOT NULL,  -- JSON: {depth: count}

    -- Parameters
    rebalance_threshold REAL NOT NULL DEFAULT 0.3,
    max_core_interests INTEGER NOT NULL DEFAULT 5,
    max_satellite_interests INTEGER NOT NULL DEFAULT 10,

    last_rebalanced_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ============================================================================
-- Strategic Layer: Portfolio Changes History
-- ============================================================================

CREATE TABLE IF NOT EXISTS portfolio_changes (
    change_id TEXT PRIMARY KEY,

    change_type TEXT NOT NULL CHECK(change_type IN ('promotion','demotion','removal')),
    trait_id TEXT NOT NULL,
    from_tier TEXT NOT NULL CHECK(from_tier IN ('core','satellite','exploratory','none')),
    to_tier TEXT NOT NULL CHECK(to_tier IN ('core','satellite','exploratory','removed')),
    reason TEXT NOT NULL,

    created_at TEXT NOT NULL,

    FOREIGN KEY (trait_id) REFERENCES traits(trait_id)
);

CREATE INDEX IF NOT EXISTS idx_portfolio_changes_created
    ON portfolio_changes(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_portfolio_changes_trait
    ON portfolio_changes(trait_id);

-- ============================================================================
-- Strategic Layer: Self-Reflection Insights
-- ============================================================================

CREATE TABLE IF NOT EXISTS reflection_insights (
    reflection_id TEXT PRIMARY KEY,

    reflection_type TEXT NOT NULL CHECK(reflection_type IN ('progress','pattern','adjustment','discovery')),

    -- Observation
    observation TEXT NOT NULL,
    evidence_json TEXT NOT NULL,  -- JSON array of event_ids

    -- Analysis
    interpretation TEXT NOT NULL,
    confidence REAL NOT NULL,

    -- Action
    suggested_action TEXT,
    action_taken TEXT,

    created_at TEXT NOT NULL,
    reviewed_by_owner INTEGER NOT NULL DEFAULT 0,

    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reflection_insights_created
    ON reflection_insights(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reflection_insights_type
    ON reflection_insights(reflection_type);
CREATE INDEX IF NOT EXISTS idx_reflection_insights_reviewed
    ON reflection_insights(reviewed_by_owner, created_at DESC);
