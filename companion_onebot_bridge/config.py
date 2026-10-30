import re
import stat
import tomllib
from dataclasses import asdict, dataclass
from ipaddress import ip_address, ip_network
from pathlib import Path, PurePosixPath
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
    astrbot_handoff_path: str | None = None
    astrbot_handoff_sha256: str | None = None
    ingress_after: int = 0
    max_frame_bytes: int = 65536
    max_voice_lease_bytes: int = 6000000
    max_pending: int = 1000
    max_disk_bytes: int = 67108864
    poll_seconds: float = 0.1
    capture_signing_key: str | None = None
    owner_authority: dict | None = None
    rich_input_scope: str = "none"
    meme_directory: str | None = None
    meme_enabled: bool = False
    meme_catalog_owner: str = "bridge"
    meme_semantics_enabled: bool = False
    # Read back owner messages sent while the event socket was closed (backfill.py).
    history_backfill: bool = False
    meme_discovery_enabled: bool = False
    meme_search_provider: str = "none"
    meme_search_api_key: str | None = None
    meme_trusted_sources: tuple[dict, ...] = ()
    meme_catalog_max_storage_bytes: int = 67108864
    meme_catalog_max_assets: int = 512
    meme_catalog_max_bindings: int = 10000
    meme_cooldown_seconds: int = 1800
    account_asset_directory: str | None = None
    account_asset_policy_sha256: str | None = None
    account_asset_max_bytes: int = 2097152
    account_asset_max_dimension: int = 2048

    def __post_init__(self):
        if (not isinstance(self.meme_catalog_owner, str)
            or self.meme_catalog_owner not in {"bridge", "core"}):
            raise ValueError("INVALID_MEME_CATALOG_OWNER")
        if self.owner_authority is not None:
            from companion_delivery_protocol.authority import validate_authority_ref

            object.__setattr__(self, "owner_authority", validate_authority_ref(
                self.owner_authority))
            if self.capture_signing_key is None:
                raise ValueError("OWNER_CAPTURE_SIGNING_KEY_REQUIRED")
        if type(self.ingress_after) is not int or not 0 <= self.ingress_after <= 4102444800:
            raise ValueError("INVALID_INGRESS_BOUNDARY")
        if self.astrbot_handoff_path is not None:
            if (not isinstance(self.astrbot_handoff_path, str)
                or not Path(self.astrbot_handoff_path).is_absolute()
                or ".." in Path(self.astrbot_handoff_path).parts
                or not isinstance(self.astrbot_handoff_sha256, str)
                or not re.fullmatch(r"[a-f0-9]{64}", self.astrbot_handoff_sha256)
                or self.ingress_after == 0):
                raise ValueError("INVALID_ASTRBOT_HANDOFF")
        elif self.astrbot_handoff_sha256 is not None or self.ingress_after:
            raise ValueError("ASTRBOT_HANDOFF_REQUIRED")
        if (self.account_asset_directory is None) != (self.account_asset_policy_sha256 is None):
            raise ValueError("ACCOUNT_ASSET_REVIEW_POLICY_REQUIRED")
        if self.account_asset_directory is not None and (
            not isinstance(self.account_asset_directory, str)
            or not Path(self.account_asset_directory).is_absolute()
            or ".." in Path(self.account_asset_directory).parts
            or not isinstance(self.account_asset_policy_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.account_asset_policy_sha256)
        ):
            raise ValueError("INVALID_ACCOUNT_ASSET_CONFIGURATION")
        for value, high in ((self.account_asset_max_bytes, 20 * 1024 * 1024),
                            (self.account_asset_max_dimension, 8192)):
            if type(value) is not int or not 1 <= value <= high:
                raise ValueError("INVALID_ACCOUNT_ASSET_LIMIT")
        if type(self.history_backfill) is not bool:
            raise ValueError("INVALID_HISTORY_BACKFILL")
        if (type(self.meme_enabled) is not bool or type(self.meme_discovery_enabled) is not bool
            or type(self.meme_semantics_enabled) is not bool
            or self.meme_search_provider not in {"none", "brave"}):
            raise ValueError("INVALID_MEME_CONFIGURATION")
        if self.meme_search_api_key is not None and (
            not isinstance(self.meme_search_api_key, str)
            or not 1 <= len(self.meme_search_api_key) <= 4096
            or not self.meme_search_api_key.isascii()
            or any(ord(char) <= 32 or ord(char) == 127 for char in self.meme_search_api_key)
        ):
            raise ValueError("INVALID_MEME_SEARCH_CREDENTIAL")
        for value, low, high in (
            (self.meme_catalog_max_storage_bytes, 1, 1073741824),
            (self.meme_catalog_max_assets, 1, 4096),
            (self.meme_catalog_max_bindings, 1, 100000),
            (self.meme_cooldown_seconds, 0, 86400),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("INVALID_MEME_LIMIT")
        if not isinstance(self.meme_trusted_sources, (tuple, list)) or len(
            self.meme_trusted_sources
        ) > 64:
            raise ValueError("INVALID_MEME_SOURCE_POLICY")
        for rule in self.meme_trusted_sources:
            if (not isinstance(rule, dict) or set(rule) != {
                "repo", "revision", "license", "license_path", "license_sha256",
                "asset_prefix", "usage_basis",
            } or any(not isinstance(value, str) or not value or len(value) > 512
                     for value in rule.values())
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", rule["repo"])
                or any(part in {".", ".."} for part in rule["repo"].split("/"))
                or not re.fullmatch(r"[a-f0-9]{40}", rule["revision"])
                or not re.fullmatch(r"[a-f0-9]{64}", rule["license_sha256"])):
                raise ValueError("INVALID_MEME_SOURCE_POLICY")
            for key in ("license_path", "asset_prefix"):
                path = PurePosixPath(rule[key])
                if (path.is_absolute() or ".." in path.parts or "\\" in rule[key]
                    or ":" in rule[key]):
                    raise ValueError("INVALID_MEME_SOURCE_POLICY")
        object.__setattr__(self, "meme_trusted_sources", tuple(self.meme_trusted_sources))
        if self.meme_directory is not None and (
            not isinstance(self.meme_directory, str) or not Path(self.meme_directory).is_absolute()
            or ".." in Path(self.meme_directory).parts
        ):
            raise ValueError("ABSOLUTE_MEME_LIBRARY_REQUIRED")
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
            (self.max_voice_lease_bytes, 65536, 6000000),
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

    def snapshot(self):
        value = asdict(self)
        if self.meme_catalog_owner == "bridge":
            value.pop("meme_catalog_owner")
        return value

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
