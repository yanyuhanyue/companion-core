"""Stopped input retirement retains usage and never claims a delivery ACK."""

import json

import httpx
import pytest

from companion.store import encode, uid
from tests.conftest import run
from tests.test_onebot_capture import capture_approval, source_window
from tests.test_onebot_direct import raw_event
from tests.test_private_companion import companion_options


@pytest.mark.parametrize("mutation", [None, "model", "outbox", "source", "active", "proof"])
def test_stopped_cancelled_input_requires_native_no_attempt_proof(core, tmp_path, mutation):
    async def source(ws):
        await ws.send(json.dumps(raw_event()))
        await ws.wait_closed()

    async def scenario():
        async with source_window(core, tmp_path, source, live=True, approved=True,
                                 scope_options=companion_options(core)) as (
            window, client, protocol, _, sends,
        ):
            await window.capture_once(client, protocol)
            await window.approve_captured(client, capture_approval(window))
            event_id=core.store.one("SELECT event_id FROM jobs")["event_id"]
            if mutation != "active":
                await window.stop_live(client)
            if mutation == "source":
                core.store.execute("UPDATE events SET body_json=? WHERE event_id=?",
                                   (encode({"text":"changed"}),event_id))
            if mutation in {"model", "outbox"}:
                job=core.store.one("SELECT * FROM jobs")
                if mutation == "model":
                    core.store.insert('model_usage',usage_id=uid('usage'),request_id=uid('model'),
                        job_id=job['job_id'],scenario='chat',provider='mock',model='synthetic',
                        input_tokens=0,output_tokens=0,billed_micro_usd=1,
                        state='SETTLED',occurred_at=core.clock())
                else:
                    core.store.insert('outbox',message_id=uid('msg'),origin_event_id=event_id,
                        conversation_id=core.store.one('SELECT conversation_id FROM events')[
                            'conversation_id'],action_type='qq.private.reply',
                        content_json='{}',content_sha256='a'*64,delivery_key='synthetic',
                        state='FAILED',attempt_count=1,created_at=core.clock(),updated_at=core.clock())
            if mutation == "proof":
                original=window.core_post

                async def changed(*args):
                    result=await original(*args)
                    return {**result,'input_sha256':'0'*64}

                window.core_post=changed
            before=window.row()['journal_head']
            if mutation:
                with pytest.raises((ValueError,httpx.HTTPStatusError)):
                    await window.reconcile_cancelled_inputs(client)
                assert window.row()['journal_head'] == before
                assert window.db.execute(
                    "SELECT state FROM window_inputs").fetchone()[0]=='APPROVED'
            else:
                proof=await client.post('/internal/v1/live-text/inputs/cancelled',json={
                    'window_sha256':window.plan.sha256,'event_id':event_id})
                assert proof.status_code==200,proof.json()
                result=await window.reconcile_cancelled_inputs(client)
                assert result['cancelled_inputs']==1 and result['state']=='STOPPED'
                assert window.db.execute(
                    "SELECT state FROM window_inputs").fetchone()[0]=='CANCELLED'
                assert window.status()['new_inputs']==1
                assert window.verify(active=False)['state']=='STOPPED'
                assert (await window.reconcile_cancelled_inputs(client))['cancelled_inputs']==0
                records,_=window.read_journal()
                assert any(r['kind']=='INPUT_CANCELLED' for r in records)
                assert not any(r['kind']=='ACK' for r in records)
                core.store.execute("UPDATE events SET body_json=? WHERE event_id=?",
                                   (encode({"text":"changed"}),event_id))
                proof=await client.post('/internal/v1/live-text/inputs/cancelled',json={
                    'window_sha256':window.plan.sha256,'event_id':event_id})
                assert proof.status_code==409
            assert sends==[] and core.model.calls==[]

    run(scenario())


@pytest.mark.parametrize("paid", [False, True])
def test_cancelled_input_proof_survives_handoff_and_cannot_be_forged_in_spool(tmp_path, paid):
    from companion_onebot_bridge.spool import Spool
    from companion_onebot_bridge.window import TestWindow, digest
    from tests.test_onebot_direct import normalized
    from tests.test_onebot_window import input_approval
    from tests.test_onebot_window_handoff import CoreContract, plan_for

    cfg, plan, now = plan_for(tmp_path)

    class Contract(CoreContract):
        def __call__(self, request):
            if request.url.path.endswith('/inputs/cancelled'):
                body=json.loads(request.content)
                proof = {**body,'input_sha256':digest('你好'),
                    'state':'CANCELLED','event_sha256':'a'*64,'job_sha256':'b'*64,
                    'privacy_epoch':0,'model_attempts':0,'send_attempts':0}
                if paid:
                    proof.update(model_attempts=1, settled_model_usage_sha256='c'*64,
                                 billed_micro_usd=295)
                return httpx.Response(200,json=proof)
            return super().__call__(request)

    contract=Contract(plan)

    async def scenario():
        with Spool(cfg) as spool:
            async with contract.client() as client:
                old=TestWindow(spool,plan,clock=lambda:now)
                await old.open(client)
                raw=raw_event()
                await old.ingest(client,raw,input_approval(plan,raw))
                # Native proof validation and authenticated receipts are exercised
                # above; this synthetic contract isolates Bridge succession.
                with spool.db:
                    spool.db.execute("UPDATE window_inputs SET capture_json='{}'")
                await old.stop_live(client)
                await old.reconcile_cancelled_inputs(client)
                _,new_plan,_=plan_for(tmp_path,2,predecessor=old)
                contract.successor=new_plan
                new=TestWindow(spool,new_plan,clock=lambda:now)
                await new.handoff(client)
                await new.open(client)
                assert new.verify()['state']=='OPEN'
                assert old.count('window_inputs')==1 and new.count('window_inputs')==0
                assert spool.db.execute(
                    "SELECT state FROM window_inputs").fetchone()[0]=='CANCELLED'
                event_id=normalized(raw,cfg)['event_id']
                with spool.db:
                    spool.db.execute("UPDATE window_inputs SET input_hash=? WHERE event_id=?",
                                     ('0'*64,event_id))
                with pytest.raises(ValueError,match='CANCELLED_INPUT_CORE_PROOF_REQUIRED'):
                    new.verify()

    run(scenario())
