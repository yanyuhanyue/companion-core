"""Dependency-free owner authority reference for the Core/Bridge HTTP v1 boundary."""

import re
from dataclasses import asdict, dataclass

CAPTURE_DOMAIN = b"companion-core/owner-capture/v2\x00"
DELIVERY_DOMAIN = b"companion-core/owner-delivery/v2\x00"
QUIESCENCE_DOMAIN = b"companion-core/owner-quiescence/v2\x00"
FIELDS = {"version", "grant_id", "revision", "policy_sha256", "restore_generation"}


@dataclass(frozen=True)
class AuthorityRef:
    version: int
    grant_id: str
    revision: int
    policy_sha256: str
    restore_generation: str

    def __post_init__(self):
        if (type(self.version) is not int or self.version != 2
            or type(self.revision) is not int or not 1 <= self.revision <= 2**53 - 1
            or not isinstance(self.grant_id, str)
            or not re.fullmatch(r"grant_[a-f0-9]{32}", self.grant_id)
            or not isinstance(self.policy_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.policy_sha256)
            or not isinstance(self.restore_generation, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.restore_generation)):
            raise ValueError("OWNER_AUTHORITY_REFERENCE_INVALID")

    @classmethod
    def from_dict(cls, value):
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict) or set(value) != FIELDS:
            raise ValueError("OWNER_AUTHORITY_REFERENCE_INVALID")
        return cls(**value)

    def as_dict(self):
        return asdict(self)


def validate_authority_ref(value):
    return AuthorityRef.from_dict(value).as_dict()
