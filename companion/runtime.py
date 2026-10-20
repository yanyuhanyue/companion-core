"""Shared durable state and identity bootstrap, independent of semantic services."""

from companion.contracts import CoreError
from companion.privacy import ErasureLedger
from companion.store import Store, after, utcnow


class Runtime:
    def __init__(self, settings, clock=utcnow):
        self.settings, self.clock = settings, clock
        self.store = Store(settings.database)
        try:
            self.ledger = ErasureLedger(settings.erasure_ledger)
            self.ledger.initialize(self.store)
            with self.store.transaction():
                for actor, kind in (
                    ("owner", "owner"),
                    ("companion", "companion"),
                    ("system", "system"),
                ):
                    self.store.execute(
                        "INSERT OR IGNORE INTO principals VALUES (?,?,?)",
                        (actor, kind, self.clock()),
                    )
                # A persistent identity cannot silently be reassigned to a new QQ owner.
                previous = self.store.one("SELECT value FROM runtime_meta WHERE key='owner_qq_id'")
                if previous and previous["value"] != settings.owner_qq_id:
                    raise CoreError("OWNER_IDENTITY_MISMATCH", 503)
                self.store.execute(
                    "INSERT OR IGNORE INTO runtime_meta VALUES ('owner_qq_id',?)",
                    (settings.owner_qq_id,),
                )
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def close(self):
        self.store.close()

    def expire_raw_events(self):
        now = self.clock()
        with self.store.transaction():
            rows = self.store.rows(
                "SELECT event_id FROM events WHERE redacted=0 AND ingested_at<? "
                "ORDER BY rowid LIMIT 1000",
                (after(now, -86400 * self.settings.raw_event_retention_days),),
            )
            if rows:
                self.ledger.expire_events(self.store, [e["event_id"] for e in rows], now)
            return len(rows)
