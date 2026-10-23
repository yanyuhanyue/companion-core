"""Workspace-local execution journal and short-lived, non-backup result cache."""

import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

from companion.voice_protocol import (
    MAX_RESULT_BODY_BYTES,
    SignedVoiceLease,
    VoiceClaimRequest,
    VoiceProtocolError,
    VoiceResult,
    canonical_bytes,
)


class NodeState:
    """Single parent process owns this state; provider children never open it."""

    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cache = self.directory / "result-cache"
        self.cache.mkdir(exist_ok=True, mode=0o700)
        if self.cache.is_symlink():
            raise VoiceProtocolError("VOICE_STATE_PATH_INVALID")
        self._lock = None
        self.db = None

    def __enter__(self):
        try:
            self._lock = (self.directory / "node.lock").open("a+b")
            if self._lock.seek(0, 2) == 0:
                self._lock.write(b"0")
                self._lock.flush()
            self._lock.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.close()
            raise VoiceProtocolError("VOICE_NODE_ALREADY_RUNNING") from None
        try:
            database = self.directory / "journal.sqlite"
            marker = self.directory / "initialized"
            if database.is_symlink() or marker.is_symlink():
                raise VoiceProtocolError("VOICE_STATE_PATH_INVALID")
            if database.exists() != marker.exists():
                raise VoiceProtocolError("VOICE_STATE_RECOVERY_REQUIRED")
            new = not database.exists()
            self.db = sqlite3.connect(database)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA journal_mode=DELETE")
            if new:
                self.db.executescript(
                    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                    "CREATE TABLE jobs (job_id TEXT NOT NULL, generation INTEGER NOT NULL, "
                    "lease_json TEXT NOT NULL, phase TEXT NOT NULL, result_sha TEXT, "
                    "cache_file TEXT, cache_expires REAL, error_code TEXT, "
                    "PRIMARY KEY(job_id,generation));"
                    "CREATE TABLE recovery_audit (recovery_id TEXT PRIMARY KEY, "
                    "record_json TEXT NOT NULL);"
                )
                self.set_meta("resource", {"state": "READY"})
                self._atomic_write(marker, uuid.uuid4().hex.encode())
            if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise VoiceProtocolError("VOICE_STATE_RECOVERY_REQUIRED")
            resource = self.resource()
            if resource.get("state") not in {"READY", "RUNNING", "QUARANTINED"}:
                raise VoiceProtocolError("VOICE_STATE_RECOVERY_REQUIRED")
            # Standard cache-directory marker: backup tools can exclude this entire tree.
            tag = self.cache / "CACHEDIR.TAG"
            if not tag.exists():
                self._atomic_write(tag, b"Signature: 8a477f597d28d172789f06886806bc55\n")
            self.prune_cache()
            return self
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            self.close()
            raise VoiceProtocolError("VOICE_STATE_RECOVERY_REQUIRED") from None

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self._lock is not None:
            self._lock.close()
            self._lock = None

    def _atomic_write(self, path: Path, data: bytes):
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                descriptor = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def meta(self, key: str):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_meta(self, key: str, value: dict):
        with self.db:
            self.db.execute(
                "INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, canonical_bytes(value).decode()),
            )

    def resource(self) -> dict:
        value = self.meta("resource")
        if not isinstance(value, dict):
            raise VoiceProtocolError("VOICE_STATE_RECOVERY_REQUIRED")
        return value

    def pending_claim(self) -> VoiceClaimRequest | None:
        value = self.meta("pending_claim")
        return VoiceClaimRequest.model_validate(value) if value else None

    def save_claim(self, claim: VoiceClaimRequest):
        previous = self.pending_claim()
        if previous is not None and previous != claim:
            raise VoiceProtocolError("VOICE_CLAIM_REPLAY_MISMATCH")
        self.set_meta("pending_claim", claim.model_dump(mode="json"))

    def clear_claim(self):
        with self.db:
            self.db.execute("DELETE FROM meta WHERE key='pending_claim'")

    def job(self, job_id: str, generation: int) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM jobs WHERE job_id=? AND generation=?",
            (job_id, generation),
        ).fetchone()
        return dict(row) if row else None

    def active_job(self) -> dict | None:
        rows = self.db.execute("SELECT * FROM jobs WHERE phase!='TERMINAL' LIMIT 2").fetchall()
        if len(rows) > 1:
            raise VoiceProtocolError("VOICE_STATE_RECOVERY_REQUIRED")
        return dict(rows[0]) if rows else None

    def accept_claim(self, lease: SignedVoiceLease) -> dict:
        with self.db:
            existing = self.job(lease.job_id, lease.generation)
            if existing is None:
                if self.active_job() is not None:
                    raise VoiceProtocolError("VOICE_NODE_BUSY")
                self.db.execute(
                    "INSERT INTO jobs(job_id,generation,lease_json,phase) VALUES (?,?,?,'CLAIMED')",
                    (lease.job_id, lease.generation, canonical_bytes(lease).decode()),
                )
            else:
                previous = SignedVoiceLease.model_validate_json(existing["lease_json"])
                fields = (
                    "node_id",
                    "operation",
                    "lease_token",
                    "input_sha256",
                    "input_bytes",
                    "input_mime",
                    "source_partial",
                    "manifest_sha256",
                    "issued_at",
                    "job_deadline_at",
                )
                if any(getattr(previous, name) != getattr(lease, name) for name in fields):
                    raise VoiceProtocolError("VOICE_CLAIM_REPLAY_MISMATCH")
            self.db.execute("DELETE FROM meta WHERE key='pending_claim'")
        return self.job(lease.job_id, lease.generation)

    def update_lease(self, lease: SignedVoiceLease):
        with self.db:
            self.db.execute(
                "UPDATE jobs SET lease_json=? WHERE job_id=? AND generation=?",
                (canonical_bytes(lease).decode(), lease.job_id, lease.generation),
            )

    def phase(self, lease: SignedVoiceLease, phase: str, *, error_code: str | None = None):
        with self.db:
            self.db.execute(
                "UPDATE jobs SET phase=?,error_code=? WHERE job_id=? AND generation=?",
                (phase, error_code, lease.job_id, lease.generation),
            )

    def dispatch(self, lease: SignedVoiceLease, *, pid: int, process_identity: str):
        if self.resource()["state"] != "READY":
            raise VoiceProtocolError("VOICE_GPU_QUARANTINED")
        resource = {
            "state": "RUNNING",
            "operation": lease.operation,
            "job_id": lease.job_id,
            "generation": lease.generation,
            "pid": pid,
            "process_identity": process_identity,
        }
        with self.db:
            current = self.job(lease.job_id, lease.generation)
            if current is None or current["phase"] != "CLAIMED":
                raise VoiceProtocolError("VOICE_DISPATCH_ALREADY_RECORDED")
            self.db.execute(
                "UPDATE jobs SET phase='RUNNING' WHERE job_id=? AND generation=?",
                (lease.job_id, lease.generation),
            )
            self.db.execute(
                "UPDATE meta SET value=? WHERE key='resource'",
                (canonical_bytes(resource).decode(),),
            )

    def release_resource(self):
        if self.resource()["state"] == "QUARANTINED":
            raise VoiceProtocolError("VOICE_GPU_QUARANTINED")
        self.set_meta("resource", {"state": "READY"})

    def quarantine(self, reason: str):
        resource = self.resource()
        if resource["state"] == "QUARANTINED":
            return
        resource.update(
            state="QUARANTINED",
            quarantine_id="quarantine_" + uuid.uuid4().hex,
            reason=reason,
        )
        self.set_meta("resource", resource)

    def recover_manually(
        self,
        *,
        quarantine_id: str,
        operator: str,
        service_instance: str,
        confirmation: str,
    ):
        resource = self.resource()
        if (
            resource["state"] != "QUARANTINED"
            or resource.get("quarantine_id") != quarantine_id
            or confirmation != "inflight-execution-stopped"
            or not operator.strip()
            or len(operator) > 128
            or not service_instance.strip()
            or len(service_instance) > 128
        ):
            raise VoiceProtocolError("VOICE_RECOVERY_CONFIRMATION_REQUIRED")
        receipt = {
            "source": "manual_operator_confirmation",
            "operator": operator,
            "service_instance": service_instance,
            "quarantine_id": quarantine_id,
            "confirmation": confirmation,
            "recorded_at_unix": time.time(),
        }
        with self.db:
            self.db.execute(
                "INSERT INTO recovery_audit VALUES (?,?)",
                ("recovery_" + uuid.uuid4().hex, canonical_bytes(receipt).decode()),
            )
            self.db.execute("UPDATE meta SET value=? WHERE key='resource'", ('{"state":"READY"}',))

    def _cache_path(self, name: str) -> Path:
        if not re.fullmatch(r"voice_[a-f0-9]{32}_[0-9]+_[a-f0-9]{64}\.json", name):
            raise VoiceProtocolError("VOICE_STATE_PATH_INVALID")
        path = self.cache / name
        if path.is_symlink() or not path.resolve().is_relative_to(self.cache.resolve()):
            raise VoiceProtocolError("VOICE_STATE_PATH_INVALID")
        return path

    def save_result(self, result: VoiceResult, *, expires_at: float):
        data = canonical_bytes(result)
        if len(data) > MAX_RESULT_BODY_BYTES or expires_at <= time.time():
            raise VoiceProtocolError("VOICE_RESULT_CACHE_EXPIRED")
        name = f"{result.ref.job_id}_{result.ref.generation}_{result.result_sha256}.json"
        path = self._cache_path(name)
        existing = self.job(result.ref.job_id, result.ref.generation)
        if existing is None or existing["phase"] not in {"RUNNING", "RESULT_READY"}:
            raise VoiceProtocolError("VOICE_RESULT_STATE_INVALID")
        if existing["result_sha"] not in {None, result.result_sha256}:
            raise VoiceProtocolError("VOICE_RESULT_CONFLICT")
        self._atomic_write(path, data)
        with self.db:
            self.db.execute(
                "UPDATE jobs SET phase='RESULT_READY',result_sha=?,cache_file=?,cache_expires=? "
                "WHERE job_id=? AND generation=?",
                (result.result_sha256, name, expires_at, result.ref.job_id, result.ref.generation),
            )

    def cached_result(self, job: dict) -> VoiceResult | None:
        if not job["cache_file"] or job["cache_expires"] <= time.time():
            return None
        path = self._cache_path(job["cache_file"])
        try:
            with path.open("rb") as handle:
                data = handle.read(MAX_RESULT_BODY_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(data) > MAX_RESULT_BODY_BYTES:
            raise VoiceProtocolError("VOICE_RESULT_CACHE_INVALID")
        try:
            result = VoiceResult.model_validate_json(data)
        except ValueError:
            raise VoiceProtocolError("VOICE_RESULT_CACHE_INVALID") from None
        if (
            result.ref.job_id != job["job_id"]
            or result.ref.generation != job["generation"]
            or result.result_sha256 != job["result_sha"]
        ):
            raise VoiceProtocolError("VOICE_RESULT_CACHE_INVALID")
        return result

    def finish(self, lease: SignedVoiceLease, *, error_code: str | None = None):
        job = self.job(lease.job_id, lease.generation)
        if job and job["cache_file"]:
            self._cache_path(job["cache_file"]).unlink(missing_ok=True)
        with self.db:
            self.db.execute(
                "UPDATE jobs SET phase='TERMINAL',cache_file=NULL,cache_expires=NULL,error_code=? "
                "WHERE job_id=? AND generation=?",
                (error_code, lease.job_id, lease.generation),
            )

    def prune_cache(self):
        now = time.time()
        with self.db:
            for row in self.db.execute(
                "SELECT * FROM jobs WHERE cache_file IS NOT NULL AND cache_expires<=?",
                (now,),
            ).fetchall():
                self._cache_path(row["cache_file"]).unlink(missing_ok=True)
                self.db.execute(
                    "UPDATE jobs SET cache_file=NULL,cache_expires=NULL,phase='RESULT_LOST',"
                    "error_code='VOICE_RESULT_CACHE_EXPIRED' WHERE job_id=? AND generation=?",
                    (row["job_id"], row["generation"]),
                )
        referenced = {
            row[0]
            for row in self.db.execute("SELECT cache_file FROM jobs WHERE cache_file IS NOT NULL")
        }
        for path in self.cache.iterdir():
            if path.name == "CACHEDIR.TAG" or path.name in referenced:
                continue
            if path.is_symlink():
                raise VoiceProtocolError("VOICE_STATE_PATH_INVALID")
            if path.name.endswith(".part") or re.fullmatch(
                r"voice_[a-f0-9]{32}_[0-9]+_[a-f0-9]{64}\.json",
                path.name,
            ):
                path.unlink()
