"""One disposable SDK session, launched only by the isolated executor service."""

import json
import sys
from pathlib import Path


def run_job(body, harness_class=None):
    if harness_class is None:
        from deepseek_harness import DeepSeekHarness

        harness_class = DeepSeekHarness
    Path(body["workspace"]).mkdir(parents=True)
    Path(body["home"]).mkdir(parents=True)
    with harness_class(
        profile="sdk-minimal",
        provider="deepseek-official",
        model=body["model"],
        max_tokens=body["max_tokens"],
        cwd=body["workspace"],
        dsh_home=body["home"],
        base_url=body["proxy_url"],
        api_key=body["token"],
        initialize_timeout_seconds=min(20, body["timeout"]),
        request_timeout_seconds=body["timeout"],
        shutdown_timeout_seconds=1,
    ) as harness:
        result = harness.run(body["prompt"])
    failed_tool = any(
        event.get("type") == "tool/result"
        and (
            event.get("data", {}).get("isError")
            or event.get("data", {}).get("result", {}).get("isError")
        )
        for event in result.events
    )
    if (
        result.finish_reason != "completed"
        or failed_tool
        or not result.final_response.strip()
        or len(result.final_response) > 11000
    ):
        return {"status": "UNKNOWN"}
    return {"status": "SUCCEEDED", "text": result.final_response}


if __name__ == "__main__":
    try:
        print(json.dumps(run_job(json.loads(sys.stdin.buffer.read(65536))), ensure_ascii=False))
    except Exception:
        print('{"status":"UNKNOWN"}')
        sys.exit(1)
