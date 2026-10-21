import re
import stat
import tomllib
from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import urlsplit


def private_address(host):
    try:
        address = ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or any(
        address in ip_network(network)
        for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
    )


def validate_endpoint(url, schemes, service):
    parsed = urlsplit(url)
    if (
        parsed.scheme not in schemes
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or not parsed.port
        or (parsed.hostname not in {service, "localhost"} and not private_address(parsed.hostname))
    ):
        raise ValueError("PRIVATE_ENDPOINT_REQUIRED")
    return parsed


@dataclass(frozen=True, repr=False)
class BridgeConfig:
    owner_qq_id: str
    bot_qq_id: str
    bridge_token: str
    onebot_token: str
    protocol_epoch: str
    core_url: str = "http://companion-api:8765"
    onebot_http_url: str = "http://snowluma:3000"
    onebot_ws_url: str = "ws://snowluma:3001"
    data_dir: str = "/bridge-data"
    delivery_enabled: bool = False
    max_frame_bytes: int = 65536
    max_pending: int = 1000
    max_disk_bytes: int = 67108864
    poll_seconds: float = 0.1
    capture_signing_key: str | None = None
    rich_input_scope: str = "none"

    def __post_init__(self):
        if self.rich_input_scope not in ("none", "references_v1"):
            raise ValueError("INVALID_RICH_INPUT_SCOPE")
        for identity in (self.owner_qq_id, self.bot_qq_id):
            if not isinstance(identity, str) or not re.fullmatch(r"[1-9][0-9]{4,19}", identity):
                raise ValueError("INVALID_ACCOUNT")
        if self.owner_qq_id == self.bot_qq_id:
            raise ValueError("DISTINCT_ACCOUNTS_REQUIRED")
        if not isinstance(self.protocol_epoch, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{8,64}", self.protocol_epoch
        ):
            raise ValueError("INVALID_PROTOCOL_EPOCH")
        for token in (self.bridge_token, self.onebot_token):
            if not isinstance(token, str) or not 32 <= len(token) <= 256 or not token.isascii():
                raise ValueError("INVALID_TOKEN")
            if any(ord(c) <= 32 or ord(c) == 127 for c in token):
                raise ValueError("INVALID_TOKEN")
        if self.bridge_token == self.onebot_token:
            raise ValueError("DISTINCT_TOKENS_REQUIRED")
        if self.capture_signing_key is not None and (
            not isinstance(self.capture_signing_key, str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.capture_signing_key)
            or self.capture_signing_key in {self.bridge_token, self.onebot_token}
        ):
            raise ValueError("INVALID_CAPTURE_SIGNING_KEY")
        validate_endpoint(self.core_url, {"http", "https"}, "companion-api")
        http = validate_endpoint(self.onebot_http_url, {"http", "https"}, "snowluma")
        ws = validate_endpoint(self.onebot_ws_url, {"ws", "wss"}, "snowluma")
        if http.hostname != ws.hostname:
            raise ValueError("ONEBOT_HOST_MISMATCH")
        if type(self.delivery_enabled) is not bool:
            raise ValueError("INVALID_DELIVERY_SWITCH")
        for value, low, high in (
            (self.max_pending, 1, 10000),
            (self.max_frame_bytes, 1024, 262144),
            (self.max_disk_bytes, 1048576, 1073741824),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("INVALID_LIMIT")
        if type(self.poll_seconds) not in (int, float) or not 0.05 <= self.poll_seconds <= 5:
            raise ValueError("INVALID_POLL_INTERVAL")

    @classmethod
    def load(cls, path):
        target = Path(path)
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or target.is_symlink():
            raise ValueError("PRIVATE_CONFIG_REQUIRED")
        if __import__("os").name != "nt" and info.st_mode & 0o077:
            raise ValueError("PRIVATE_CONFIG_REQUIRED")
        return cls(**tomllib.loads(target.read_text("utf-8")))

    @property
    def origin(self):
        return f"onebot-direct:FriendMessage:{self.owner_qq_id}"

    @property
    def capture_public_key(self):
        if self.capture_signing_key is None:
            return None
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
            self.capture_signing_key
        )).public_key().public_bytes_raw().hex()
