"""Real shared-lock/executor lifetime and bounded float32 proof representation."""

import asyncio
import math
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from companion import meme_index as index
from companion.contracts import CoreError
from tests.conftest import run


def vector(first=1.0, second=0.0):
    return [first, second, *([0.0] * 510)]


class Encoder:
    def passage_embed(self, texts, *, batch_size):
        assert type(texts) is tuple and len(texts) <= 32 and batch_size == 32
        return (vector() for _ in texts)

    def query_embed(self, query):
        assert type(query) is str and len(query) <= 2000
        return iter([vector()])


def local_encoder(monkeypatch, factory=Encoder):
    calls = []
    main_thread = threading.get_ident()

    def load(path, *, local_only):
        assert local_only is True and threading.get_ident() != main_thread
        assert index._inference_lock(path).locked()
        calls.append(path)
        return factory()

    monkeypatch.setattr(index, "encoder", load)
    return calls


@pytest.mark.parametrize(
    "bad",
    [
        [],
        [1.0] * 511,
        [1.0] * 513,
        [0.0] * 512,
        vector(math.nan),
        vector(math.inf),
        vector(1e100),
        [True, *([0.0] * 511)],
        ["1.0", *([0.0] * 511)],
    ],
)
def test_invalid_vectors_never_enter_cache_or_proof(bad):
    with pytest.raises((ValueError, OverflowError)):
        index.pack(bad)


def test_float32_is_the_scored_and_hashed_representation():
    encoded = index.pack(vector(0.123456789, 0.234567891))
    assert len(encoded) == 2048
    assert index.unpack(encoded)[0] == struct.unpack("<f", struct.pack("<f", 0.123456789))[0]
    assert index.decode_vector(index.vector_text(encoded), index.vector_sha(encoded)) == encoded
    with pytest.raises(ValueError):
        index.decode_vector(index.vector_text(encoded) + "\n", index.vector_sha(encoded))
    with pytest.raises(ValueError):
        index.decode_vector(index.vector_text(encoded), "f" * 64)


def test_local_cpu_materializes_one_query_and_at_most_32_passages(tmp_path, monkeypatch):
    calls = local_encoder(monkeypatch)
    service_index = index.MemeIndex()
    query, passages, reason = run(
        service_index.infer(
            tmp_path / "nested" / ".." / "cache", "query", ("caption",) * 32, time.monotonic() + 2
        )
    )
    assert query and len(passages) == 32 and reason is None
    assert len(calls) == 1 and calls[0] == str((tmp_path / "cache").resolve())
    assert service_index._inflight is None
    assert not index._inference_lock(calls[0]).locked()


def test_cancellation_before_cpu_starts_keeps_lock_and_one_queued_future(tmp_path, monkeypatch):
    calls = local_encoder(monkeypatch)
    release = threading.Event()

    async def scenario():
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        occupied = loop.run_in_executor(None, release.wait)
        service_index = index.MemeIndex()
        request = asyncio.create_task(
            service_index.infer(tmp_path, "query", ("caption",), time.monotonic() + 2)
        )
        await asyncio.sleep(0)
        future = service_index._inflight
        assert future is not None and not future.done() and calls == []
        lock = index._inference_lock(str(tmp_path.resolve()))
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert lock.locked() and not future.cancelled()
        try:
            for path in (tmp_path, tmp_path / "changed-cache"):
                assert (await service_index.infer(path, "query", (), time.monotonic() + 2))[
                    2
                ] == "INFERENCE_BUSY"
            assert service_index._inflight is future and calls == []
        finally:
            release.set()
        await occupied
        await asyncio.shield(future)
        await asyncio.sleep(0)
        assert len(calls) == 1 and not lock.locked() and service_index._inflight is None

    run(scenario())


def test_deadline_keeps_running_cpu_locked_and_discards_late_failure(tmp_path, monkeypatch):
    release = threading.Event()

    async def scenario():
        started, loop = asyncio.Event(), asyncio.get_running_loop()
        errors = []
        loop.set_exception_handler(lambda _, context: errors.append(context))

        class Stalled(Encoder):
            def query_embed(self, query):
                loop.call_soon_threadsafe(started.set)
                assert release.wait(5)
                raise RuntimeError("SYNTHETIC_LATE_CPU_FAILURE")

        calls = local_encoder(monkeypatch, Stalled)
        service_index = index.MemeIndex()
        request = asyncio.create_task(
            service_index.infer(tmp_path, "query", (), time.monotonic() + 0.1)
        )
        await asyncio.wait_for(started.wait(), 2)
        assert (await request) == (None, (), "LOCAL_DEADLINE")
        future = service_index._inflight
        lock = index._inference_lock(str(tmp_path.resolve()))
        try:
            for _ in range(5):
                assert (await service_index.infer(tmp_path, "query", (), time.monotonic() + 2))[
                    2
                ] == "INFERENCE_BUSY"
            assert len(calls) == 1 and lock.locked() and future is not None
        finally:
            release.set()
        with pytest.raises(RuntimeError, match="SYNTHETIC_LATE_CPU_FAILURE"):
            await asyncio.shield(future)
        await asyncio.sleep(0)
        assert not lock.locked() and service_index._inflight is None and errors == []

    run(scenario())


@pytest.mark.parametrize("bad", [vector(math.nan), [0.0] * 512, [1.0] * 511])
def test_fresh_invalid_model_output_is_a_hard_error(tmp_path, monkeypatch, bad):
    class Invalid(Encoder):
        def query_embed(self, query):
            return iter([bad])

    local_encoder(monkeypatch, Invalid)
    with pytest.raises(CoreError, match="MEME_INDEX_INFERENCE_FAILED"):
        run(index.MemeIndex().infer(tmp_path, "query", (), time.monotonic() + 2))
    assert not index._inference_lock(str(tmp_path.resolve())).locked()


def test_submission_failure_releases_only_unsubmitted_lock(tmp_path, monkeypatch):
    async def scenario():
        loop = asyncio.get_running_loop()

        def fail(*args):
            raise RuntimeError("SYNTHETIC_SUBMISSION_FAILURE")

        with monkeypatch.context() as patch:
            patch.setattr(loop, "run_in_executor", fail)
            with pytest.raises(RuntimeError, match="SYNTHETIC_SUBMISSION_FAILURE"):
                await index.MemeIndex().infer(tmp_path, "query", (), time.monotonic() + 2)
        assert not index._inference_lock(str(tmp_path.resolve())).locked()

    run(scenario())
