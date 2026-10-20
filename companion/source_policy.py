"""Fixed, immutable source definitions shared with the isolated Reader image.

Standard library only: no Core validation, storage, authentication or I/O imports.
"""

from dataclasses import dataclass
from typing import Literal

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
DEFINITIONS = (
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
    for definition in DEFINITIONS:
        if definition.name == name:
            return definition
    raise KeyError(name)
