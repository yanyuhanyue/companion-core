import json
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from companion import observe, ops_status


def records(path):
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


@contextmanager
def status_server(core, *, fail=False, stale=False):
    requests = []
    instance = ["a" * 32]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers["Authorization"], self.headers["Host"]))
            snapshot = ops_status.snapshot(core.settings)
            if stale:
                snapshot["observed_at"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
            body = json.dumps({"operations": snapshot,
                               "observation": {"api_instance": instance[0]}}).encode()
            self.send_response(503 if fail else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests, instance
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_real_localhost_samples_use_wall_and_monotonic_time_without_worker_claim(core, tmp_path):
    output = tmp_path / "observation.jsonl"
    with status_server(core) as (url, requests, instance):
        collect = observe.http_collector(core.settings, url)
        started = time.monotonic()
        summary = observe.observe(collect, output, samples=3, interval_seconds=0.02,
                                  max_gap_seconds=5)
        elapsed = time.monotonic() - started
    data = records(output)
    samples = data[1:-1]
    assert summary["failures"] == summary["discontinuities"] == 0
    assert summary["worker_health_proven"] is False
    assert 0 < summary["clear_segment_observed_seconds"] <= elapsed < 10
    assert data[0]["evidence_type"] == "local_synthetic"
    assert len({row["run_id"] for row in data}) == 1
    assert all(row["segment"] == 1 and row["api_instance"] == instance[0] for row in samples)
    assert all("+00:00" in row["observed_at"] for row in samples)
    assert len(requests) == 3
    assert all(path == "/internal/v1/ops/status?summary_only=true" for path, _, _ in requests)
    assert all(token == "Bearer " + core.settings.bridge_token.get_secret_value()
               for _, token, _ in requests)
    assert url.removeprefix("http://") == requests[0][2]
    text = output.read_text("utf-8")
    assert url not in text and core.settings.bridge_token.get_secret_value() not in text
    assert core.settings.database not in text


def test_failure_restart_and_long_gap_split_segments_not_continuous_health(tmp_path):
    number = 0

    def collect():
        nonlocal number
        number += 1
        if number == 2:
            raise RuntimeError("private response body")
        if number == 4:
            time.sleep(0.08)
        return {"blockers": []}, ("a" if number < 4 else "b") * 32

    output = tmp_path / "gaps.jsonl"
    result = observe.observe(collect, output, samples=5, interval_seconds=0.01,
                             max_gap_seconds=0.05)
    data = records(output)[1:-1]
    assert data[1]["error"] == "SAMPLE_FAILED" and data[1]["segment"] is None
    assert data[2]["segment"] != data[0]["segment"]
    assert "API_INSTANCE_CHANGED" in data[3]["discontinuities"]
    assert "SAMPLE_GAP" in data[4]["discontinuities"]
    assert result["failures"] == 1 and result["clear_segment_observed_seconds"] == 0
    assert "private response body" not in output.read_text("utf-8")


def test_observed_blockers_never_count_toward_clear_segment(tmp_path):
    output = tmp_path / "blocked.jsonl"
    result = observe.observe(lambda: ({"blockers": ["MODEL_USAGE_UNSETTLED"]}, None), output,
                             samples=2, interval_seconds=0.001)
    assert result["clear_segment_observed_seconds"] == 0
    assert all(row["segment"] is None for row in records(output)[1:-1])
    assert all(row["restart_detection"] == "UNAVAILABLE" for row in records(output)[1:-1])


def test_new_invocations_cannot_append_or_join_evidence(tmp_path):
    first, second = tmp_path / "one.jsonl", tmp_path / "two.jsonl"
    def collect():
        return {"blockers": []}, None

    one = observe.observe(collect, first, samples=1)
    with pytest.raises(FileExistsError):
        observe.observe(collect, first, samples=1)
    two = observe.observe(collect, second, samples=1)
    assert one["run_id"] != two["run_id"]
    assert one["clear_segment_observed_seconds"] == two["clear_segment_observed_seconds"] == 0


def test_nonloopback_dns_and_external_without_authorization_make_no_http(core, monkeypatch):
    calls = []
    monkeypatch.setattr(observe.socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (2, 1, 6, "", ("192.168.1.5", 8765)),
    ])
    monkeypatch.setattr(observe.httpx, "Client", lambda **kwargs: calls.append(kwargs))
    with pytest.raises(ValueError, match="LOOPBACK"):
        observe.http_collector(core.settings, "http://localhost:8765")()
    with pytest.raises(ValueError, match="AUTHORIZATION"):
        observe.http_collector(core.settings, "https://synthetic.invalid", allow_external=True)
    assert calls == []


@pytest.mark.parametrize("url", ["https://user:secret@host", "http://localhost?key=secret",
                                "http://localhost/other", "file:///private"])
def test_http_collector_rejects_non_origin_urls(core, url):
    with pytest.raises(ValueError):
        observe.http_collector(core.settings, url)


def test_http_failure_is_a_recorded_failure_not_api_or_worker_health(core, tmp_path):
    with status_server(core, fail=True) as (url, requests, _):
        output = tmp_path / "failed.jsonl"
        result = observe.observe(observe.http_collector(core.settings, url), output, samples=1)
    assert len(requests) == result["failures"] == 1
    assert records(output)[1]["snapshot"] is None
    assert result["worker_health_proven"] is False


def test_stale_http_snapshot_is_a_failure_not_a_continuous_interval(core, tmp_path):
    with status_server(core, stale=True) as (url, requests, _):
        output = tmp_path / "stale.jsonl"
        result = observe.observe(observe.http_collector(core.settings, url), output, samples=1)
    assert len(requests) == result["failures"] == 1
    assert result["clear_segment_observed_seconds"] == 0


def test_runtime_evidence_requires_explicit_authorization_reference(tmp_path):
    with pytest.raises(ValueError, match="AUTHORIZATION"):
        observe.observe(lambda: ({"blockers": []}, None), tmp_path / "runtime.jsonl",
                        samples=1, evidence_type="authorized_runtime")
    assert not (tmp_path / "runtime.jsonl").exists()


def test_wall_clock_jump_is_a_gap_and_does_not_add_fictional_duration(tmp_path, monkeypatch):
    calls = 0

    class JumpingClock(datetime):
        @classmethod
        def now(cls, timezone):
            nonlocal calls
            calls += 1
            return datetime.now(UTC) + timedelta(seconds=10 if calls >= 3 else 0)

    monkeypatch.setattr(observe, "datetime", JumpingClock)
    output = tmp_path / "clock.jsonl"
    result = observe.observe(lambda: ({"blockers": []}, None), output,
                             samples=3, interval_seconds=0.001)
    data = records(output)[1:-1]
    assert "WALL_CLOCK_DISCONTINUITY" in data[1]["discontinuities"]
    assert data[1]["segment"] is None and data[2]["segment"] != data[0]["segment"]
    assert result["clear_segment_observed_seconds"] == 0


def test_slow_last_collection_is_not_an_unobserved_clear_interval(tmp_path):
    def collect():
        time.sleep(0.03)
        return {"blockers": []}, None

    output = tmp_path / "slow.jsonl"
    result = observe.observe(collect, output, samples=1, interval_seconds=0.001,
                             max_gap_seconds=0.01)
    assert records(output)[1]["discontinuities"] == ["COLLECTION_GAP"]
    assert result["clear_segment_observed_seconds"] == 0


def test_interrupted_observation_leaves_explicitly_incomplete_evidence(tmp_path):
    def collect():
        raise KeyboardInterrupt

    output = tmp_path / "interrupted.jsonl"
    with pytest.raises(KeyboardInterrupt):
        observe.observe(collect, output, samples=1)
    assert [row["kind"] for row in records(output)] == ["observation_start"]


@pytest.mark.parametrize("options", [{"samples": 0}, {"samples": 100001},
                                    {"interval_seconds": float("nan")},
                                    {"interval_seconds": 0}, {"max_gap_seconds": 0},
                                    {"authorization_sha256": "private raw string"}])
def test_invalid_bounds_do_not_create_evidence(tmp_path, options):
    output = tmp_path / "invalid.jsonl"
    with pytest.raises(ValueError):
        observe.observe(lambda: ({"blockers": []}, None), output, **options)
    assert not output.exists()
