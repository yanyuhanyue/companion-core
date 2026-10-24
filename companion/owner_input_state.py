"""Read-only original input completion receipts for stopped Bridge custody."""

import json

import companion_delivery_protocol as wire
from companion import owner_authority, owner_input, poke
from companion.contracts import CoreError
from companion.poke_protocol import OwnerPokeCapture


def read(store, settings, capture):
    with store.transaction():
        native_poke = isinstance(capture, OwnerPokeCapture)
        if native_poke:
            original, _, source = poke.verify_original(
                store, settings, capture.model_dump(mode="json"))
            input_hash = source["notice_sha256"]
        else:
            original, _ = owner_input.verify(store, settings, capture.model_dump(mode="json"))
            input_hash = original.input_sha256
        event_id = original.event_id
        erased = bool(store.one("SELECT 1 FROM runtime_meta WHERE key=?",
                                 ("erased_event:" + event_id,)))
        event = store.one("SELECT event_id,redacted,fingerprint,reply_revision "
                          "FROM events WHERE event_id=?",
                          (event_id,))
        revision = event.pop("reply_revision") if event else 0
        saved = store.one("SELECT value FROM runtime_meta WHERE key=?",
                          ((poke.INPUT if native_poke else owner_input.INPUT) + event_id,))
        head = owner_authority._latest(store)
        ref = original.authority
        stopped = bool(head and head["state"] == "STOPPED"
            and owner_authority.reference(head).as_dict() == ref | {"revision": ref["revision"] + 1}
            and head["restore_generation"] == owner_authority._meta(
                store, owner_authority.GENERATION))
        unadmitted = bool(stopped and event is None)
        if native_poke and event and not erased:
            poke.original_source(store, settings, event_id)
        saved_capture = json.loads(saved["value"]) if saved else {}
        original_value = original.model_dump(mode="json")
        matches = (saved_capture.get("capture_sha256") == wire.digest(original_value)
                   if native_poke else saved_capture.get("capture") == original_value)
        if not erased and (saved and not matches or not saved and not unadmitted):
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
            ("voice_jobs", "job_id", {"QUEUED", "LEASED", "RUNNING", "UNKNOWN"}),
            ("bili_video_items", "item_id", {"DECLARED", "PREPARED", "ANALYZED", "UNKNOWN"}),
        ):
            field = "event_id" if table == "voice_jobs" else "owner_event_id"
            cleanup = ",cleanup_state" if table == "voice_jobs" else ""
            extra = store.rows(f"SELECT {identity} AS id,state{cleanup} FROM {table} "
                               f"WHERE {field}=? ORDER BY {identity} LIMIT ?",
                               (event_id, maximum + 1))
            reads.extend({**item, "active": item["state"] in states
                          or item.get("cleanup_state") == "PENDING"} for item in extra)
        complete = all(len(items) <= maximum for items in (jobs, models, deliveries, reads, tools))
        unsettled_work = any(item["state"] not in wire.JOB_TERMINAL
                             for item in jobs + tools) or any(
            item.get("active", item["state"] in {
                "WAITING", "QUEUED", "FETCHING", "RUNNING", "LEASED", "UNKNOWN"})
                for item in reads)
        unsettled_model = any(item["state"] != "SETTLED" for item in models)
        unsettled_delivery = any(item["state"] not in wire.DELIVERY_TERMINAL for item in deliveries)
        primary = [job for job in jobs if job["kind"] == ("poke" if native_poke else "chat")]
        bound = bool(len(primary) == 1
                     and json.loads(primary[0]["authority_json"]) == original.authority)
        suppressed = bool(native_poke and event and saved and not jobs and not deliveries)
        terminal = bool(complete and (bound or erased or suppressed
            or unadmitted and not jobs and not deliveries)
            and not (
            unsettled_work or unsettled_model or unsettled_delivery))
        return {"authority": original.authority, "event_id": event_id,
            "reply_revision": revision,
            "input_sha256": input_hash, "terminal": terminal,
            "state": "RECALLED" if erased or event and event["redacted"] else (
                "CANCELLED" if unadmitted and terminal else "COMPLETE" if terminal else "PENDING"),
            "unsettled_work": unsettled_work, "unsettled_model": unsettled_model,
            "unsettled_delivery": unsettled_delivery,
            "work_sha256": wire.digest({"event": event, "jobs": jobs, "models": models,
                                        "deliveries": deliveries, "reads": reads, "tools": tools})}
