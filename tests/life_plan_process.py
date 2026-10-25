"""Original selector and Core in an independent process; only provider HTTP is fake."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

from companion.autonomous_selector import native_selector
from companion.config import Settings
from companion.core import Core
from companion.model import ModelClient
from companion.store import after


def choice(material, phase, now):
    result = {"action": "CONTINUE", "destination": "NONE", "text": "",
              "source_event_ids": [source["event_id"] for source in material["sources"]],
              "reason": "依据实际阅读结果调整有限计划"}
    if phase == "PROPOSE":
        result.update(action="EXPLORE", goal={
            "topic": "比较恒星颜色资料", "source_event_ids": result["source_event_ids"],
            "scope": {"operation": "read_plan", "urls": material["read_targets"][:3]},
        })
    elif phase == "FINISH":
        result.update(action="SHARE", destination="PRIVATE_MESSAGE_DRAFT",
                      text="三篇资料都已读到，恒星颜色和表面温度的关系值得再想一想。")
    else:
        plan = next(goal["plan"] for goal in material["goals"] if "plan" in goal)
        result["goal_id"] = plan["goal_id"]
        result["continuation"] = {"expected_version": plan["version"], "action": phase}
        if phase in {"CONTINUE", "REORDER", "BAD_ORDER", "STALE_VERSION"}:
            remaining = [step["step_id"] for step in plan["steps"]
                         if step["state"] == "WAITING_DECISION"]
            result["continuation"].update(action="CONTINUE", step_order=(
                list(reversed(remaining)) if phase == "REORDER" else remaining))
            if phase == "BAD_ORDER":
                result["continuation"]["step_order"] = [plan["steps"][0]["step_id"]]
            elif phase == "STALE_VERSION":
                result["continuation"]["expected_version"] -= 1
        elif phase == "WAIT":
            result["continuation"]["wait_until"] = after(now, 3600)
    return result


async def main():
    args = json.loads(await asyncio.to_thread(Path(sys.argv[1]).read_text, encoding="utf-8"))
    settings = Settings.model_validate(args["settings"])
    if settings.model_url != "https://synthetic.invalid/v1":
        raise ValueError("SYNTHETIC_PROVIDER_REQUIRED")
    calls = []

    def provider(request):
        material = json.loads(json.loads(request.content)["messages"][-1]["content"])
        assert material["kind"] == "life"
        calls.append(material)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(
                choice(material, args["phase"], args["now"]), ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 180, "completion_tokens": 80},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        with Core(settings, ModelClient(settings, client), lambda: args["now"]) as core:
            selector = native_selector(core, "independent-life-plan")
            for _ in range(len(selector.lanes) * 4):
                before = len(calls)
                selected = await selector.step()
                assert len(calls) - before <= 1
                if selected.blocked or not selected.more:
                    break
            else:
                raise AssertionError("native selector did not become idle")
            result = {"pid": os.getpid(), "provider_calls": len(calls), "materials": calls,
                      "results": core.store.rows("SELECT * FROM life_activity_results"),
                      "jobs": core.store.rows("SELECT job_id,state,last_error_code FROM jobs")}
    await asyncio.to_thread(Path(args["result_path"]).write_text, json.dumps(result),
                            encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
