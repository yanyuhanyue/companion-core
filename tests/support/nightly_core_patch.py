"""Test the pending Core-owner delegation patch without editing that owner's file.

Enable with pytest -p tests.support.nightly_core_patch until Root applies the
reviewable patch. Only three method delegates change; native entrypoints, DB,
budget/admission and provider transport remain the real implementations.
"""

import pytest

from companion import erasure, memory_work
from companion.core import Core


@pytest.fixture(autouse=True)
def pending_core_delegates(monkeypatch):
    monkeypatch.setattr(Core, "extract_once", memory_work.extract_once)
    monkeypatch.setattr(Core, "extract_step", memory_work.extract_step, raising=False)
    monkeypatch.delattr(Core, "_extract_claimed_once", raising=False)
    monkeypatch.setattr(Core, "prepare_memories", memory_work.prepare_memories)
    original = Core.__init__

    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.consolidation.extract_step = self.extract_step

    monkeypatch.setattr(Core, "__init__", initialize)
    contracts = erasure.cleanup_contracts
    monkeypatch.setattr(erasure, "cleanup_contracts", lambda: (
        *contracts(), erasure.event_cleanup("memory-work", memory_work.erase_memory_work),
    ))
