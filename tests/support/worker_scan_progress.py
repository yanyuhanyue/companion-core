"""One output collector owns each independently running native test worker."""

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

SCAN_PREFIX = b'{"phase": "worker_scan"'


def emit_scan_progress(scans):
    print(json.dumps({"phase": "worker_scan", "count": scans}), flush=True)


@dataclass
class Child:
    process: asyncio.subprocess.Process
    ready: Path
    report: Path
    reports_scans: bool = False
    scans: int | None = field(default=None, init=False)
    progress_error: ValueError | None = field(default=None, init=False)
    _collection: asyncio.Task = field(init=False, repr=False)

    def __post_init__(self):
        stdout = asyncio.create_task(self._drain_stdout())
        stderr = asyncio.create_task(self.process.stderr.read())
        self._collection = asyncio.create_task(self._collect(stdout, stderr))

    def _record(self, line):
        if not line.startswith(SCAN_PREFIX):
            return
        try:
            record = json.loads(line)
            if (set(record) != {"phase", "count"} or record["phase"] != "worker_scan"
                    or type(record["count"]) is not int or record["count"] < 1):
                raise ValueError("INVALID_SCAN_PROGRESS")
            if self.scans is not None and record["count"] <= self.scans:
                raise ValueError("SCAN_PROGRESS_REGRESSION")
            self.scans = record["count"]
        except (TypeError, ValueError) as error:
            if self.progress_error is None:
                self.progress_error = ValueError(str(error))
                self.progress_error.add_note("Raw scan frame: " + repr(line))

    async def _drain_stdout(self):
        output, pending = bytearray(), b""
        while chunk := await self.process.stdout.read(4096):
            output.extend(chunk)
            *lines, pending = (pending + chunk).split(b"\n")
            for line in lines:
                self._record(line)
        if pending.startswith(SCAN_PREFIX) and self.progress_error is None:
            self.progress_error = ValueError("INCOMPLETE_SCAN_PROGRESS")
            self.progress_error.add_note("Raw scan frame: " + repr(pending))
        return bytes(output)

    async def _collect(self, stdout, stderr):
        await self.process.wait()
        return tuple(await asyncio.gather(stdout, stderr))

    async def collect(self):
        # Canceling one waiter must not abandon either pipe or create another reader.
        return await asyncio.shield(self._collection)


def read_scan_progress(child):
    if child.progress_error is not None:
        raise child.progress_error
    if child.scans is None:
        raise ValueError("MISSING_SCAN_PROGRESS")
    return str(child.scans)
