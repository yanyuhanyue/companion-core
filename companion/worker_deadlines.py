"""Read-only deadline projections for native domains; no claims or recovery writes."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

JOBS = (
    ("jobs", "next_attempt_at", "state IN ('QUEUED','RETRY_WAIT')"),
    ("jobs", "lease_until", "state='RUNNING'"),
)
DELIVERIES = (("outbox", "lease_until", "state IN ('LEASED','SENDING')"),)
SOCIAL = (("social_autonomy_plans", "expires_at", "state IN ('READY','PLANNING')"),)
CONTENT = (
    ("content_items", "lease_until", "state IN ('FETCHING','RUNNING')"),
    ("content_items", "deadline", "state IN ('WAITING','QUEUED','FETCHING','RUNNING')"),
)
TOOLS = (("tool_tasks", "lease_until", "state IN ('LEASED','RUNNING')"),)
DIARY = (
    ("diary_runs", "next_attempt_at", "state IN ('PENDING','RETRY_WAIT')"),
    ("diary_runs", "lease_until", "state='RUNNING'"),
)
LIFE = (
    ("consolidation_runs", "next_attempt_at", "state='PARTIAL'"),
    ("consolidation_runs", "lease_until", "state='LEASED'"),
    ("reflection_runs", "lease_until", "state='RUNNING'"),
    ("goal_steps", "lease_until", "state='RUNNING'"),
    ("goals", "next_due_at", "state IN ('ACCEPTED','QUEUED','RETRY_WAIT')"),
    ("commitments", "deadline", "state IN ('ACKNOWLEDGED','IN_PROGRESS')"),
)


def _next(store, settings, scan_started_at, boundaries, *, local_hour=None):
    # Use the scan horizon, not wait entry: deadlines crossed during native work
    # need one rescan. Older fenced rows must not produce an immediate busy loop.
    parts = [f"SELECT julianday({field}) AS at FROM {table} WHERE {condition}"
             for table, field, condition in boundaries]
    parts.append("SELECT julianday(a.lease_until) AS at FROM model_admissions a "
                 "JOIN model_usage m USING(request_id) WHERE m.state='RESERVED'")
    row = store.one("SELECT MIN(at) AS at FROM (" + " UNION ALL ".join(parts)
                    + ") WHERE at>=julianday(?)", (scan_started_at.isoformat(),))
    dates = ([datetime.fromtimestamp((row["at"] - 2440587.5) * 86400 + 0.002, UTC)]
             if row["at"] else [])
    local = scan_started_at.astimezone(ZoneInfo(settings.timezone))
    dates.append((local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0).astimezone(UTC))
    if local_hour is not None:
        due = local.replace(hour=local_hour, minute=0, second=0, microsecond=0)
        if due <= local:
            due += timedelta(days=1)
        dates.append(due.astimezone(UTC))
    if settings.live_text_window:
        end = settings.live_text_window.ends_at
        if end > scan_started_at:
            dates.append(end)
    return min(dates)


def chat(store, settings, scan_started_at):
    return _next(store, settings, scan_started_at, JOBS + DELIVERIES + SOCIAL)


def content(store, settings, scan_started_at):
    return _next(store, settings, scan_started_at, CONTENT)


def executive(store, settings, scan_started_at):
    return _next(store, settings, scan_started_at, TOOLS)


def diary(store, settings, scan_started_at):
    return _next(store, settings, scan_started_at, DIARY,
                 local_hour=settings.diary_generate_hour)


def life(store, settings, scan_started_at):
    return _next(store, settings, scan_started_at, LIFE,
                 local_hour=settings.consolidation_local_hour)


PROVIDERS = {
    "worker": chat,
    "content-worker": content,
    "executive-worker": executive,
    "diary-worker": diary,
    "life-worker": life,
}
