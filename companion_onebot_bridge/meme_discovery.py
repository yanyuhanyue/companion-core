"""Background public-image discovery over a replaceable provider and caller budget.

The Core durable job owner invokes this once per claimed attempt. No foreground wait,
task scheduler, credential lookup, model invocation or message transport lives here.
"""

import json
from dataclasses import replace
from urllib.parse import urlencode

from companion_meme_sources import (
    DEFAULT_LIMITS,
    INTENTS,
    ArchiveMember,
    Metadata,
    RepositoryImage,
    ReviewApproval,
    ReviewEvidence,
    SearchImage,
    Usage,
)
from companion_meme_sources.fetch import PublicFetcher, public_url, resolve_public

QUERIES = {
    "celebrate": "庆祝 成功 表情包", "comfort": "安慰 抱抱 表情包",
    "like": "喜欢 爱心 表情包", "surprised": "惊讶 表情包",
    "morning": "早安 表情包", "sleep": "晚安 表情包", "playful": "调皮 表情包",
}


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
            source_files = None
            if supplied is None:
                resource = await fetcher.get(
                    candidate.asset_url, catalog.library.max_bytes
                )
                if resource.mime not in {"image/png", "image/jpeg", "image/gif"}:
                    rejected += 1
                    continue
                data, member_path = resource.data, ""
            elif isinstance(supplied, RepositoryImage):
                from companion_meme_sources.community import verify_repository_image

                resource = fetcher.verify(supplied.resource)
                for witness in (supplied.manifest, supplied.license, supplied.listing):
                    fetcher.verify(witness)
                verify_repository_image(candidate, supplied)
                data, member_path = resource.data, ""
                source_files = {role: getattr(supplied, role).evidence
                                for role in ("manifest", "license", "listing")}
            else:
                if not isinstance(supplied, ArchiveMember):
                    raise ValueError("MEME_FETCH_EVIDENCE_REQUIRED")
                resource = fetcher.verify(supplied.resource)
                # Rebind the claimed member to the actual fetched archive, not supplied
                # bytes or a fabricated raw URL. Never extract anything to disk.
                from companion_meme_sources.community import bounded_archive, relative_path

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
                resource.evidence, image.sha256, member_path, source_files,
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
