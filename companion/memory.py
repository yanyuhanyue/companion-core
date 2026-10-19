import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from companion.contracts import CoreError
from companion.privacy import redact, sensitive
from companion.store import encode, uid


class Memory:
    def __init__(self, store, settings, ledger):
        self.store, self.settings, self.ledger = store, settings, ledger

    def require_owner(self, actor):
        if actor != "owner" or not self.settings.memory_enabled:
            raise CoreError("MEMORY_ACCESS_DENIED", 403)

    def search(self, query, actor="owner", subjects=(), limit=6):
        self.require_owner(actor)
        s = self.store
        # Missing evidence must never remain confidently retrievable.
        s.execute(
            "UPDATE memories SET confidence=0,needs_review=1,status='HYPOTHESIS' "
            "WHERE status IN ('CONFIRMED','HYPOTHESIS') AND NOT EXISTS "
            "(SELECT 1 FROM memory_evidence m JOIN events e ON e.event_id=m.event_id "
            "WHERE m.memory_id=memories.memory_id AND e.redacted=0)"
        )
        base = (
            "SELECT m.* FROM memories m WHERE m.status IN ('CONFIRMED','HYPOTHESIS') "
            "AND m.privacy_scope='private' AND m.confidence>0 AND EXISTS "
            "(SELECT 1 FROM memory_evidence v JOIN events e ON e.event_id=v.event_id "
            "WHERE v.memory_id=m.memory_id AND e.redacted=0 AND e.actor_id='owner') "
        )
        params = []
        if subjects:
            base += "AND m.subject IN (" + ",".join("?" for _ in subjects) + ") "
            params.extend(subjects)
        if query != "*":
            if len(query) < 3:
                base += "AND instr(m.content,?)>0 "
                params.append(query)
            else:
                terms = re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]+", query)
                grams = []
                for term in terms:
                    if re.search(r"[\u4e00-\u9fff]", term):
                        grams.extend(term[i : i + 3] for i in range(len(term) - 2))
                    else:
                        grams.append(term)
                grams = [term for term in dict.fromkeys(grams) if len(term) >= 3][:40]
                if not grams:
                    return []
                match = " OR ".join('"' + term.replace('"', '""') + '"' for term in grams)
                base += (
                    "AND m.memory_id IN (SELECT memory_id FROM memories_fts "
                    "WHERE memories_fts MATCH ?) "
                )
                params.append(match)
        base += (
            "ORDER BY instr(m.content,?)>0 DESC,m.needs_review,"
            "m.status='CONFIRMED' DESC,m.updated_at DESC LIMIT ?"
        )
        params.append(query)
        params.append(limit)
        rows = s.rows(base, params)
        for row in rows:
            row["evidence_ids"] = [
                e["event_id"]
                for e in s.rows(
                    "SELECT v.event_id FROM memory_evidence v "
                    "JOIN events e ON e.event_id=v.event_id "
                    "WHERE v.memory_id=? AND e.redacted=0",
                    (row["memory_id"],),
                )
            ]
        return rows

    def add(self, candidate, now, confirmed=False):
        s = self.store
        event = s.one(
            "SELECT * FROM events WHERE event_id=? AND redacted=0 AND actor_id='owner'",
            (candidate.evidence_event_id,),
        )
        if not event:
            raise CoreError("MISSING_MEMORY_EVIDENCE")
        body = json.loads(event["body_json"])
        text = body.get("text", "")
        if candidate.evidence_quote not in text:
            raise CoreError("UNSUPPORTED_MEMORY_EVIDENCE")
        if not confirmed and sensitive(text + candidate.content):
            return None
        if candidate.subject == "self" and not re.search(r"你(?:最近|经常|总是|这几次|刚才)", text):
            return None
        content = redact(candidate.content, self.settings)
        if "[REDACTED]" in content:
            return None
        same = s.one(
            "SELECT * FROM memories WHERE content=? AND subject=? AND status IN "
            "('HYPOTHESIS','CONFIRMED')",
            (content, candidate.subject),
        )
        if same:
            memory_id = same["memory_id"]
        else:
            conflict = s.one(
                "SELECT memory_id FROM memories WHERE subject=? AND fact_key=? "
                "AND status IN ('HYPOTHESIS','CONFIRMED') AND content<>?",
                (candidate.subject, candidate.fact_key, content),
            )
            memory_id = uid("mem")
            s.insert(
                "memories",
                memory_id=memory_id,
                subject=candidate.subject,
                kind=candidate.kind,
                content=content,
                status="CONFIRMED" if confirmed else "HYPOTHESIS",
                origin="user_confirmed" if confirmed else "model_inferred",
                confidence=1.0 if confirmed else 0.5,
                importance=0.5,
                privacy_scope="private",
                created_at=now,
                updated_at=now,
                fact_key=candidate.fact_key,
                needs_review=int(bool(conflict)),
            )
            if conflict:
                s.execute(
                    "UPDATE memories SET needs_review=1 WHERE memory_id=?", (conflict["memory_id"],)
                )
                s.audit("memory.conflict", memory_id, event["trace_id"], now)
        s.execute(
            "INSERT OR IGNORE INTO memory_evidence VALUES (?,?,1,?)",
            (memory_id, event["event_id"], now),
        )
        if candidate.subject == "self":
            self.observe_trait(candidate.fact_key, event, now)
        return memory_id

    def observe_trait(self, topic, event, now):
        s = self.store
        trait = s.one("SELECT * FROM traits WHERE category='style' AND topic=?", (topic,))
        trait_id = trait["trait_id"] if trait else uid("trait")
        if not trait:
            s.insert(
                "traits",
                trait_id=trait_id,
                category="style",
                topic=topic,
                strength=0.2,
                confidence=0.3,
                status="EXPLORING",
                updated_at=now,
            )
        s.execute(
            "INSERT OR IGNORE INTO trait_evidence VALUES (?,?,1,0.5,?)",
            (trait_id, event["event_id"], event["occurred_at"]),
        )
        evidence = s.rows(
            "SELECT e.conversation_id,e.occurred_at FROM trait_evidence t "
            "JOIN events e ON e.event_id=t.event_id WHERE t.trait_id=? "
            "AND e.redacted=0 AND e.actor_id='owner'",
            (trait_id,),
        )
        sessions, days = set(), set()
        for item in evidence:
            dt = datetime.fromisoformat(item["occurred_at"]).astimezone(
                ZoneInfo(self.settings.timezone)
            )
            sessions.add((item["conversation_id"], int(dt.timestamp()) // 1800))
            days.add(dt.date())
        conflict = s.one(
            "SELECT 1 FROM memories WHERE subject='self' AND fact_key=? AND needs_review=1",
            (topic,),
        )
        state = "EXPLORING"
        if len(sessions) >= self.settings.emerging_min_sessions:
            state = "EMERGING"
        if state == "EMERGING" and len(days) >= self.settings.stable_min_days and not conflict:
            state = "STABLE"
        if conflict:
            state = "FADING"
        s.execute(
            "UPDATE traits SET status=?,independent_sessions=?,independent_days=?,"
            "strength=?,confidence=?,last_evidence_at=?,updated_at=?,version=version+1 "
            "WHERE trait_id=?",
            (
                state,
                len(sessions),
                len(days),
                min(0.9, len(days) / 10),
                min(0.9, 0.3 + len(sessions) / 20),
                now,
                now,
                trait_id,
            ),
        )

    def decide(self, memory_id, decision, now, corrected=None):
        self.require_owner("owner")
        s = self.store
        with s.transaction():
            old = s.one("SELECT * FROM memories WHERE memory_id=?", (memory_id,))
            if not old:
                raise CoreError("MEMORY_NOT_FOUND", 404)
            if decision == "forget":
                if old["status"] != "FORGOTTEN":
                    self.ledger.append(s, memory_id, now)
                return {"memory_id": memory_id, "state": "FORGOTTEN"}
            if old["status"] not in {"HYPOTHESIS", "CONFIRMED"}:
                raise CoreError("MEMORY_NOT_ACTIVE")
            if decision == "confirm":
                s.execute(
                    "UPDATE memories SET status='CONFIRMED',confidence=1,"
                    "origin='user_confirmed',updated_at=? WHERE memory_id=?",
                    (now, memory_id),
                )
                return {"memory_id": memory_id, "state": "CONFIRMED"}
            if not corrected or not corrected.strip():
                raise CoreError("CORRECTION_REQUIRED", 400)
            if redact(corrected, self.settings) != corrected:
                raise CoreError("CREDENTIAL_MEMORY_DENIED", 400)
            new_id = uid("mem")
            s.insert(
                "memory_revisions",
                revision_id=uid("rev"),
                memory_id=memory_id,
                revision_reason="owner_correction",
                prior_status=old["status"],
                prior_content=old["content"],
                changed_by="owner",
                created_at=now,
            )
            s.execute(
                "UPDATE memories SET status='SUPERSEDED',updated_at=? WHERE memory_id=?",
                (now, memory_id),
            )
            s.insert(
                "memories",
                memory_id=new_id,
                subject=old["subject"],
                kind=old["kind"],
                content=corrected,
                status="CONFIRMED",
                origin="user_confirmed",
                confidence=1.0,
                importance=old["importance"],
                privacy_scope="private",
                supersedes_memory_id=memory_id,
                created_at=now,
                updated_at=now,
                fact_key=old["fact_key"],
            )
            # Correction is itself an authenticated owner event; original evidence is retained.
            evidence = s.one(
                "SELECT e.* FROM memory_evidence m JOIN events e ON e.event_id=m.event_id "
                "WHERE m.memory_id=? LIMIT 1",
                (memory_id,),
            )
            if not evidence:
                raise CoreError("MISSING_MEMORY_EVIDENCE")
            event_id = uid("evt")
            s.insert(
                "events",
                event_id=event_id,
                schema_version=1,
                source="owner.decision",
                source_event_id=event_id,
                conversation_id=evidence["conversation_id"],
                actor_id="owner",
                event_type="memory.corrected",
                occurred_at=now,
                ingested_at=now,
                privacy_scope="private",
                body_json=encode({"text": corrected}),
                trace_id=event_id,
                memory_processed=1,
            )
            s.insert(
                "memory_evidence", memory_id=new_id, event_id=event_id, polarity=1, created_at=now
            )
            s.execute("DELETE FROM conversation_summaries")
            s.execute("UPDATE episodes SET summary='',status='REDACTED'")
            s.execute(
                "DELETE FROM trait_evidence WHERE trait_id IN "
                "(SELECT trait_id FROM traits WHERE topic=?)",
                (old["fact_key"],),
            )
            s.execute(
                "UPDATE traits SET status='FADING',confidence=0 WHERE topic=?", (old["fact_key"],)
            )
            return {"memory_id": new_id, "state": "CONFIRMED", "supersedes": memory_id}

    def episodes(self, events, now):
        s = self.store
        episode_id = uid("episode")
        excerpts = [json.loads(e["body_json"]).get("text", "")[:100] for e in events]
        summary = "资料摘录（不可信观察）：\n" + "\n".join(excerpts)
        s.insert(
            "episodes",
            episode_id=episode_id,
            title="QQ 对话片段",
            summary=summary,
            conversation_id=events[0]["conversation_id"],
            privacy_scope="private",
            started_at=events[0]["occurred_at"],
            ended_at=events[-1]["occurred_at"],
            status="ACTIVE",
            extractor_version="v1-extractive",
            created_at=now,
        )
        for event in events:
            s.insert("episode_events", episode_id=episode_id, event_id=event["event_id"])
            s.execute("UPDATE events SET memory_processed=1 WHERE event_id=?", (event["event_id"],))
        s.insert(
            "conversation_summaries",
            summary_id=uid("summary"),
            conversation_id=events[0]["conversation_id"],
            first_event_id=events[0]["event_id"],
            last_event_id=events[-1]["event_id"],
            summary_text=summary,
            summarizer_version="v1-extractive",
            created_at=now,
        )
