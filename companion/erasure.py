"""Bounded module cleanup contract; the Ledger owns durability and coordination.

Each owner keeps its own SQL. Handlers receive the already resolved event closure,
must be idempotent, run in the caller's transaction, and must not commit, append a
Ledger record, send I/O or refund accounting. New persistent projections register
here with their source-evidence eraser; adding a name also requests one targeted
repair of older COMPLETE snapshots via the existing runtime_meta projection.

Diary projection resolution runs before memory/evidence removal and may return
readback event IDs. The handlers below retain their original order after that
phase. Unattributed summaries/traits/Outbox still use Ledger's conservative raw-
fact fallback on a new erasure, never on the one-time COMPLETE repair.
"""

from collections.abc import Callable
from dataclasses import dataclass

from companion.store import Store

CONTRACT_KEY = "derived_erasure_contract"
# Bump when an existing handler's cleanup meaning changes without a new name.
# Names detect registration additions; they cannot detect arbitrary code edits.
CONTRACT_REVISION = 2


@dataclass(frozen=True)
class ErasureScope:
    event_ids: frozenset[str]
    clear_reply_derivatives: bool


@dataclass(frozen=True)
class DerivedCleanup:
    name: str
    erase: Callable[[Store, ErasureScope], None]


def event_cleanup(name, erase):
    """Adapt existing event-evidence erasers without changing their module APIs."""
    return DerivedCleanup(name, lambda store, scope: erase(store, scope.event_ids))


def cleanup_contracts():
    # Lazy imports preserve the existing modules' privacy helper dependencies.
    from companion.affect import erase_affect
    from companion.consolidation import erase_consolidation
    from companion.content import erase_content
    from companion.executive import erase_executive
    from companion.exploration import erase_exploration
    from companion.goals import erase_goals
    from companion.media import erase_media_links
    from companion.meme_core import erase_captions
    from companion.observation import erase_observations
    from companion.reading import erase_reading
    from companion.reflection import erase_reflections

    return (
        event_cleanup("observations", erase_observations),
        event_cleanup("affect", erase_affect),
        DerivedCleanup(
            "reflections",
            lambda store, scope: erase_reflections(
                store, scope.event_ids, scope.clear_reply_derivatives
            ),
        ),
        event_cleanup("consolidation", erase_consolidation),
        event_cleanup("goals", erase_goals),
        event_cleanup("reading", erase_reading),
        event_cleanup("exploration", erase_exploration),
        event_cleanup("executive", erase_executive),
        event_cleanup("media", erase_media_links),
        event_cleanup("meme-captions", erase_captions),
        event_cleanup("content", erase_content),
    )


def contract_signature():
    # One rebuildable marker, not a new authority or Ledger format/version.
    return ",".join(
        (str(CONTRACT_REVISION), "diary-projections", *(item.name for item in cleanup_contracts()))
    )


def erase_derivatives(store, scope):
    if not store.db.in_transaction:
        raise RuntimeError("ERASURE_REQUIRES_TRANSACTION")
    for contract in cleanup_contracts():
        contract.erase(store, scope)
