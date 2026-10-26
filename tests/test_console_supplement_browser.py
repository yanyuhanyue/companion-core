"""Supplemental console controls use real TLS, browser actions and native receipts."""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from companion.affect import NEUTRAL, feedback_reset_at
from companion.console_cognition import tool_scope
from companion.executive_protocol import ExecutorEndpoint, ToolArguments, ToolRequest
from companion.source_grants import grant_tombstone
from tests.conftest import run
from tests.test_console_browser import console_page, navigate, submit_dialog
from tests.test_executive import FakeExecutor
from tests.test_executive import configured as configure_executive
from tests.test_executive import proposal as tool_proposal
from tests.test_growth_foundations import complete_read, view
from tests.test_life_topics import bug, due
from tests.test_life_topics import proposal as life_proposal
from tests.test_m1 import remember
from tests.test_next_life_growth import feedback_days, snapshot
from tests.test_source_grants import accepted, derived_records
from tests.test_source_grants import prepare as prepare_grant

pytestmark = pytest.mark.skipif(
    os.environ.get("COMPANION_TEST_BROWSER") != "1", reason="Explicit isolated browser run"
)


def test_console_model_measurement_distinguishes_local_counts_and_unknown_usage(core, tmp_path):
    from pathlib import Path

    from tests.test_context_meter_inspector import recorded_attempts

    core.clock.now = datetime.now(UTC)
    run(recorded_attempts(core, tmp_path, unknown=True))
    attempts = core.store.rows("SELECT request_id,state FROM model_usage ORDER BY rowid")

    async def scenario():
        async with console_page(core, tmp_path) as page:
            for attempt in attempts:
                await page.evaluate("id => { location.hash = 'thinking/model:' + id; }",
                                    attempt["request_id"])
                await page.locator("#page[aria-busy=false]").wait_for()
                panel = page.locator(".steps-view").filter(has_text="本次请求计量")
                await panel.wait_for()
                assert "固定渲染器本地计数" in await panel.inner_text()
                assert "上下文区域分布与裁剪原因未记录" in await panel.inner_text()
                usage = panel.locator(".kv").filter(has_text="实际输入 / 输出")
                assert ("未记录" if attempt["state"] == "UNKNOWN" else "100 / 4") in (
                    await usage.inner_text())
                assert "SYNTHETIC_PRIVATE_TOOL_RESULT" not in (
                    await page.locator("#page").inner_text())
            # Retain one synthetic screenshot for the local verification receipt.
            target = Path(__file__).parents[1] / ".local/test-results/context-meter-browser.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(target), full_page=True)

    run(scenario())


def test_console_cache_receipt_three_states_preserve_known_usage(core, tmp_path):
    from pathlib import Path

    from playwright.async_api import expect

    from tests.test_cache_usage_presence import ABSENT, attempt
    from tests.test_token_measurement import configure

    core.clock.now = datetime.now(UTC)
    configure(core, tmp_path)
    attempts = [attempt(core, cache=value)[1] for value in (ABSENT, 0, 40)]
    target = Path(__file__).parents[1] / "output/playwright"
    target.mkdir(parents=True, exist_ok=True)

    async def exercise():
        async with console_page(core, tmp_path) as page:
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            for row, expected, name in zip(attempts, ("未记录", "0", "40"),
                                           ("absent", "zero", "positive"), strict=True):
                await page.goto(page.url.split("#")[0] + "#thinking/model:" + row["request_id"])
                panel = page.locator(".steps-view").filter(has_text="本次请求计量")
                await panel.wait_for()
                cache = panel.locator(".kv").filter(has_text="实际缓存输入")
                await expect(cache.locator("span").last).to_have_text(expected)
                assert "80 / 3" in await panel.inner_text()
                assert "固定渲染器本地计数" in await panel.inner_text()
                assert "已计或预留费用" in await panel.inner_text()
                await page.screenshot(path=str(target / f"c5a-cache-{name}.png"), full_page=True)
            assert errors == []

    run(exercise())


def assert_receipts(core, states):
    receipts = core.store.rows("SELECT state,result_json FROM console_operations ORDER BY rowid")
    assert all(item["state"] == "COMPLETE" for item in receipts)
    # These native actions intentionally return no business identifier to the UI.
    assert [json.loads(item["result_json"]) for item in receipts] == [
        {"state": state} for state in states]


def test_console_source_grant_revoke_retains_history_then_forget_erases_it(core, tmp_path):
    core.clock.now = datetime.now(UTC)
    grant = prepare_grant(core)
    event_id = accepted(core, grant)
    derived_records(core, event_id)

    async def scenario():
        async with console_page(core, tmp_path) as page:
            await navigate(page, "阅读与文件")
            await page.get_by_role("button", name="来源授权", exact=True).click()
            row = page.locator("tbody tr").filter(has_text="读取程序 heybox")
            assert await row.get_by_text("有效", exact=True).count() == 1
            path = f"/source-grants/{grant.grant_id}"
            await row.get_by_role("button", name="撤回", exact=True).click()
            await submit_dialog(page, "撤回授权", path)
            saved = core.store.one("SELECT * FROM source_grants WHERE grant_id=?",
                                   (grant.grant_id,))
            assert saved["revoked_at"] and saved["forgotten_at"] is None
            assert not core.store.one("SELECT redacted FROM events WHERE event_id=?",
                                      (event_id,))["redacted"]
            assert core.store.one("SELECT content FROM memories")["content"] == (
                "Private derived memory")
            assert await row.get_by_text("已撤回", exact=True).count() == 1
            assert await row.get_by_role("button", name="撤回", exact=True).count() == 0
            assert_receipts(core, ["COMPLETE"])

            await row.get_by_role("button", name="遗忘", exact=True).click()
            await submit_dialog(page, "遗忘这个授权", path)
            assert core.store.one("SELECT forgotten_at FROM source_grants WHERE grant_id=?",
                                  (grant.grant_id,))["forgotten_at"]
            assert core.store.one("SELECT redacted FROM events WHERE event_id=?",
                                  (event_id,))["redacted"]
            assert core.store.one("SELECT status,content FROM memories") == {
                "status": "FORGOTTEN", "content": ""}
            assert core.store.one("SELECT value FROM runtime_meta WHERE key=?",
                                  (grant_tombstone(grant.grant_id),))
            assert await row.get_by_text("已遗忘", exact=True).count() == 1
            assert await row.get_by_role("button").count() == 0
            assert_receipts(core, ["COMPLETE", "COMPLETE"])
            assert core.model.calls == []

    run(scenario())


def test_console_can_close_consumed_topic_and_invalidate_its_retained_shadow(core, tmp_path):
    core.clock.now = datetime.now(UTC) - timedelta(days=1)
    topic = bug(core)
    life_proposal(core)
    assert run(core.run_once(job_id=due(core)))
    assert core.store.one("SELECT state,reason_code FROM life_topics") == {
        "state": "CLOSED", "reason_code": "CONSUMED"}
    draft = core.store.one("SELECT * FROM life_activity_results")
    assert draft["state"] == "SHADOW" and draft["text"]
    usage = core.store.rows("SELECT * FROM model_usage")
    calls = list(core.model.calls)

    async def scenario():
        async with console_page(core, tmp_path) as page:
            await navigate(page, "日记与成长")
            card = page.locator(".topic").filter(has_text="导出时还有一个没有解决的Bug")
            assert draft["text"] in await card.inner_text()
            assert "她已经做了决定" in await card.inner_text()
            await card.get_by_role("button", name="关闭话题", exact=True).click()
            await page.get_by_label("以哪句话作为关闭依据").select_option("evt_test_1")
            await submit_dialog(page, "关闭话题", f"/life/topics/{topic['topic_id']}/close")
            assert core.store.one("SELECT state,reason_code,closed_event_id FROM life_topics") == {
                "state": "CLOSED", "reason_code": "OWNER_CLOSED", "closed_event_id": "evt_test_1"}
            assert core.store.one("SELECT state,text,reason FROM life_activity_results") == {
                "state": "INVALIDATED", "text": "", "reason": ""}
            assert await card.get_by_role("button", name="关闭话题", exact=True).count() == 0
            assert draft["text"] not in await card.inner_text()
            assert_receipts(core, ["CLOSED"])
            assert core.store.rows("SELECT * FROM model_usage") == usage
            assert core.model.calls == calls
            assert not core.store.rows("SELECT * FROM outbox")

    run(scenario())


def test_console_affect_reset_clears_feedback_but_retains_preferences_and_interests(core, tmp_path):
    core.clock.now = datetime.now(UTC) - timedelta(days=2)
    core.settings.affect_enabled = True
    feedback_days(core, count=3)
    memory_id = remember(core, 100, "我喜欢天文主题", confirmed=True)
    complete_read(core, 200, "https://example.org/console-browser-astronomy")
    assert core.affect.snapshot()["state"]["interaction_tone"] == "concise"
    learned = snapshot(core)["items"][0]
    assert learned["independent_days"] == 3 and learned["status"] == "EMERGING"
    interests = view(core)
    events = core.store.rows("SELECT event_id,body_json,redacted FROM events ORDER BY rowid")

    async def scenario():
        async with console_page(core, tmp_path) as page:
            await navigate(page, "情绪与偏好")
            affect = page.locator(".affect-dim").filter(has_text="回复风格")
            assert await affect.locator("b").inner_text() == "简短一点"
            preference = page.locator(".sheet").filter(
                has=page.get_by_role("heading", name="你的偏好", exact=True))
            interest = page.locator(".sheet").filter(
                has=page.get_by_role("heading", name="她的兴趣", exact=True))
            expression = page.locator(".sheet").filter(
                has=page.get_by_role("heading", name="表达习惯学习", exact=True))
            assert "我喜欢天文主题" in await preference.inner_text()
            assert "来自你的 1 条原话" in await preference.inner_text()
            assert "天文研究" in await interest.inner_text()
            assert "读完 1 篇，分布在 1 天" in await interest.inner_text()
            assert "回复风格：简短一点" in await expression.inner_text()
            assert "有 3 天的明确反馈" in await expression.inner_text()
            assert "正在形成" in await expression.inner_text()

            await page.get_by_role("button", name="重置情绪观察", exact=True).click()
            await submit_dialog(page, "重置", "/affect/reset")
            assert core.affect.snapshot()["state"] == NEUTRAL
            assert not core.affect.snapshot()["evidence"]
            assert snapshot(core)["items"] == [] and feedback_reset_at(core.store)
            assert await affect.locator("b").inner_text() == "默认"
            assert "还没有可以学习的反馈" in await expression.inner_text()
            assert core.store.rows(
                "SELECT event_id,body_json,redacted FROM events ORDER BY rowid") == events
            assert core.store.one("SELECT status FROM memories WHERE memory_id=?",
                                  (memory_id,))["status"] == "CONFIRMED"
            after = view(core)
            assert after["privacy_epoch"] == interests["privacy_epoch"] + 1
            for kind in ("owner.preference", "self.interest"):
                assert after[kind] == interests[kind]
            assert_receipts(core, ["RESET"])
            assert core.model.calls == []

    run(scenario())


def test_console_tools_approve_exact_scope_then_revoke_and_reject_without_dispatch(core, tmp_path):
    core.clock.now = datetime.now(UTC)
    executor = FakeExecutor()
    executive = configure_executive(core, executor, allow_changes=True)
    core.executive.adapter = executor
    approved = executive.submit(tool_proposal(2, "windows.write_text", text="待批准的合成文件正文"))
    rejected = executive.submit(tool_proposal(3, "windows.write_text",
                                             text="待拒绝的另一份合成正文"))

    async def scenario():
        async with console_page(core, tmp_path) as page:
            await navigate(page, "执行任务")
            approve_card = page.locator(".tool-card").filter(has_text="待批准的合成文件正文")
            assert "result.txt" in await approve_card.inner_text()
            await approve_card.get_by_role("button", name="批准", exact=True).click()
            await page.get_by_role("checkbox", name="我已经看过写入文件的具体内容").check()
            await submit_dialog(page, "批准执行", f"/tools/{approved['task_id']}/decision")
            assert executive.get(approved["task_id"])["state"] == "QUEUED"
            approval = core.store.one("SELECT * FROM tool_approvals WHERE task_id=?",
                                      (approved["task_id"],))
            assert approval["decision"] == "APPROVE"
            assert approval["scope_sha256"] == approved["scope_sha256"]
            assert_receipts(core, ["QUEUED"])

            await approve_card.get_by_role("button", name="撤回", exact=True).click()
            await submit_dialog(page, "撤回任务", f"/tools/{approved['task_id']}/revoke")
            assert executive.get(approved["task_id"])["state"] == "CANCELLED"
            assert executive.get(approved["task_id"])["last_error_code"] == "OWNER_REVOKED"
            assert core.store.one("SELECT revoked_at FROM tool_approvals WHERE task_id=?",
                                  (approved["task_id"],))["revoked_at"]

            reject_card = page.locator(".tool-card").filter(has_text="待拒绝的另一份合成正文")
            await reject_card.get_by_role("button", name="拒绝", exact=True).click()
            await submit_dialog(page, "拒绝", f"/tools/{rejected['task_id']}/decision")
            assert executive.get(rejected["task_id"])["state"] == "CANCELLED"
            assert core.store.one("SELECT decision FROM tool_approvals WHERE task_id=?",
                                  (rejected["task_id"],))["decision"] == "REJECT"
            assert_receipts(core, ["QUEUED", "CANCELLED", "CANCELLED"])
            assert executor.calls == core.model.calls == []
            assert not core.store.rows("SELECT * FROM tool_tasks WHERE dispatched_at IS NOT NULL")

    run(scenario())


@pytest.mark.parametrize("field", ["text", "prompt"])
def test_console_redacted_tool_scope_disables_approval_but_allows_rejection(core, tmp_path, field):
    core.clock.now = datetime.now(UTC)
    executor = FakeExecutor()
    executive = configure_executive(core, executor, allow_changes=True)
    core.executive.adapter = executor
    private_text = "我朋友的住址（仅供测试的合成内容）"
    if field == "text":
        request = tool_proposal(2, "windows.write_text", text=private_text)
    else:
        executive.config.endpoints["dsh"] = ExecutorEndpoint(
            url="http://127.0.0.1:8992", token="synthetic-dsh-browser-token-123456",
            workspace="work", capabilities=["dsh.run"], max_cost_micro_usd=1000)
        request = ToolRequest(idempotency_key="browser-redacted-dsh", capability="dsh.run",
            workspace="work", arguments=ToolArguments(prompt=private_text),
            source_event_ids=["evt_test_1"])
    task = executive.submit(request)
    scope = tool_scope(core, task)
    assert scope["state"] == "RECORDED" and scope[field]["state"] == "REDACTED"

    async def scenario():
        async with console_page(core, tmp_path) as page:
            await navigate(page, "执行任务")
            card = page.locator(".tool-card")
            assert "含有可能敏感的内容，不在这里显示" in await card.inner_text()
            assert private_text not in await page.locator("#page").inner_text()
            assert await card.get_by_role("button", name="批准", exact=True).is_disabled()
            assert await card.get_by_role("button", name="拒绝", exact=True).is_enabled()
            assert await card.get_by_role("button", name="撤回", exact=True).is_enabled()
            await card.get_by_role("button", name="拒绝", exact=True).click()
            await submit_dialog(page, "拒绝", f"/tools/{task['task_id']}/decision")
            assert executive.get(task["task_id"])["state"] == "CANCELLED"
            assert core.store.one("SELECT decision,scope_sha256 FROM tool_approvals") == {
                "decision": "REJECT", "scope_sha256": task["scope_sha256"]}
            assert_receipts(core, ["CANCELLED"])
            assert executor.calls == core.model.calls == []

    run(scenario())
