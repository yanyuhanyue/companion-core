"""Closing messages may go unanswered; a lull after a lively chat may get one more line."""

import json

import httpx
import pytest

from companion import turn_taking
from companion.config import Settings
from companion.contracts import LeaseRequest
from companion.model import ModelResponse
from companion.transport import lease_deliveries
from companion.turn_taking import SILENCE_RULES, TurnTakingConfig
from tests.conftest import event, ingest, run
from tests.notice_owner_fixtures import run_notice
from tests.test_life_worker_integration import process_worker
from tests.test_onebot_capture import real_core_http
from tests.test_social_owner_authority import authorize


class Model:
    def __init__(self):
        self.calls, self.chat, self.activity = [], [], []

    async def complete(self, messages, scenario):
        self.calls.append((messages, scenario))
        queue = self.chat if scenario == "chat" else self.activity
        return ModelResponse(queue.pop(0) if queue else "嗯嗯，我也这么觉得。", 100, 20, 40)


def enable(core, **values):
    core.settings.turn_taking = TurnTakingConfig(**values)
    core.settings.proactive_enabled = True
    core.settings.quiet_start = core.settings.quiet_end = 0
    core.model = Model()


def turn(core, number, text, answer=None, *, deliver=True):
    """One owner message, its chat job, and (optionally) the delivered reply."""
    if answer is not None:
        core.model.chat.append(answer)
    result = ingest(core, number, text)
    assert run(core.run_once("worker"))
    if deliver:
        core.store.execute(
            "UPDATE outbox SET state='SENT',sent_at=? WHERE origin_event_id=? "
            "AND state='PENDING'", (core.clock(), result["event_id"]))
    return result


def system(core, index=-1):
    return core.model.calls[index][0][0]["content"]


def lease(core):
    return lease_deliveries(core.store, core.settings, LeaseRequest(worker_id="bridge"),
                            core.clock())["items"]


def outbox(core):
    return [json.loads(row["content_json"])["text"]
            for row in core.store.rows("SELECT content_json FROM outbox ORDER BY rowid")]


def test_defaults_are_on_and_stay_out_of_the_settings_dump():
    settings = Settings(owner_qq_id="10001", bot_qq_id="10002", bridge_token="x" * 32)
    assert settings.turn_taking.silence_enabled and settings.turn_taking.follow_up_enabled
    assert "turn_taking" not in settings.model_dump()


def test_a_short_closing_after_a_delivered_reply_may_go_unanswered(core):
    enable(core)
    turn(core, 1, "今天把报告交了", "辛苦啦，交完轻松多了。")
    core.clock.advance(20)
    result = turn(core, 2, "嗯嗯", turn_taking.SKIP)
    assert SILENCE_RULES in system(core)
    assert SILENCE_RULES not in system(core, 0)  # The first message had nothing to close.
    assert outbox(core) == ["辛苦啦，交完轻松多了。"]
    assert core.store.one("SELECT state FROM jobs WHERE job_id=?",
                          (result["job_id"],))["state"] == "SUCCEEDED"
    audit = core.store.one("SELECT safe_detail_json FROM audit_log WHERE action='job.succeeded' "
                           "ORDER BY rowid DESC LIMIT 1")
    assert json.loads(audit["safe_detail_json"])["reply_skipped"] is True


@pytest.mark.parametrize("text", ["那你呢？", "你觉得行吗", "我跟你说个事情吧好不好",
                                 "怎么弄", "帮我看看", "2+2", "我好难过", "还有个事"])
def test_questions_requests_and_new_content_are_always_answered(core, text):
    enable(core)
    turn(core, 1, "今天把报告交了", "辛苦啦。")
    core.clock.advance(20)
    turn(core, 2, text)
    assert SILENCE_RULES not in system(core)
    assert len(outbox(core)) == 2


def test_an_answer_to_the_bots_question_is_not_a_closing(core):
    enable(core)
    turn(core, 1, "今天把报告交了", "交完了吗？")
    core.clock.advance(20)
    turn(core, 2, "嗯")
    assert SILENCE_RULES not in system(core)


def test_a_skip_that_was_not_offered_fails_instead_of_being_sent(core):
    enable(core)
    result = turn(core, 1, "今天把报告交了", turn_taking.SKIP, deliver=False)
    assert not any(turn_taking.SKIP in text for text in outbox(core))
    job = core.store.one("SELECT state,last_error_code FROM jobs WHERE job_id=?",
                         (result["job_id"],))
    assert job["last_error_code"] == "TURN_SKIP_NOT_ALLOWED"
    assert job["state"] in {"FAILED", "RETRY_WAIT"}


def test_silence_never_repeats_and_respects_the_switch(core):
    enable(core)
    turn(core, 1, "今天把报告交了", "辛苦啦。")
    core.clock.advance(20)
    turn(core, 2, "嗯", turn_taking.SKIP)
    core.clock.advance(20)
    turn(core, 3, "好")
    assert SILENCE_RULES not in system(core)  # Previous owner message got no reply.
    enable(core, silence_enabled=False)
    turn(core, 4, "今天把报告交了", "辛苦啦。")
    core.clock.advance(20)
    turn(core, 5, "嗯")
    assert SILENCE_RULES not in system(core)


def lively(core):
    turn(core, 1, "我刚看完那部电影", "结局你喜欢吗？")
    core.clock.advance(60)
    return turn(core, 2, "还行，就是有点仓促", "我也觉得后半段太赶了。")


def test_a_lull_after_a_lively_chat_gets_one_follow_up(core):
    enable(core)
    lively(core)
    assert not turn_taking.flush(core.store, core.settings, core.clock())
    core.clock.advance(900)
    core.model.activity.append("对了，里面那个配角其实挺出彩的。")
    assert turn_taking.flush(core.store, core.settings, core.clock())
    assert not turn_taking.flush(core.store, core.settings, core.clock())
    assert run(run_notice(core))
    messages, scenario = core.model.calls[-1]
    assert scenario == "activity" and turn_taking.FOLLOW_UP_RULES in messages[0]["content"]
    payload = json.loads(messages[-1]["content"])
    assert payload["silent_minutes"] == 15
    owner = [item["owner"] for item in payload["turns"]]
    assert owner == ["我刚看完那部电影", "还行，就是有点仓促"]
    row = core.store.one("SELECT action_type FROM outbox ORDER BY rowid DESC")
    assert row["action_type"] == "qq.private.proactive"
    items = lease(core)  # The delivery recheck accepts the follow-up's own queued row.
    assert [item["content"] for item in items] == [{"text": "对了，里面那个配角其实挺出彩的。"}]
    core.store.execute("UPDATE outbox SET state='SENT',sent_at=? WHERE state='LEASED'",
                       (core.clock(),))
    core.clock.advance(900)
    assert not turn_taking.flush(core.store, core.settings, core.clock())  # No double text.


@pytest.mark.parametrize(("owner", "answer", "wording"), [
    ("还行，就是有点仓促", "我也觉得，尤其是那个配角，还有一点可以说。",
     "那个配角最后没有回头，我觉得比直接解释自己的选择更有劲。"),
    ("这个话题就说到这里吧", "好，这一段就到这里。", turn_taking.SKIP),
    ("我先去开个会", "好，你先忙。", turn_taking.SKIP),
    ("也可以聊点别的", "我想想。", "忽然想到，游戏里的钓鱼经常比主线还有吸引力。"),
    ("对，没有别的了", "好，说明白就行。", turn_taking.SKIP),
])
def test_same_timing_allows_content_handoff_new_topic_or_skip(core, owner, answer, wording):
    # These are simulated choices, not evidence that a live model judged them well.
    enable(core)
    turn(core, 1, "我刚看完那部电影", "结局你喜欢吗？")
    core.clock.advance(60)
    turn(core, 2, owner, answer)
    core.clock.advance(900)
    core.model.activity.append(wording)
    before = len(core.model.calls)
    assert turn_taking.flush(core.store, core.settings, core.clock())
    assert run(run_notice(core))
    assert len(core.model.calls) == before + 1
    messages, scenario = core.model.calls[-1]
    assert scenario == "activity"
    payload = json.loads(messages[-1]["content"])
    assert payload["silent_minutes"] == 15
    assert payload["turns"][-1] == {
        "owner": owner, "you": answer, "owner_at": core.store.one(
            "SELECT occurred_at FROM events WHERE event_id='evt_test_2'")["occurred_at"],
        "owner_truncated": False, "you_truncated": False,
    }
    assert not payload["excerpt_truncated"]
    items = lease(core)
    assert [item["content"]["text"] for item in items] == (
        [] if wording == turn_taking.SKIP else [wording])
    # A completed check, including SKIP, does not become a recurring planner.
    core.clock.advance(60)
    assert not turn_taking.flush(core.store, core.settings, core.clock())
    assert len(core.model.calls) == before + 1


@pytest.mark.parametrize("speaker", ["owner", "you"])
def test_long_turn_keeps_its_final_stance_and_marks_missing_middle(core, speaker):
    enable(core)
    turn(core, 1, "我刚看完那部电影", "结局你喜欢吗？")
    core.clock.advance(60)
    ending = "这段就说到这里，接下来要先忙别的了。"
    long = "说说电影里的那个配角。" + "中间还有不少细节。" * 60 + ending
    turn(core, 2, long if speaker == "owner" else "还行，就是有点仓促",
         long if speaker == "you" else "好，先忙你的。")
    core.clock.advance(900)
    core.model.activity.append(turn_taking.SKIP)
    assert turn_taking.flush(core.store, core.settings, core.clock())
    assert run(run_notice(core))
    payload = json.loads(core.model.calls[-1][0][-1]["content"])
    excerpt = payload["turns"][-1][speaker]
    assert excerpt.startswith("说说电影里的那个配角。") and excerpt.endswith(ending)
    assert len(excerpt) == turn_taking.TURN_CHARS and "…" in excerpt
    assert payload["turns"][-1][speaker + "_truncated"] and payload["excerpt_truncated"]
    assert lease(core) == []


def test_changing_omitted_text_still_withholds_the_follow_up(core):
    enable(core)
    turn(core, 1, "我刚看完那部电影", "结局你喜欢吗？")
    core.clock.advance(60)
    original = "电影里的细节。" * 80
    turn(core, 2, original, "还有一点可以聊。")
    core.clock.advance(900)
    core.model.activity.append("那个配角的选择比结局本身更有意思。")
    assert turn_taking.flush(core.store, core.settings, core.clock())
    assert run(run_notice(core))
    changed = original[:200] + "改" + original[201:]
    assert turn_taking._excerpt(original) == turn_taking._excerpt(changed)
    core.store.execute("UPDATE events SET body_json=json_set(body_json,'$.text',?) "
                       "WHERE event_id='evt_test_2'", (changed,))
    assert lease(core) == []


def test_the_owner_speaking_first_cancels_the_follow_up(core):
    enable(core)
    lively(core)
    core.clock.advance(900)
    assert turn_taking.flush(core.store, core.settings, core.clock())
    job = core.store.one("SELECT job_id FROM jobs WHERE kind='activity'")
    turn(core, 3, "我去洗澡了")
    calls = len(core.model.calls)
    run(core.run_once("notice", job_id=job["job_id"]))
    assert len(core.model.calls) == calls
    assert core.store.one("SELECT state FROM jobs WHERE job_id=?",
                          (job["job_id"],))["state"] == "CANCELLED"
    assert not core.store.rows("SELECT 1 FROM outbox WHERE action_type='qq.private.proactive'")


def test_the_owner_speaking_before_delivery_withholds_a_queued_follow_up(core):
    enable(core)
    lively(core)
    core.clock.advance(900)
    core.model.activity.append("对了，里面那个配角其实挺出彩的。")
    assert turn_taking.flush(core.store, core.settings, core.clock())
    assert run(run_notice(core))
    ingest(core, 3, "我回来了")
    assert not any(item["content"]["text"] == "对了，里面那个配角其实挺出彩的。"
                   for item in lease(core))


def test_a_model_skip_sends_nothing_and_the_daily_cap_holds(core):
    enable(core, follow_up_daily_checks=1)
    lively(core)
    core.clock.advance(900)
    core.model.activity.append(turn_taking.SKIP)
    assert turn_taking.flush(core.store, core.settings, core.clock())
    assert run(run_notice(core))
    assert not core.store.rows("SELECT 1 FROM outbox WHERE action_type='qq.private.proactive'")
    core.clock.advance(60)
    turn(core, 3, "刚才去倒水了", "好嘞。")
    core.clock.advance(60)
    turn(core, 4, "继续说电影", "那个配角挺出彩的。")
    core.clock.advance(900)
    assert not turn_taking.flush(core.store, core.settings, core.clock())


@pytest.mark.parametrize("case", ["quiet_chat", "closing", "too_late", "disabled", "paused"])
def test_no_follow_up_when_it_would_be_unwelcome(core, case):
    enable(core, follow_up_enabled=case != "disabled")
    if case == "quiet_chat":
        turn(core, 1, "我刚看完那部电影", "结局你喜欢吗？")
        core.clock.advance(3600)
        turn(core, 2, "还行", "我也觉得。")
    elif case == "closing":
        turn(core, 1, "我刚看完那部电影", "结局你喜欢吗？")
        core.clock.advance(60)
        turn(core, 2, "困了，晚安", "晚安～")
    else:
        lively(core)
    if case == "paused":
        core.store.execute("INSERT OR REPLACE INTO runtime_meta VALUES ('proactive_paused','1')")
    core.clock.advance(4000 if case == "too_late" else 900)
    assert not turn_taking.flush(core.store, core.settings, core.clock())


def test_erasing_a_turn_cancels_a_queued_follow_up(core):
    enable(core)
    first = lively(core)
    core.clock.advance(900)
    assert turn_taking.flush(core.store, core.settings, core.clock())
    job = core.store.one("SELECT j.job_id,j.event_id FROM jobs j WHERE kind='activity'")
    sources = {row["source_event_id"] for row in core.store.rows(
        "SELECT source_event_id FROM event_dependencies WHERE event_id=?", (job["event_id"],))}
    assert first["event_id"] in sources
    core.store.execute("UPDATE events SET redacted=1 WHERE event_id=?", (first["event_id"],))
    calls = len(core.model.calls)
    run(core.run_once("notice", job_id=job["job_id"]))
    assert len(core.model.calls) == calls
    assert not core.store.rows("SELECT 1 FROM outbox WHERE action_type='qq.private.proactive'")


def test_the_life_worker_selector_schedules_and_words_the_follow_up(core):
    from companion.autonomous_selector import native_selector
    from tests.test_autonomous_selector import drain

    enable(core)
    lively(core)
    core.clock.advance(900)
    core.model.activity.append("对了，里面那个配角其实挺出彩的。")
    turns = run(drain(native_selector(core, "life-test")))
    assert "follow_up" in {turn.lane for turn in turns if turn.result.progressed}
    items = lease(core)
    assert [item["content"] for item in items] == [{"text": "对了，里面那个配角其实挺出彩的。"}]
    assert {row["scenario"] for row in core.store.rows(
        "SELECT scenario FROM model_usage WHERE state='SETTLED'")} == {"chat", "activity"}


def test_real_http_and_independent_worker_settle_one_follow_up_across_restart(core, tmp_path):
    enable(core)

    async def exercise():
        async with real_core_http(core) as port, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False,
            headers={"Authorization": "Bearer " + core.settings.bridge_token.get_secret_value()},
        ) as http:
            for number, text, reply in (
                (1, "我刚看完那部电影", "结局你喜欢吗？"),
                (2, "还行，就是有点仓促", "我也觉得后半段太赶了。"),
            ):
                core.model.chat.append(reply)
                message = event(core, number, text)
                accepted = await http.post("/internal/v1/inbox/events",
                    json=message.model_dump(mode="json"),
                    headers={"X-Idempotency-Key": message.event_id})
                assert accepted.status_code == 202, accepted.text
                assert await core.run_once("chat-entrypoint")
                # The delivered chat turns are the synthetic prior state of this lull.
                core.store.execute("UPDATE outbox SET state='SENT',sent_at=? WHERE state='PENDING'",
                                   (core.clock(),))
                core.clock.advance(60)
            _, authority = authorize(core, tmp_path, account=False,
                                     additional_purposes=("proactive",))
            core.clock.advance(900)
            observed = await process_worker(core, tmp_path, "followup", "evt_test_2")
            assert observed["provider_calls"] == 1
            assert observed["materials"][0]["kind"] == "followup"
            material = observed["materials"][0]
            assert not material["excerpt_truncated"]
            assert all(turn["owner_at"] and not turn["owner_truncated"]
                       and not turn["you_truncated"] for turn in material["turns"])
            assert {unit["lane"] for unit in observed["units"] if unit["progressed"]} >= {
                "follow_up", "activity",
            }
            usage = core.store.rows("SELECT * FROM model_usage WHERE scenario='activity'")
            assert len(usage) == 1 and usage[0]["state"] == "SETTLED"
            leased = await http.post("/internal/v1/deliveries/lease", json={
                "worker_id": "bridge", "authority": authority,
            })
            assert leased.status_code == 200, leased.text
            items = leased.json()["items"]
            assert [item["action_type"] for item in items] == ["qq.private.proactive"]
            assert [item["content"] for item in items] == [
                {"text": "对了，里面那个配角其实挺出彩的。"},
            ]
            restarted = await process_worker(core, tmp_path, "followup", "evt_test_2", restart=True)
            assert restarted["provider_calls"] == 0
            assert len(core.store.rows("SELECT 1 FROM model_usage WHERE scenario='activity'")) == 1
            assert len(core.store.rows("SELECT 1 FROM outbox WHERE action_type="
                                       "'qq.private.proactive'")) == 1

    run(exercise())
