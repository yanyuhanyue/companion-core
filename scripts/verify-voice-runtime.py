"""Run an actual local provider in the production supervised child process.

Only explicit sample input is used. This does not connect to Core or send QQ messages.
"""

import argparse
import asyncio
import base64
import hashlib
import json
import time
from pathlib import Path

from companion.voice_protocol import VoiceAudioResult, VoiceTranscriptResult
from companion_voice_node.providers import validate_wav
from companion_voice_node.supervisor import ProcessSupervisor


async def verify(arguments):
    root = arguments.runtime_root.resolve(strict=True)
    workspace = arguments.workspace.resolve(strict=True)
    profile = json.loads(arguments.profile.read_text(encoding="utf-8-sig"))
    operation = arguments.operation
    provider = profile[operation]
    data = arguments.input.read_bytes()
    if operation == "asr":
        validate_wav(data)
    else:
        data.decode("utf-8")
    output = arguments.output.resolve()
    if not output.is_relative_to(root) or not arguments.input.resolve().is_relative_to(root):
        raise ValueError("Verification input and output must remain in the runtime directory")
    temporary = root / "tmp" / ("verify-" + str(time.time_ns()))
    supervisor = ProcessSupervisor(workspace, temporary)
    started = time.monotonic()
    try:
        await supervisor.start(30)
        child_pid = supervisor.process.pid
        await supervisor.dispatch(
            {
                "operation": operation,
                "provider": provider,
                "input_base64": base64.b64encode(data).decode(),
                "source_partial": False,
            }
        )
        result = await asyncio.wait_for(supervisor.result(), arguments.timeout)
        if not await supervisor.finish():
            raise RuntimeError("Verification provider child did not finish cleanly")
    finally:
        stopped = await supervisor.stop()
    elapsed = time.monotonic() - started
    receipt = {
        "operation": operation,
        "elapsed_seconds": elapsed,
        "provider_child_pid": child_pid,
        "provider_child_exited": stopped,
        "input_sha256": hashlib.sha256(data).hexdigest(),
        "real_provider": True,
        "core_connected": False,
        "qq_sent": False,
    }
    if result["status"] != "SUCCEEDED":
        receipt.update(result)
        output.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2))
        raise RuntimeError(result["error_code"])
    if operation == "tts":
        payload = VoiceAudioResult.model_validate(result["payload"])
        audio = base64.b64decode(payload.audio_base64, validate=True)
        validated = validate_wav(audio)
        output.write_bytes(audio)
        receipt.update(
            {
                "sha256": validated.sha256,
                "bytes": len(audio),
                "duration_seconds": validated.duration_seconds,
                "sample_rate": validated.sample_rate,
                "channels": validated.channels,
                "real_time_factor": elapsed / validated.duration_seconds,
            }
        )
    else:
        payload = VoiceTranscriptResult.model_validate(result["payload"])
        output.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
        receipt.update(
            {
                "language": payload.language,
                "partial": payload.partial,
                "duration_seconds": payload.duration_seconds,
                "segments": len(payload.segments),
                "device": provider["device"],
                "compute_type": provider["compute_type"],
                "real_time_factor": elapsed / payload.duration_seconds,
            }
        )
    receipt["status"] = "SUCCEEDED"
    output.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--operation", choices=("tts", "asr"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    asyncio.run(verify(parser.parse_args()))
