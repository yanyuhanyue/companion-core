"""Background public-image discovery over a replaceable provider and caller budget.

The Core durable job owner invokes this once per claimed attempt. No foreground wait,
task scheduler, credential lookup, model invocation or message transport lives here.
"""

import asyncio
import hashlib
import ipaddress
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

from .images import global_address, resolve_public
from .meme_catalog import INTENTS, Metadata

QUERIES = {
    "celebrate": "庆祝 成功 表情包", "comfort": "安慰 抱抱 表情包",
    "like": "喜欢 爱心 表情包", "surprised": "惊讶 表情包",
    "morning": "早安 表情包", "sleep": "晚安 表情包", "playful": "调皮 表情包",
}


def public_url(value):
    if (not isinstance(value, str) or len(value) > 2048 or "\\" in value
        or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
        raise ValueError("MEME_URL_INVALID")
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username is not None
        or url.password is not None or url.fragment or url.port not in {None, 443}):
        raise ValueError("MEME_URL_INVALID")
    try:
        ipaddress.ip_address(url.hostname)
    except ValueError:
        pass
    else:
        if not global_address(url.hostname):
            raise ValueError("MEME_NONPUBLIC_ADDRESS")
    return value


@dataclass(frozen=True)
class DiscoveryLimits:
    queries: int = 1
    requests: int = 12
    download_bytes: int = 16 * 1024 * 1024
    storage_bytes: int = 4 * 1024 * 1024
    model_cost: int = 0
    max_images: int = 3

    def __post_init__(self):
        for value, low, high in (
            (self.queries, 1, 1), (self.requests, 1, 20),
            (self.download_bytes, 1, 32 * 1024 * 1024),
            (self.storage_bytes, 1, 16 * 1024 * 1024),
            (self.model_cost, 0, 0), (self.max_images, 1, 8),
        ):
            if type(value) is not int or not low <= value <= high:
                raise ValueError("MEME_LIMIT_INVALID")


@dataclass
class Usage:
    queries: int = 0
    requests: int = 0
    download_bytes: int = 0
    storage_bytes: int = 0
    model_cost: int = 0


DEFAULT_LIMITS = DiscoveryLimits()


@dataclass(frozen=True)
class SearchImage:
    source_page: str
    asset_url: str
    description: str
    tags: tuple[str, ...] = ()
    license: str = "unknown"
    revision: str = ""
    semantic_verified: bool = False
    license_sha256: str = ""


@dataclass(frozen=True, slots=True)
class FetchHop:
    url: str
    address: str
    status: int


@dataclass(frozen=True, slots=True)
class FetchEvidence:
    requested_url: str
    final_url: str
    hops: tuple[FetchHop, ...]
    body_sha256: str
    fetched_at: str


@dataclass(frozen=True, slots=True)
class FetchedResource:
    data: bytes
    mime: str
    evidence: FetchEvidence


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    resource: FetchedResource
    path: str


@dataclass(frozen=True, slots=True)
class ReviewEvidence:
    source_page: str  # Discovery claim, not a fetched licensing attestation.
    discovered_asset_url: str
    revision: str
    fetch: FetchEvidence
    image_sha256: str
    archive_member: str = ""

    @property
    def json(self):
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @property
    def sha256(self):
        return hashlib.sha256(self.json.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ReviewApproval:
    metadata: Metadata
    evidence_sha256: str


class PublicFetcher:
    """Numeric DNS pinning and bounded redirects; no cookies or ambient proxy."""

    def __init__(self, limits, usage, check, *, transport=None, resolver=resolve_public):
        self.limits, self.usage, self.check = limits, usage, check
        self.transport, self.resolver = transport, resolver
        self._issued = []

    def verify(self, resource):
        # Only this attempt's trusted fetch layer may issue acquisition evidence. A
        # provider cannot substitute an equal-shaped object built from upstream metadata.
        if not any(resource is issued for issued in self._issued):
            raise ValueError("MEME_FETCH_EVIDENCE_INVALID")
        return resource

    async def begin_query(self):
        await self.check()
        if self.usage.queries >= self.limits.queries:
            raise ValueError("MEME_QUERY_LIMIT")
        self.usage.queries += 1

    async def get(self, url, max_bytes, *, token=None, redirects=True):
        url = public_url(url)
        requested_url, hops = url, []
        if token is not None and urlsplit(url).hostname != "api.search.brave.com":
            raise ValueError("MEME_CREDENTIAL_DESTINATION")
        delegate = self.transport or httpx.AsyncHTTPTransport(trust_env=False, retries=0)
        try:
            async with asyncio.timeout(20):
                for hop in range(4):
                    await self.check()
                    target = httpx.URL(public_url(url))
                    addresses = await self.resolver(target.host, 443)
                    if not addresses or any(not global_address(ip) for ip in addresses):
                        raise ValueError("MEME_NONPUBLIC_ADDRESS")
                    await self.check()
                    if self.usage.requests >= self.limits.requests:
                        raise ValueError("MEME_REQUEST_LIMIT")
                    self.usage.requests += 1
                    headers = {"Host": target.host, "Accept-Encoding": "identity"}
                    if token:
                        headers["X-Subscription-Token"] = token
                    request = httpx.Request(
                        "GET", target.copy_with(host=addresses[0]), headers=headers,
                        extensions={"sni_hostname": target.host,
                                    "timeout": dict.fromkeys(
                                        ("connect", "read", "write", "pool"), 5)},
                    )

                    async def trace(name, info, request=request, target=target, pin=addresses[0]):
                        if name.endswith("send_request_headers.started"):
                            await self.check()
                            if (request.url != target.copy_with(host=pin)
                                or request.headers.get("Host") != target.host
                                or request.extensions.get("sni_hostname") != target.host):
                                raise ValueError("MEME_DESTINATION_CHANGED")

                    request.extensions["trace"] = trace
                    response = await delegate.handle_async_request(request)
                    try:
                        await self.check()
                        if sum(len(k) + len(v) for k, v in response.headers.raw) > 16384:
                            raise ValueError("MEME_HEADERS_LIMIT")
                        hops.append(FetchHop(str(target), addresses[0], response.status_code))
                        if response.status_code in {301, 302, 303, 307, 308}:
                            locations = response.headers.get_list("location")
                            if token or not redirects or hop == 3 or len(locations) != 1:
                                raise ValueError("MEME_REDIRECT_REJECTED")
                            url = public_url(urljoin(url, locations[0]))
                            continue
                        if response.status_code != 200:
                            raise ValueError("MEME_HTTP_REJECTED")
                        if response.headers.get("content-encoding", "identity").strip().lower() != (
                            "identity"
                        ):
                            raise ValueError("MEME_ENCODING_REJECTED")
                        limit = min(max_bytes,
                                    self.limits.download_bytes - self.usage.download_bytes)
                        size = int(response.headers.get("content-length", "0"))
                        if limit <= 0 or not 0 <= size <= limit:
                            raise ValueError("MEME_BYTE_LIMIT")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            self.usage.download_bytes += len(chunk)
                            await self.check()
                            if len(body) + len(chunk) > limit:
                                raise ValueError("MEME_BYTE_LIMIT")
                            body.extend(chunk)
                        if not body:
                            raise ValueError("MEME_EMPTY_RESPONSE")
                        mime = response.headers.get("content-type", "").split(";")[0].lower()
                        data = bytes(body)
                        resource = FetchedResource(data, mime, FetchEvidence(
                            requested_url, str(target), tuple(hops),
                            hashlib.sha256(data).hexdigest(), datetime.now(UTC).isoformat(),
                        ))
                        self._issued.append(resource)
                        return resource
                    finally:
                        await response.aclose()
        finally:
            if self.transport is None:
                await delegate.aclose()


class BraveImages:
    def __init__(self, token):
        self.token = token

    async def discover(self, intent, fetcher):
        if not isinstance(self.token, str) or not self.token.strip():
            raise ValueError("MEME_SEARCH_NOT_CONFIGURED")
        await fetcher.begin_query()
        url = "https://api.search.brave.com/res/v1/images/search?" + urlencode({
            "q": QUERIES[intent], "count": fetcher.limits.max_images,
            "safesearch": "strict", "search_lang": "zh-hans", "country": "ALL",
        })
        resource = await fetcher.get(url, 512 * 1024, token=self.token)
        value = json.loads(resource.data)
        if not isinstance(value, dict) or not isinstance(value.get("results"), list):
            raise ValueError("MEME_SEARCH_RESPONSE_INVALID")
        for row in value["results"][:fetcher.limits.max_images]:
            try:
                source = public_url(row["url"])
                asset = public_url(row["properties"]["url"])
                title = row.get("title") or "Public image search candidate"
                if not isinstance(title, str) or len(title) > 2000:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            yield SearchImage(source, asset, title), None


async def run_discovery(job_key, intent, provider, catalog, *, budget, check, review,
                        commit_check, limits=DEFAULT_LIMITS, transport=None,
                        resolver=resolve_public):
    """Run under a durable claimed job, with idempotent budget key supplied by its owner.

    budget.reserve(job_key, limits) must durably deny a previously consumed/unknown attempt;
    its returned reservation exposes async check(), finish(Usage), unknown(Usage). Exceptions
    conservatively retain reservations. review(SearchImage, PreparedImage, ReviewEvidence)
    returns a source/byte-bound ReviewApproval or None, without unreserved network/model
    work. SearchImage.asset_url is the actual fetched URL (archive URL for a pack member).
    commit_check is the synchronous local STOP/reply-generation/recall commit fence.
    """
    if intent not in INTENTS:
        raise ValueError("MEME_INTENT_INVALID")
    await check()
    reservation = await budget.reserve(job_key, limits)
    usage = Usage()

    async def fence():
        await check()
        await reservation.check()

    fetcher = PublicFetcher(limits, usage, fence, transport=transport, resolver=resolver)
    accepted, rejected, pending = [], 0, []
    try:
        await fence()
        number = 0
        async for candidate, supplied in provider.discover(intent, fetcher):
            number += 1
            if number > limits.max_images:
                break
            await fence()
            if supplied is None:
                resource = await fetcher.get(
                    candidate.asset_url, catalog.library.max_bytes
                )
                if resource.mime not in {"image/png", "image/jpeg", "image/gif"}:
                    rejected += 1
                    continue
                data, member_path = resource.data, ""
            else:
                if not isinstance(supplied, ArchiveMember):
                    raise ValueError("MEME_FETCH_EVIDENCE_REQUIRED")
                resource = fetcher.verify(supplied.resource)
                # Rebind the claimed member to the actual fetched archive, not supplied
                # bytes or a fabricated raw URL. Never extract anything to disk.
                from .meme_packs import bounded_archive, relative_path

                member_path = relative_path(supplied.path).as_posix()
                with bounded_archive(resource.data) as archive, archive.open(member_path) as stream:
                    data = stream.read(catalog.library.max_bytes + 1)
            fetcher.verify(resource)
            image = catalog.library.validate_bytes(data)
            if image is None:
                rejected += 1
                continue
            evidence = ReviewEvidence(
                candidate.source_page, candidate.asset_url, candidate.revision,
                resource.evidence, image.sha256, member_path,
            )
            reviewed_candidate = replace(candidate, asset_url=resource.evidence.final_url)
            await fence()
            approval = await review(reviewed_candidate, image, evidence)
            await fence()
            if approval is None:
                rejected += 1
                continue
            if (not isinstance(approval, ReviewApproval)
                or approval.evidence_sha256 != evidence.sha256
                or not isinstance(approval.metadata, Metadata)):
                raise ValueError("MEME_REVIEW_INVALID")
            metadata = approval.metadata
            if ((metadata.source_page, metadata.asset_url, metadata.revision, metadata.fetched_at)
                != (evidence.source_page, evidence.fetch.final_url,
                    evidence.revision, evidence.fetch.fetched_at)
                or metadata.acquisition_json not in ("", evidence.json)):
                raise ValueError("MEME_REVIEW_SOURCE_MISMATCH")
            # Persist exactly the acquisition the reviewer saw. Never change a reviewed
            # source after approval; the digest also covers bytes and every redirect hop.
            metadata = replace(metadata, acquisition_json=evidence.json)
            existed = catalog.db.execute("SELECT active FROM meme_assets WHERE sha=?",
                                         (image.sha256,)).fetchone()
            if existed and not existed[0]:
                rejected += 1
                continue
            if not existed and usage.storage_bytes + len(data) > limits.storage_bytes:
                raise ValueError("MEME_STORAGE_BUDGET")
            sha = catalog.add(data, metadata, check=commit_check)
            if not existed:
                usage.storage_bytes += len(data)
            from .meme_semantic import managed, usable

            awaiting_caption = managed(catalog.db, sha) and not usable(catalog, sha)
            destination = pending if awaiting_caption else accepted
            if sha not in destination:
                destination.append(sha)
        await fence()
        await reservation.finish(usage)
        result = {"status": "completed", "accepted": accepted, "rejected": rejected,
                  "usage": usage.__dict__}
        if pending:
            result["pending_caption"] = pending
        return result
    except BaseException:
        # Cancellation, process-loss recovery and late provider errors never imply refunds.
        await reservation.unknown(usage)
        raise
