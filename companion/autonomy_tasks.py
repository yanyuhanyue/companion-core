"""
Scheduled tasks for autonomy system: portfolio rebalancing and self-reflection.

These tasks should be run periodically to maintain long-term autonomy:
- Portfolio rebalancing: weekly
- Daily reflection: daily
- Weekly reflection: weekly
- Monthly reflection: monthly
"""

from datetime import datetime


def run_portfolio_rebalancing(store, settings, clock):
    """
    Weekly portfolio rebalancing task.

    Promotes/demotes interests based on activity patterns.
    """
    if not settings.life_loop.autonomy_enabled:
        return {"status": "disabled"}

    from companion.life_autonomy import LifeAutonomy

    autonomy = LifeAutonomy(store, settings, clock)
    result = autonomy.run_portfolio_rebalancing()

    # Log result
    print(f"[Autonomy] Portfolio rebalancing: {result}")

    return result


def run_daily_reflection(store, settings, clock):
    """
    Daily self-reflection task.

    Generates progress and pattern insights.
    """
    if not settings.life_loop.autonomy_enabled:
        return {"status": "disabled"}

    from companion.life_autonomy import LifeAutonomy

    autonomy = LifeAutonomy(store, settings, clock)
    insights = autonomy.run_self_reflection(period="daily")

    # Log insights
    print(f"[Autonomy] Daily reflection: {len(insights)} insights generated")

    return {"insights_count": len(insights), "insights": insights}


def run_weekly_reflection(store, settings, clock):
    """
    Weekly self-reflection task.

    Generates progress, patterns, and adjustment suggestions.
    """
    if not settings.life_loop.autonomy_enabled:
        return {"status": "disabled"}

    from companion.life_autonomy import LifeAutonomy

    autonomy = LifeAutonomy(store, settings, clock)
    insights = autonomy.run_self_reflection(period="weekly")

    # Log insights
    print(f"[Autonomy] Weekly reflection: {len(insights)} insights generated")

    return {"insights_count": len(insights), "insights": insights}


def run_monthly_reflection(store, settings, clock):
    """
    Monthly self-reflection task.

    Generates comprehensive insights including discoveries.
    """
    if not settings.life_loop.autonomy_enabled:
        return {"status": "disabled"}

    from companion.life_autonomy import LifeAutonomy

    autonomy = LifeAutonomy(store, settings, clock)
    insights = autonomy.run_self_reflection(period="monthly")

    # Log insights
    print(f"[Autonomy] Monthly reflection: {len(insights)} insights generated")

    return {"insights_count": len(insights), "insights": insights}


def initialize_autonomy_schema(store):
    """
    Initialize autonomy database schema.

    Should be called on application startup.
    """
    from companion.autonomy_store import AutonomyStore

    autonomy_store = AutonomyStore(store)
    autonomy_store.initialize_schema()

    print("[Autonomy] Schema initialized")


# Cron schedule suggestions:
# Portfolio rebalancing: "0 2 * * 1"  (Every Monday at 2am)
# Daily reflection: "0 1 * * *"       (Every day at 1am)
# Weekly reflection: "0 3 * * 0"      (Every Sunday at 3am)
# Monthly reflection: "0 4 1 * *"     (First day of month at 4am)
