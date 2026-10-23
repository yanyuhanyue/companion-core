"""Bounded local extraction using the existing PyAV/FFmpeg libraries in a child process.

There is deliberately no URL resolver or downloader. The Reader supplies a verified
DownloadedMedia and its live authorization callback. Custom read-only AVIO, forced
container formats and an empty protocol allowlist prohibit external references.

Linux enforces address-space/CPU/file-size/open-file rlimits; Windows enforces Job
Object committed-memory/CPU/one-process limits. These are not identical RSS limits.
An unavailable native limiter rejects the work; there is no unbounded fallback.
"""

import asyncio
import hashlib
import inspect
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

from companion.video_media_contract import (
    AssetManifest,
    ClipRange,
    DownloadedMedia,
    MediaAsset,
    MediaLimits,
    MediaSelection,
    VideoTarget,
    validate_assets,
    validate_selection,
)

PACKAGE_ROOT = str(Path(__file__).resolve().parents[1])


class MediaExtractionError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise MediaExtractionError(code)


async def _validate(callback, cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        _fail("MEDIA_EXTRACTION_CANCELLED")
    value = callback()
    if inspect.isawaitable(value):
        value = await value
    if value is not None:
        _fail("MEDIA_AUTHORIZATION_UNAVAILABLE")
    if cancel_event is not None and cancel_event.is_set():
        _fail("MEDIA_EXTRACTION_CANCELLED")


def _real_path(value, *, create=False):
    path = Path(value)
    if (str(path).startswith(("\\\\", "//")) or not path.is_absolute()
        or path.is_symlink() or path.resolve() != path.absolute()):
        raise ValueError("media paths must be explicit real local absolute paths")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


async def _freeze(downloaded, target, validate, cancel_event):
    source = await asyncio.to_thread(_real_path, downloaded.path)
    try:
        initial = os.stat(source, follow_symlinks=False)
        if not stat.S_ISREG(initial.st_mode) or initial.st_size != downloaded.byte_length:
            _fail("MEDIA_INPUT_CHANGED")
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                             | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(descriptor, "rb") as incoming, target.open("xb") as frozen:
            info = os.fstat(incoming.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size != downloaded.byte_length
                or (info.st_dev, info.st_ino) != (initial.st_dev, initial.st_ino)):
                _fail("MEDIA_INPUT_CHANGED")
            digest, size = hashlib.sha256(), 0
            while data := incoming.read(min(1024 * 1024, downloaded.byte_length - size + 1)):
                size += len(data)
                if size > downloaded.byte_length:
                    _fail("MEDIA_INPUT_CHANGED")
                digest.update(data)
                frozen.write(data)
                await _validate(validate, cancel_event)
            if size != downloaded.byte_length or digest.hexdigest() != downloaded.sha256:
                _fail("MEDIA_INPUT_CHANGED")
            frozen.flush()
            os.fsync(frozen.fileno())
    except OSError:
        _fail("MEDIA_INPUT_UNAVAILABLE")


async def _bounded_read(stream, maximum, overflow):
    chunks, total, exceeded = [], 0, False
    while chunk := await stream.read(16384 if exceeded else min(16384, maximum - total + 1)):
        if not exceeded:
            total += len(chunk)
            if total > maximum:
                exceeded = True
                chunks.clear()
                overflow.set()
            else:
                chunks.append(chunk)
        if exceeded:
            # Keep draining until the supervisor kills the child. Abandoning a
            # paused Windows pipe here can prevent process.wait() from closing.
            await asyncio.sleep(0)
    return b"".join(chunks)


async def _stop(process, grace):
    if process.returncode is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), grace)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()


async def _process(request, limits, validate, cancel_event):
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "LD_LIBRARY_PATH"}}
    env.update(PYTHONPATH=PACKAGE_ROOT, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
               TEMP=str(request.parent), TMP=str(request.parent), TMPDIR=str(request.parent))
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "source_reader.video_extract", "--worker", str(request),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=PACKAGE_ROOT, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    overflow = asyncio.Event()
    stdout = asyncio.create_task(_bounded_read(
        process.stdout, limits.max_process_output_bytes, overflow))
    stderr = asyncio.create_task(_bounded_read(
        process.stderr, limits.max_process_output_bytes, overflow))
    finished = asyncio.create_task(process.wait())
    try:
        while not finished.done():
            await _validate(validate, cancel_event)
            if overflow.is_set():
                _fail("MEDIA_PROCESS_OUTPUT_LIMIT")
            for task in (stdout, stderr):
                if task.done():
                    task.result()
            # File writes are bounded in the child as well; inspect only its output files.
            sizes = [path.stat().st_size for path in request.parent.glob("asset-*.*")]
            if (any(size > limits.max_asset_bytes for size in sizes)
                or sum(sizes) > limits.max_total_output_bytes):
                _fail("MEDIA_OUTPUT_LIMIT")
            await asyncio.wait({finished}, timeout=0.1)
        output, _ = await asyncio.gather(stdout, stderr)
        await _validate(validate, cancel_event)
        if overflow.is_set():
            _fail("MEDIA_PROCESS_OUTPUT_LIMIT")
        if process.returncode:
            _fail("MEDIA_PROCESS_FAILED")
        try:
            result = json.loads(output)
        except (ValueError, TypeError):
            _fail("MEDIA_PROCESS_RESPONSE_INVALID")
        if not isinstance(result, dict):
            _fail("MEDIA_PROCESS_RESPONSE_INVALID")
        if set(result) == {"error"}:
            allowed = {
                "MEDIA_RESOURCE_LIMIT_UNAVAILABLE", "MEDIA_INPUT_INVALID", "MEDIA_TIMELINE_INVALID",
                "MEDIA_DECODE_LIMIT", "MEDIA_OUTPUT_LIMIT", "MEDIA_EXTRACTION_UNAVAILABLE",
                "MEDIA_SELECTION_UNAVAILABLE",
            }
            _fail(result["error"] if result["error"] in allowed else "MEDIA_PROCESS_FAILED")
        return result
    finally:
        await _stop(process, limits.terminate_grace_seconds)
        await asyncio.gather(finished, stdout, stderr, return_exceptions=True)


async def extract_media(target, selection, limits, downloaded, *, validate, work_dir,
                        cancel_event=None):
    """Return verified bounded bytes; every owned temporary file is removed on exit.

    No subprocess starts until input size, hash, target and selection match. The
    callback must raise when Core's current lease/source/grant is no longer valid.
    Resource control is mandatory: Linux rlimits or a Windows process Job Object.
    """
    validate_selection(target, selection, limits, downloaded)
    if not callable(validate):
        raise ValueError("a live Core validation callback is required")
    try:
        async with asyncio.timeout(limits.timeout_seconds):
            root = await asyncio.to_thread(_real_path, work_dir, create=True)
            await _validate(validate, cancel_event)
            with tempfile.TemporaryDirectory(prefix="video-extract-", dir=root) as temporary:
                directory = Path(temporary)
                if directory.parent != root:
                    _fail("MEDIA_INPUT_PATH_INVALID")
                frozen = directory / "input.media"
                await _freeze(downloaded, frozen, validate, cancel_event)
                request = directory / "request.json"
                request.write_text(json.dumps({
                    "target": asdict(target), "selection": asdict(selection),
                    "limits": asdict(limits), "downloaded": asdict(downloaded),
                }, allow_nan=False), encoding="utf-8")
                result = await _process(request, limits, validate, cancel_event)
                count = len(selection.frame_times) if selection.kind == "preview" else len(
                    selection.clips)
                if (set(result) != {"assets"} or not isinstance(result["assets"], list)
                    or len(result["assets"]) != count):
                    _fail("MEDIA_PROCESS_RESPONSE_INVALID")
                assets = []
                for index, row in enumerate(result["assets"]):
                    if not isinstance(row, dict):
                        _fail("MEDIA_PROCESS_RESPONSE_INVALID")
                    manifest = AssetManifest(**{
                        **row, "source_pts_seconds": tuple(row.get("source_pts_seconds", ())),
                    })
                    suffix = "png" if manifest.kind == "frame" else "mp4"
                    path = directory / f"asset-{index}.{suffix}"
                    if path.is_symlink() or path.stat().st_size > limits.max_asset_bytes:
                        _fail("MEDIA_OUTPUT_LIMIT")
                    with path.open("rb") as stream:
                        data = stream.read(limits.max_asset_bytes + 1)
                    assets.append(MediaAsset(manifest, data))
                validate_assets(target, selection, limits, tuple(assets),
                                expected_input_sha256=downloaded.sha256)
                await _validate(validate, cancel_event)
                return tuple(assets)
    except TimeoutError:
        _fail("MEDIA_EXTRACTION_TIMEOUT")


def _resource_limits(limits):
    """Fail closed if the platform cannot install enforced child process limits."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Basic(ctypes.Structure):
                _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                    ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
                    ("maximum_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD)]

            class IO(ctypes.Structure):
                _fields_ = [(name, ctypes.c_uint64) for name in (
                    "read_operations", "write_operations", "other_operations", "read_bytes",
                    "write_bytes", "other_bytes")]

            class Extended(ctypes.Structure):
                _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                    ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t),
                    ("peak_job_memory", ctypes.c_size_t)]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                       ctypes.c_void_p, wintypes.DWORD]
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            handle, info = kernel.CreateJobObjectW(None, None), Extended()
            # CPU, one process, committed memory, and kill on custody loss.
            info.basic.flags = 0x2 | 0x8 | 0x100 | 0x2000
            info.basic.process_time = math.ceil(limits.timeout_seconds) * 10_000_000
            info.basic.active_processes = 1
            info.process_memory = limits.max_memory_bytes
            if (not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info),
                                                                 ctypes.sizeof(info))
                or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())):
                _fail("MEDIA_RESOURCE_LIMIT_UNAVAILABLE")
            return handle  # Keep custody until worker process exit.
        if sys.platform.startswith("linux"):
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (limits.max_memory_bytes,) * 2)
            resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(limits.timeout_seconds),) * 2)
            resource.setrlimit(resource.RLIMIT_FSIZE, (limits.max_asset_bytes,) * 2)
            resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
            return None
    except (OSError, ValueError, AttributeError):
        pass
    _fail("MEDIA_RESOURCE_LIMIT_UNAVAILABLE")


class _BoundedOutput:
    def __init__(self, stream, maximum):
        self.stream, self.maximum = stream, maximum

    def write(self, data):
        if self.stream.tell() + len(data) > self.maximum:
            _fail("MEDIA_OUTPUT_LIMIT")
        return self.stream.write(data)

    def seek(self, offset, whence=0):
        position = self.stream.seek(offset, whence)
        if not 0 <= position <= self.maximum:
            _fail("MEDIA_OUTPUT_LIMIT")
        return position

    def tell(self):
        return self.stream.tell()

    def writable(self):
        return True

    def seekable(self):
        return True

    def flush(self):
        self.stream.flush()


def _open_media(av, stream, mime):
    return av.open(stream, mode="r", format="mov" if mime == "video/mp4" else "matroska",
                   options={"protocol_whitelist": "", "enable_drefs": "0",
                            "use_absolute_path": "0"})


def _worker_extract(directory, target, selection, limits, downloaded):
    import av

    input_path = directory / "input.media"
    with input_path.open("rb") as frozen:
        actual_hash = hashlib.file_digest(frozen, "sha256").hexdigest()
    if (input_path.stat().st_size != downloaded.byte_length or actual_hash != downloaded.sha256):
        _fail("MEDIA_INPUT_INVALID")
    assets, decoded, total_bytes, total_frames, total_seconds = [], 0, 0, 0, 0.0
    with (input_path.open("rb") as incoming,
          _open_media(av, incoming, downloaded.mime_type) as source):
        if len(source.streams.video) != 1:
            _fail("MEDIA_INPUT_INVALID")
        stream = source.streams.video[0]
        stream.codec_context.thread_count = 1
        if (stream.start_time is None or not stream.time_base
            or min(stream.width, stream.height) < 1
            or stream.width * stream.height > limits.max_source_pixels):
            _fail("MEDIA_INPUT_INVALID")
        duration = (float(stream.duration * stream.time_base) if stream.duration is not None
                    else source.duration / av.time_base if source.duration else 0)
        if (not math.isfinite(duration) or duration <= 0
            or abs(duration - downloaded.source_range.seconds)
            > limits.timestamp_tolerance_seconds):
            _fail("MEDIA_TIMELINE_INVALID")
        origin = float(stream.start_time * stream.time_base)
        scale = min(1, limits.max_width / stream.width, limits.max_height / stream.height)
        width, height = max(2, int(stream.width * scale) // 2 * 2), max(
            2, int(stream.height * scale) // 2 * 2)

        def frames(start, end):
            nonlocal decoded
            local = start - downloaded.source_range.start_seconds + origin
            source.seek(int(local / stream.time_base), stream=stream,
                        backward=True, any_frame=False)
            previous = -math.inf
            for frame in source.decode(stream):
                decoded += 1
                if decoded > limits.max_decoded_frames:
                    _fail("MEDIA_DECODE_LIMIT")
                if (frame.pts is None or not frame.time_base
                    or frame.width * frame.height > limits.max_source_pixels):
                    _fail("MEDIA_INPUT_INVALID")
                stamp = float(frame.pts * frame.time_base) - origin
                stamp += downloaded.source_range.start_seconds
                if not math.isfinite(stamp) or stamp <= previous:
                    _fail("MEDIA_TIMELINE_INVALID")
                previous = stamp
                if stamp + 1e-9 < start:
                    continue
                if stamp >= end:
                    break
                yield frame, round(stamp, 9)

        requests = ((at, at) for at in selection.frame_times) if selection.kind == "preview" else (
            (clip.start_seconds, clip.end_seconds) for clip in selection.clips)
        for index, (start, end) in enumerate(requests):
            is_frame = selection.kind == "preview"
            kind, suffix, mime, codec = (("frame", "png", "image/png", "png") if is_frame
                                         else ("clip", "mp4", "video/mp4", "h264"))
            path = directory / f"asset-{index}.{suffix}"
            maximum = min(limits.max_asset_bytes, limits.max_total_output_bytes - total_bytes)
            pts, output_seconds = [], 0.0
            stop = min(downloaded.source_range.end_seconds, start + limits.max_frame_gap_seconds
                       + 1e-9) if is_frame else end
            with path.open("xb") as output:
                bounded = _BoundedOutput(output, maximum)
                if is_frame:
                    try:
                        frame, stamp = next(frames(start, stop))
                    except StopIteration:
                        _fail("MEDIA_SELECTION_UNAVAILABLE")
                    frame.reformat(width=width, height=height, format="rgb24").to_image().save(
                        bounded, format="PNG")
                    pts = [stamp]
                else:
                    with av.open(bounded, mode="w", format="mp4") as encoded:
                        video = encoded.add_stream("libx264", rate=limits.output_fps)
                        video.width, video.height, video.pix_fmt = width, height, "yuv420p"
                        video.options = {"crf": "23", "preset": "veryfast", "threads": "1"}
                        previous_tick = -1
                        for frame, stamp in frames(start, stop):
                            if not pts:
                                first = stamp
                            tick = round((stamp - first) * limits.output_fps)
                            if tick <= previous_tick:
                                continue
                            if len(pts) + total_frames >= limits.max_output_frames:
                                _fail("MEDIA_DECODE_LIMIT")
                            image = frame.reformat(width=width, height=height, format="yuv420p")
                            image.pts, image.time_base = tick, Fraction(1, limits.output_fps)
                            for packet in video.encode(image):
                                encoded.mux(packet)
                            previous_tick = tick
                            pts.append(stamp)
                        if not pts:
                            _fail("MEDIA_SELECTION_UNAVAILABLE")
                        for packet in video.encode():
                            encoded.mux(packet)
                    output.flush()
                    with path.open("rb") as saved, _open_media(av, saved, "video/mp4") as checked:
                        if len(checked.streams) != 1 or len(checked.streams.video) != 1:
                            _fail("MEDIA_OUTPUT_LIMIT")
                        actual = checked.streams.video[0]
                        actual.codec_context.thread_count = 1
                        output_seconds = float(actual.duration * actual.time_base)
                        count = sum(1 for _ in checked.decode(actual))
                        if (actual.codec_context.name != "h264" or count != len(pts)
                            or (actual.width, actual.height) != (width, height)):
                            _fail("MEDIA_OUTPUT_LIMIT")
            total_frames += len(pts)
            total_seconds += output_seconds
            if (pts[0] - start > limits.max_frame_gap_seconds
                or not is_frame and end - pts[-1] > limits.max_frame_gap_seconds):
                _fail("MEDIA_SELECTION_UNAVAILABLE")
            if (output_seconds > limits.max_clip_seconds + limits.timestamp_tolerance_seconds
                or total_seconds > limits.max_total_clip_seconds
                + limits.timestamp_tolerance_seconds
                or total_frames > limits.max_output_frames):
                _fail("MEDIA_OUTPUT_LIMIT")
            size = path.stat().st_size
            total_bytes += size
            with path.open("rb") as saved:
                digest = hashlib.file_digest(saved, "sha256").hexdigest()
            assets.append(AssetManifest(
                asset_id=f"{kind}-{index}", kind=kind, target_sha256=target.sha256,
                selection_sha256=selection.sha256, input_sha256=downloaded.sha256,
                requested_start_seconds=start, requested_end_seconds=end,
                actual_start_seconds=pts[0], actual_end_seconds=pts[-1],
                source_pts_seconds=tuple(pts), output_duration_seconds=output_seconds,
                mime_type=mime, codec=codec, width=width, height=height, frame_count=len(pts),
                byte_length=size, sha256=digest,
            ))
    return {"assets": [asdict(asset) for asset in assets]}


def _worker(path):
    try:
        payload = json.loads(path.read_bytes())
        target, limits = VideoTarget(**payload["target"]), MediaLimits(**payload["limits"])
        selected = payload["selection"]
        selection = MediaSelection(**{**selected, "frame_times": tuple(selected["frame_times"]),
            "clips": tuple(ClipRange(**clip) for clip in selected["clips"])})
        incoming = payload["downloaded"]
        downloaded = DownloadedMedia(**{**incoming, "source_range": ClipRange(
            **incoming["source_range"])})
        validate_selection(target, selection, limits, downloaded)
        resource_custody = _resource_limits(limits)
        result = _worker_extract(path.parent, target, selection, limits, downloaded)
        del resource_custody
    except MediaExtractionError as error:
        result = {"error": error.code}
    except ImportError:
        result = {"error": "MEDIA_EXTRACTION_UNAVAILABLE"}
    except Exception:
        result = {"error": "MEDIA_INPUT_INVALID"}
    print(json.dumps(result, separators=(",", ":"), allow_nan=False))


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit(2)
    _worker(Path(sys.argv[2]))
