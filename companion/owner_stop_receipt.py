"""Read original journal-backed STOP evidence without granting execution permission."""

from contextlib import nullcontext

from companion import owner_authority
from companion.contracts import CoreError
from companion_delivery_protocol.authority import AuthorityRef


def read(store, settings, authority):
    ref = AuthorityRef.from_dict(authority)
    with nullcontext() if store.db.in_transaction else store.transaction():
        owner_authority.custody(store, settings, settlement=True)
        original = store.one(
            "SELECT * FROM owner_authority_revisions WHERE grant_id=? AND revision=?",
            (ref.grant_id, ref.revision),
        )
        stopped = owner_authority._latest(store)
        if (original is None or owner_authority.reference(original) != ref
            or original["state"] != "ACTIVE" or stopped is None
            or stopped["state"] != "STOPPED"
            or stopped["grant_id"] != ref.grant_id
            or stopped["revision"] != ref.revision + 1
            or stopped["predecessor_sha256"] != original["record_sha256"]
            or stopped["policy_sha256"] != ref.policy_sha256
            or stopped["restore_generation"] != ref.restore_generation
            or owner_authority._meta(store, owner_authority.GENERATION)
            != ref.restore_generation):
            raise CoreError("OWNER_AUTHORITY_STOP_EVIDENCE_REQUIRED", 409)
        return {"authority": ref.as_dict(), "state": "STOPPED",
                "stopped_authority": owner_authority.reference(stopped).as_dict(),
                "core_stop_sha256": stopped["record_sha256"],
                "budget_journal_id": original["journal_id"], "quiescence_proven": False}
