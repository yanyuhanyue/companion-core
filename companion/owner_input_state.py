"""Read-only original input completion receipts for stopped Bridge custody."""

import json

import companion_delivery_protocol as wire
from companion import owner_authority, owner_input
from companion.contracts import CoreError


def read(store, settings, capture):
    with store.transaction():
        original, _ = owner_input.verify(store, settings, capture.model_dump(mode="json"))
        event_id = original.event_id
        erased = bool(store.one("SELECT 1 FROM runtime_meta WHERE key=?",
                                 ("erased_event:" + event_id,)))
        event = store.one("SELECT event_id,redacted,fingerprint FROM events WHERE event_id=?",
                          (event_id,))
        saved = store.one("SELECT value FROM runtime_meta WHERE key=?",
                          (owner_input.INPUT + event_id,))
        head = owner_authority._latest(store)
        ref = original.authority
        stopped = bool(head and head["state"] == "STOPPED"
            and owner_authority.reference(head).as_dict() == ref | {"revision": ref["revision"] + 1}
            and head["restore_generation"] == owner_authority._meta(
                store, owner_authority.GENERATION))
        unadmitted = bool(stopped and event is None)
        if not erased and (saved and json.loads(saved["value"]).get("capture")
                           != original.model_dump(mode="json") or not saved and not unadmitted):
            raise CoreError("OWNER_APPROVED_INPUT_REQUIRED", 404)
        maximum = wire.MAX_INPUT_DELIVERIES
        jobs = store.rows("SELECT j.job_id,j.kind,j.state,j.authority_json FROM jobs j "
            "WHERE j.event_id=? OR EXISTS (SELECT 1 FROM job_sources s "
            "WHERE s.job_id=j.job_id AND s.event_id=?) ORDER BY j.job_id LIMIT ?",
            (event_id, event_id, maximum + 1))
        models = store.rows("SELECT request_id,state FROM model_usage WHERE job_id IN "
            "(SELECT j.job_id FROM jobs j WHERE j.event_id=? OR EXISTS "
            "(SELECT 1 FROM job_sources s WHERE s.job_id=j.job_id AND s.event_id=?)) "
            "ORDER BY request_id LIMIT ?", (event_id, event_id, maximum + 1))
        deliveries = store.rows("SELECT message_id,state,attempt_count,authority_json FROM outbox "
            "WHERE origin_event_id=? ORDER BY message_id LIMIT ?", (event_id, maximum + 1))
        reads = store.rows("SELECT item_id AS id,state FROM content_items "
            "WHERE owner_event_id=? ORDER BY item_id LIMIT ?", (event_id, maximum + 1))
        reads.extend(store.rows("SELECT request_id AS id,state FROM exploration_requests WHERE "
            "request_id IN (SELECT read_request_id FROM content_items WHERE owner_event_id=?) "
            "ORDER BY request_id LIMIT ?", (event_id, maximum + 1)))
        tools = store.rows("SELECT t.task_id,t.state FROM tool_tasks t JOIN tool_evidence e "
            "USING(task_id) WHERE e.event_id=? ORDER BY t.task_id LIMIT ?",
            (event_id, maximum + 1))
        for table, identity, states in (
            ("voice_jobs", "job_id", {"QUEUED", "LEASED", "RUNNING"}),
            ("bili_video_items", "item_id", {"DECLARED", "PREPARED", "ANALYZED"}),
        ):
            field = "event_id" if table == "voice_jobs" else "owner_event_id"
            extra = store.rows(f"SELECT {identity} AS id,state FROM {table} "
                               f"WHERE {field}=? ORDER BY {identity} LIMIT ?",
                               (event_id, maximum + 1))
            reads.extend({**item, "active": item["state"] in states} for item in extra)
        complete = all(len(items) <= maximum for items in (jobs, models, deliveries, reads, tools))
        unsettled_work = any(item["state"] not in wire.JOB_TERMINAL
                             for item in jobs + tools) or any(
            item.get("active", item["state"] in {
                "WAITING", "QUEUED", "FETCHING", "RUNNING", "LEASED"}) for item in reads)
        unsettled_model = any(item["state"] != "SETTLED" for item in models)
        unsettled_delivery = any(item["state"] not in wire.DELIVERY_TERMINAL for item in deliveries)
        primary = [job for job in jobs if job["kind"] == "chat"]
        bound = bool(len(primary) == 1
                     and json.loads(primary[0]["authority_json"]) == original.authority)
        terminal = bool(complete and (bound or erased or unadmitted and not jobs and not deliveries)
            and not (
            unsettled_work or unsettled_model or unsettled_delivery))
        return {"authority": original.authority, "event_id": event_id,
            "input_sha256": original.input_sha256, "terminal": terminal,
            "state": "RECALLED" if erased or event and event["redacted"] else (
                "CANCELLED" if unadmitted and terminal else "COMPLETE" if terminal else "PENDING"),
            "unsettled_work": unsettled_work, "unsettled_model": unsettled_model,
            "unsettled_delivery": unsettled_delivery,
            "work_sha256": wire.digest({"event": event, "jobs": jobs, "models": models,
                                        "deliveries": deliveries, "reads": reads, "tools": tools})}
