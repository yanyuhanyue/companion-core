"""Bounded observation evidence, not a heartbeat, queue, or health authority."""

import hashlib
import json
import math
import os
import re
import socket
import time
import uuid
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from companion.store import encode


def http_collector(settings, url, *, allow_external=False, authorization_sha256=None):
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("OBSERVATION_ORIGIN_REQUIRED")
    if allow_external and not re.fullmatch(r"[a-f0-9]{64}", authorization_sha256 or ""):
        raise ValueError("OBSERVATION_EXTERNAL_AUTHORIZATION_REQUIRED")

    def collect():
        started = datetime.now(UTC)
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (
            443 if parsed.scheme == "https" else 80
        ), type=socket.SOCK_STREAM)
        if not addresses or (not allow_external and any(
            not ip_address(row[4][0]).is_loopback for row in addresses
        )):
            raise ValueError("OBSERVATION_LOOPBACK_REQUIRED")
        # Pin the actual request after checking DNS. Preserve original Host/SNI.
        target = httpx.URL(url.rstrip("/") + "/internal/v1/ops/status?summary_only=true")
        target = target.copy_with(host=addresses[0][4][0])
        with httpx.Client(timeout=5, trust_env=False, follow_redirects=False,
                          verify=parsed.scheme == "https") as client:
            request = client.build_request("GET", target, headers={
                "Host": parsed.netloc,
                "Authorization": "Bearer " + settings.bridge_token.get_secret_value(),
                "Cache-Control": "no-cache",
            }, extensions={"sni_hostname": parsed.hostname})
            response = client.send(request, stream=True)
            try:
                if response.status_code != 200:
                    raise ValueError("OBSERVATION_HTTP_STATUS")
                body = bytearray()
                for part in response.iter_bytes(chunk_size=8192):
                    body.extend(part)
                    if len(body) > 131072:
                        raise ValueError("OBSERVATION_RESPONSE_TOO_LARGE")
            finally:
                response.close()
        result = json.loads(body)
        operations, identity = result["operations"], result["observation"]["api_instance"]
        observed = datetime.fromisoformat(operations["observed_at"])
        if (operations.get("schema_version") != 1 or operations.get("read_only") is not True
            or not isinstance(operations.get("blockers"), list)
            or not re.fullmatch(r"[a-f0-9]{32}", identity) or observed.tzinfo is None
            or observed < started - timedelta(seconds=5)
            or observed > datetime.now(UTC) + timedelta(seconds=5)):
            raise ValueError("OBSERVATION_RESPONSE_INVALID")
        return operations, identity

    return collect


def observe(collect, output, *, samples=3, interval_seconds=5, max_gap_seconds=None,
            evidence_type="local_synthetic", authorization_sha256=None, source="local_database"):
    if (type(samples) is not int or not 1 <= samples <= 100000
        or not math.isfinite(interval_seconds) or not 0 < interval_seconds <= 3600
        or evidence_type not in {"local_synthetic", "authorized_runtime"}):
        raise ValueError("OBSERVATION_BOUNDS_INVALID")
    max_gap_seconds = (interval_seconds * 1.5 + 0.1 if max_gap_seconds is None
                       else max_gap_seconds)
    if not math.isfinite(max_gap_seconds) or max_gap_seconds < interval_seconds:
        raise ValueError("OBSERVATION_GAP_INVALID")
    if evidence_type == "authorized_runtime" and not re.fullmatch(
        r"[a-f0-9]{64}", authorization_sha256 or "",
    ):
        raise ValueError("OBSERVATION_RUNTIME_AUTHORIZATION_REQUIRED")
    if authorization_sha256 is not None and not re.fullmatch(
        r"[a-f0-9]{64}", authorization_sha256,
    ):
        raise ValueError("OBSERVATION_AUTHORIZATION_REFERENCE_INVALID")
    run_id, segment, prior, segment_start = uuid.uuid4().hex, 0, None, None
    completed, failures, discontinuities = 0, 0, 0
    path = Path(output)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        hasher = hashlib.sha256()

        def write(record):
            line = encode(record) + "\n"
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
            hasher.update(line.encode())

        write({"kind": "observation_start", "schema_version": 1, "run_id": run_id,
               "evidence_type": evidence_type, "source": source,
               "authorization_sha256": authorization_sha256,
               "started_at": datetime.now(UTC).isoformat(),
               "interval_seconds": interval_seconds, "max_gap_seconds": max_gap_seconds,
               "worker_health_proven": False})
        for sequence in range(samples):
            wall, mono = datetime.now(UTC), time.monotonic_ns()
            gap = None if prior is None else (mono - prior[1]) / 1e9
            reasons = []
            if gap is not None and (gap < 0 or gap > max_gap_seconds):
                reasons.append("SAMPLE_GAP")
            if prior is not None and abs((wall - prior[0]).total_seconds() - gap) > 2:
                reasons.append("WALL_CLOCK_DISCONTINUITY")
            try:
                snapshot, instance = collect()
                collection_seconds = (time.monotonic_ns() - mono) / 1e9
                if collection_seconds > max_gap_seconds:
                    reasons.append("COLLECTION_GAP")
                if prior is not None and prior[2] is not None and instance is not None and (
                    prior[2] != instance
                ):
                    reasons.append("API_INSTANCE_CHANGED")
                if snapshot["blockers"]:
                    reasons.append("OBSERVED_BLOCKERS")
                error = None
            except Exception:
                snapshot, instance, error = None, None, "SAMPLE_FAILED"
                reasons.append(error)
                failures += 1
            if reasons:
                discontinuities += 1
                segment_start = None
            elif segment_start is None:
                segment += 1
                segment_start = mono
            write({"kind": "observation_sample", "run_id": run_id, "sequence": sequence,
                   "observed_at": wall.isoformat(), "monotonic_ns": mono,
                   "collection_seconds": (time.monotonic_ns() - mono) / 1e9,
                   "interval_seconds": gap, "api_instance": instance,
                   "restart_detection": "AVAILABLE" if instance else "UNAVAILABLE",
                   "segment": segment if segment_start is not None else None,
                   "discontinuities": reasons, "error": error, "snapshot": snapshot})
            completed += 1
            prior = wall, mono, instance
            if sequence + 1 < samples:
                # Long collections count toward the next interval, never compress gaps.
                time.sleep(max(0, interval_seconds - (time.monotonic_ns() - mono) / 1e9))
        summary = {"kind": "observation_end", "run_id": run_id, "samples": completed,
                   "failures": failures, "discontinuities": discontinuities,
                   "clear_segment_observed_seconds": (
                       (prior[1] - segment_start) / 1e9 if segment_start is not None else 0
                   ), "worker_health_proven": False,
                   "ended_at": datetime.now(UTC).isoformat(),
                   "file_sha256_before_summary": hasher.hexdigest()}
        write(summary)
    return summary
