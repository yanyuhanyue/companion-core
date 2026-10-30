"""A Bridge stop finishes the unit in progress instead of cancelling it mid-send."""

import asyncio

from companion_onebot_bridge import app
from tests.conftest import run


def test_stop_waits_for_the_unit_in_progress(monkeypatch):
    finished = []

    async def scenario():
        stop = asyncio.Event()

        async def loop():
            await stop.wait()
            # A send that was already under way when stop arrived.
            await asyncio.sleep(0.05)
            finished.append("sent")

        tasks = [asyncio.create_task(loop())]
        await asyncio.sleep(0.02)
        stop.set()
        return await app.drain(tasks)

    assert run(scenario()) is True
    assert finished == ["sent"]


def test_a_unit_outliving_the_grace_is_cancelled(monkeypatch):
    monkeypatch.setattr(app, "SHUTDOWN_GRACE_SECONDS", 0.05)

    async def scenario():
        task = asyncio.create_task(asyncio.sleep(10))
        clean = await app.drain([task])
        await asyncio.sleep(0)
        return clean, task.cancelled()

    assert run(scenario()) == (False, True)


def test_stop_closes_the_socket_and_an_exit_without_stop_does_not():
    class Socket:
        closed = 0

        async def close(self):
            self.closed += 1

    async def scenario():
        stop, first, second = asyncio.Event(), Socket(), Socket()
        async with app.close_on_stop(stop, second):
            pass
        async with app.close_on_stop(stop, first):
            stop.set()
            await asyncio.sleep(0.01)
        return first.closed, second.closed

    assert run(scenario()) == (1, 0)
