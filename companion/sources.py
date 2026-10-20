"""Closed platform policies shared by content, receipts, grants and sharing.

This is a fixed two-source allowlist, not an adapter registry. Observation protocol
types also retain their existing non-content sources; those confer no read/share
capability. Transport-specific scraping and credential handling stay in adapters.
"""

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlsplit, urlunsplit

from companion.contracts import CoreError

ContentSource = Literal["bilibili", "heybox"]
ObservationSource = Literal[
    ContentSource, "web", "voice", "camera", "android", "dsh", "windows", "simulation", "qq_image"
]
SourceCapability = Literal[
    "get", "read", "discover", "reading", "article", "share", "source_grant", "link_images"
]


@dataclass(frozen=True)
class SourceDefinition:
    name: ContentSource
    capabilities: frozenset[SourceCapability]
    content_hosts: frozenset[str]
    content_path: str
    identifier: str
    canonical_prefix: str
    image_hosts: frozenset[str]
    reading_evidence_hosts: frozenset[str]
    share_label: str
    lowercase_identifier: bool = False
    query_path: str = ""
    query_identifier: str = ""


_COMMON = frozenset({"get", "read", "discover", "share", "source_grant", "link_images"})
_DEFINITIONS = (
    SourceDefinition(
        name="bilibili",
        capabilities=_COMMON | {"reading"},
        content_hosts=frozenset({"www.bilibili.com", "m.bilibili.com", "bilibili.com"}),
        content_path=r"/video/(BV[0-9A-Za-z]{10}|av[0-9]{1,20})/?",
        identifier=r"BV[0-9A-Za-z]{10}|av[0-9]{1,20}",
        canonical_prefix="https://www.bilibili.com/video/",
        image_hosts=frozenset({"i0.hdslb.com", "i1.hdslb.com", "i2.hdslb.com"}),
        reading_evidence_hosts=frozenset(
            {"www.bilibili.com", "api.bilibili.com", "aisubtitle.hdslb.com", "i0.hdslb.com"}
        ),
        share_label="视频简介",
    ),
    SourceDefinition(
        name="heybox",
        capabilities=_COMMON | {"article"},
        content_hosts=frozenset({"www.xiaoheihe.cn", "xiaoheihe.cn", "api.xiaoheihe.cn"}),
        content_path=r"/(?:(?:app/)?bbs/link|a)/([0-9]{1,32}|[0-9a-fA-F]{12})/?",
        identifier=r"[0-9]{1,32}|[0-9a-fA-F]{12}",
        canonical_prefix="https://www.xiaoheihe.cn/app/bbs/link/",
        image_hosts=frozenset({"imgheybox.max-c.com", "imgheybox1.max-c.com"}),
        reading_evidence_hosts=frozenset(),
        share_label="帖子正文",
        lowercase_identifier=True,
        query_path="/v3/bbs/app/api/web/share",
        query_identifier="link_id",
    ),
)


def source_definition(name):
    for definition in _DEFINITIONS:
        if definition.name == name:
            return definition
    raise CoreError("CONTENT_SOURCE_NOT_ALLOWED", 400)


def supports(name, capability):
    return any(row.name == name and capability in row.capabilities for row in _DEFINITIONS)


def source_names(capability):
    return tuple(row.name for row in _DEFINITIONS if capability in row.capabilities)


def observation_sources(capability):
    return tuple("observation." + name for name in source_names(capability))


def _url(value):
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Invalid source URL")
    parsed = urlsplit(value)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("Invalid source URL")
    return parsed


def content_target(value):
    """Extract only an allowed platform content identifier; never fetch the input URL.

    Preserve existing query handling: ordinary content URLs lose their query; the
    explicit share route requires exactly one valid link_id. Receipt URLs below
    instead reject queries entirely.
    """
    try:
        parsed = _url(value)
        if parsed.scheme != "https" or parsed.port not in {None, 443}:
            raise ValueError()
        for definition in _DEFINITIONS:
            if parsed.hostname not in definition.content_hosts:
                continue
            match = re.fullmatch(definition.content_path, parsed.path)
            identifier = match[1] if match else ""
            if definition.query_path and parsed.path == definition.query_path:
                identifiers = parse_qs(parsed.query, keep_blank_values=True).get(
                    definition.query_identifier, []
                )
                identifier = identifiers[0] if len(identifiers) == 1 else ""
            if re.fullmatch(definition.identifier, identifier):
                if definition.lowercase_identifier:
                    identifier = identifier.lower()
                return definition.name, definition.canonical_prefix + identifier
    except (TypeError, ValueError):
        pass
    raise CoreError("CONTENT_URL_NOT_ALLOWED", 400)


def public_image_url(value, source):
    """Canonical download input, with the same platform-bound CDN transforms removed."""
    try:
        definition = source_definition(source)
        parsed = _url(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in definition.image_hosts
            or parsed.port not in {None, 80, 443}
            or not parsed.path.startswith("/")
        ):
            raise ValueError()
        return urlunsplit(("https", parsed.hostname, parsed.path, "", ""))
    except (CoreError, TypeError, ValueError):
        raise CoreError("CONTENT_IMAGE_URL_NOT_ALLOWED", 400) from None


def _evidence_url(value, capability, hosts):
    parsed = _url(value)
    if (
        parsed.scheme != "https"
        or parsed.port not in {None, 443}
        or parsed.query
        or not any(
            parsed.hostname in hosts(row) for row in _DEFINITIONS if capability in row.capabilities
        )
    ):
        raise ValueError("Invalid source evidence URL")
    return parsed


def validate_reading_evidence_url(value):
    _evidence_url(value, "reading", lambda row: row.reading_evidence_hosts)


def validate_article_image_url(value):
    parsed = _evidence_url(value, "article", lambda row: row.image_hosts)
    if not parsed.path.startswith("/"):
        raise ValueError("Invalid article image reference")
