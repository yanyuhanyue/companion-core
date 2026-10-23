"""Source-bound caption cache and incremental local index in the existing spool.

This is a single-step client of Core jobs, not another scheduler. Model usage receipts
are retained independently of semantic eligibility, including after cancellation.
"""

import hashlib
import re

from companion_meme_protocol import caption_text as caption_text

from .meme_caption import (
    CaptionAsset,
    CaptionContext,
    CaptionIdentity,
    CaptionOutcome,
    CaptionRequest,
    CaptionResult,
    EmbeddingBatch,
    EmbeddingSpec,
    UsageReceipt,
    canonical,
    caption_cache_key,
    digest,
    parse,
    sample_frames,
    source_evidence,
    strict_json,
)


def installed(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name='meme_semantic_config'").fetchone()


def profile(db):
    row = db.execute("SELECT value FROM meme_semantic_config WHERE singleton=1").fetchone()
    return strict_json(row[0]) if row else None


def source_hash(metadata):
    return digest(source_evidence(metadata))


def receipt_follows(previous, current):
    if previous.usage_request_id != current.usage_request_id:
        return False
    order = {"RESERVED": 0, "UNKNOWN": 1, "SETTLED": 2}
    if order[current.state] < order[previous.state]:
        return False
    return all(
        getattr(previous, field) is None or getattr(previous, field) == getattr(current, field)
        for field in ("input_tokens", "output_tokens", "billed_micro_usd")
    )


def managed(db, sha):
    return bool(
        installed(db)
        and db.execute("SELECT 1 FROM meme_semantic_assets WHERE sha=?", (sha,)).fetchone()
    )


def terminal_without_usage(binding):
    if not binding or binding[0] not in {"FAILED", "CANCELLED"} or binding[2] is not None:
        return False
    try:
        outcome = parse(CaptionOutcome, binding[1])
        return outcome.state == binding[0] and outcome.usage is None
    except (ValueError, TypeError):
        return False


def stage_if_enabled(catalog, sha, metadata):
    if not installed(catalog.db):
        return
    settings = profile(catalog.db)
    if settings is None:
        return
    catalog.db.execute(
        "INSERT OR IGNORE INTO meme_semantic_assets VALUES (?,?,NULL,NULL,'PENDING')",
        (sha, source_hash(metadata)),
    )
    binding = catalog.db.execute(
        "SELECT b.state,b.outcome_json,b.usage_json FROM meme_semantic_assets a "
        "JOIN meme_caption_bindings b ON b.request_sha=a.binding_request "
        "WHERE a.sha=? AND a.state='QUARANTINED'", (sha,),
    ).fetchone()
    if terminal_without_usage(binding):
        catalog.db.execute("UPDATE meme_semantic_assets SET state='PENDING' WHERE sha=?", (sha,))


def invalidate_in_transaction(db, sha):
    if installed(db):
        db.execute("UPDATE meme_semantic_assets SET state='INVALID' WHERE sha=?", (sha,))
        db.execute("DELETE FROM meme_semantic_vectors WHERE sha=?", (sha,))


def asset_snapshot(catalog, sha):
    row = catalog.db.execute(
        "SELECT data,metadata,active FROM meme_assets WHERE sha=?", (sha,)
    ).fetchone()
    if not row or row[2] != 1 or not row[0] or hashlib.sha256(row[0]).hexdigest() != sha:
        raise ValueError("MEME_SEMANTIC_ASSET_INVALID")
    metadata = catalog.metadata(row[1])
    return row[0], metadata, source_hash(metadata)


def usable(catalog, sha, offer_key=None):
    if not managed(catalog.db, sha):
        return True
    row = catalog.db.execute(
        "SELECT source_sha,cache_key,state,binding_request FROM meme_semantic_assets WHERE sha=?",
        (sha,),
    ).fetchone()
    if row[2] != "READY":
        return False
    try:
        _, _, current = asset_snapshot(catalog, sha)
        settings = profile(catalog.db)
        binding = catalog.db.execute(
            "SELECT state,source_sha,cache_key FROM meme_caption_bindings WHERE request_sha=?",
            (row[3],),
        ).fetchone()
        if not binding or tuple(binding) != ("SUCCEEDED", current, row[1]):
            return False
        identity = parse(CaptionIdentity, settings["caption"])
        cached = catalog.db.execute(
            "SELECT identity_json,result_json FROM meme_caption_cache WHERE cache_key=?",
            (row[1],),
        ).fetchone()
        if (
            current != row[0]
            or not cached
            or parse(CaptionIdentity, cached[0]) != identity
            or row[1] != caption_cache_key(sha, identity)
            or not caption_eligible(parse(CaptionResult, cached[1]), settings["minimum_confidence"])
        ):
            return False
        if offer_key is not None:
            proof = catalog.db.execute(
                "SELECT profile_sha,proof_json FROM meme_semantic_offers WHERE key=?", (offer_key,)
            ).fetchone()
            if not proof or proof[0] != digest(settings):
                return False
            expected = {
                "source_sha": current,
                "cache_key": row[1],
                "caption_sha": digest(strict_json(cached[1])),
            }
            if strict_json(proof[1]).get(sha) != expected:
                return False
        return True
    except (ValueError, TypeError, KeyError):
        return False


def caption_eligible(result, minimum):
    if (
        result.confidence < minimum
        or result.content_status != "clear"
        or result.target == "uncertain"
        or result.tone == "uncertain"
        or result.temporal_status in {"changes", "uncertain"}
        or not result.suitable
    ):
        return False
    # Visible text is evidence too: a benign visual label cannot erase a direct insult.
    if re.search(
        r"你.{0,4}(?:笨|蠢|废物)|活该|滚开|去死", result.visible_text
    ) and result.tone not in {"mocking", "sarcastic", "angry"}:
        return False
    return True


class SemanticLibrary:
    def __init__(self, catalog, identity, embedding_spec, *, minimum_confidence=0.8):
        self.catalog, self.db = catalog, catalog.db
        self.identity = parse(CaptionIdentity, identity)
        self.embedding_spec = parse(EmbeddingSpec, embedding_spec)
        if type(minimum_confidence) not in (int, float) or not 0.5 <= minimum_confidence <= 1:
            raise ValueError("MEME_CONFIDENCE_LIMIT")
        self.settings = {
            "caption": self.identity.model_dump(mode="json"),
            "embedding": self.embedding_spec.model_dump(mode="json"),
            "minimum_confidence": minimum_confidence,
        }
        for sql in (
            "CREATE TABLE IF NOT EXISTS meme_semantic_config "
            "(singleton INTEGER PRIMARY KEY CHECK(singleton=1),value TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS meme_semantic_assets "
            "(sha TEXT PRIMARY KEY,source_sha TEXT,cache_key TEXT,binding_request TEXT,state TEXT)",
            "CREATE TABLE IF NOT EXISTS meme_caption_cache (cache_key TEXT PRIMARY KEY,sha TEXT,"
            "identity_json TEXT,result_json TEXT,usage_json TEXT)",
            "CREATE TABLE IF NOT EXISTS meme_caption_bindings (request_sha TEXT PRIMARY KEY,"
            "sha TEXT,source_sha TEXT,cache_key TEXT,context_json TEXT,job_id TEXT,state TEXT,"
            "outcome_json TEXT,usage_json TEXT)",
            "CREATE TABLE IF NOT EXISTS meme_semantic_vectors "
            "(sha TEXT PRIMARY KEY,source_sha TEXT,cache_key TEXT,caption_sha TEXT,"
            "model_version TEXT,dimensions INTEGER,vector_json TEXT)",
            "CREATE TABLE IF NOT EXISTS meme_semantic_offers "
            "(key TEXT PRIMARY KEY,query_sha TEXT,profile_sha TEXT,proof_json TEXT)",
        ):
            self.db.execute(sql)
        with self.catalog.atomic():
            self.db.execute(
                "INSERT OR REPLACE INTO meme_semantic_config VALUES (1,?)",
                (canonical(self.settings),),
            )
            # Existing assets are staged too; old process instances cannot bypass the gate.
            for sha, raw in self.db.execute("SELECT sha,metadata FROM meme_assets WHERE active=1"):
                stage_if_enabled(catalog, sha, catalog.metadata(raw))

    def _profile_check(self):
        if profile(self.db) != self.settings:
            raise ValueError("MEME_SEMANTIC_PROFILE_CHANGED")

    def _current(self, sha):
        self._profile_check()
        data, metadata, source = asset_snapshot(self.catalog, sha)
        row = self.db.execute(
            "SELECT source_sha,state FROM meme_semantic_assets WHERE sha=?", (sha,)
        ).fetchone()
        if not row or row[0] != source or row[1] == "INVALID":
            raise ValueError("MEME_SEMANTIC_SOURCE_CHANGED")
        return data, metadata, source

    def _request(self, sha, context):
        data, _, source = self._current(sha)
        image = self.catalog.library.validate_bytes(data)
        if image is None:
            raise ValueError("MEME_ASSET_INVALID")
        return CaptionRequest(
            CaptionAsset(data, sha, source, image.mime_type, len(data)),
            parse(CaptionContext, context),
            self.identity,
        )

    def _status(self, sha, *, cache_hit=False):
        row = self.db.execute(
            "SELECT state,cache_key,binding_request FROM meme_semantic_assets WHERE sha=?", (sha,)
        ).fetchone()
        usage = self.db.execute(
            "SELECT usage_json FROM meme_caption_bindings WHERE request_sha=?", (row[2],)
        ).fetchone()
        return {
            "state": row[0],
            "cache_hit": cache_hit,
            "cache_key": row[1],
            "usage": strict_json(usage[0]) if usage and usage[0] else None,
        }

    def rebind_source(self, sha, metadata, *, check):
        """Caller has a fresh source/license approval; removed bytes cannot be resurrected."""
        with self.catalog.atomic(check):
            check()
            self._profile_check()
            asset_snapshot(self.catalog, sha)
            source = source_hash(metadata)
            self.db.execute(
                "UPDATE meme_assets SET metadata=? WHERE sha=?", (canonical(metadata.__dict__), sha)
            )
            self.db.execute(
                "UPDATE meme_semantic_assets SET source_sha=?,state='PENDING',"
                "cache_key=NULL,binding_request=NULL WHERE sha=?",
                (source, sha),
            )
            self.db.execute("DELETE FROM meme_semantic_vectors WHERE sha=?", (sha,))

    def invalidate(self, sha):
        with self.catalog.atomic():
            invalidate_in_transaction(self.db, sha)

    async def advance(self, sha, client, context, *, check, commit_check):
        """Submit once or poll once. The caller/ Core worker decides when another step runs."""
        await check()
        request = self._request(sha, context)
        current_request = request
        with self.catalog.atomic(commit_check):
            commit_check()
            row = self.db.execute(
                "SELECT state,cache_key,binding_request FROM meme_semantic_assets WHERE sha=?",
                (sha,),
            ).fetchone()
            selected_request = row[2] if row[1] == request.cache_key else request.sha256
            binding = self.db.execute(
                "SELECT context_json,job_id,state,outcome_json,usage_json "
                "FROM meme_caption_bindings WHERE request_sha=?",
                (selected_request,),
            ).fetchone()
            handoff = selected_request != request.sha256 and terminal_without_usage(
                binding[2:] if binding else None,
            )
            if handoff:
                selected_request = request.sha256
                binding = self.db.execute(
                    "SELECT context_json,job_id,state,outcome_json,usage_json "
                    "FROM meme_caption_bindings WHERE request_sha=?", (selected_request,),
                ).fetchone()
            if (not handoff and row[1] == request.cache_key
                and row[0] in {"READY", "QUARANTINED"}):
                cached = self.db.execute(
                    "SELECT result_json FROM meme_caption_cache WHERE cache_key=?",
                    (request.cache_key,),
                ).fetchone()
                if cached and binding and binding[2] == "SUCCEEDED":
                    state = (
                        "READY"
                        if caption_eligible(
                            parse(CaptionResult, cached[0]), self.settings["minimum_confidence"]
                        )
                        else "QUARANTINED"
                    )
                    self.db.execute(
                        "UPDATE meme_semantic_assets SET state=? WHERE sha=?", (state, sha)
                    )
                return self._status(
                    sha, cache_hit=bool(cached and binding and binding[2] == "SUCCEEDED")
                )
            if binding:
                request = self._request(sha, strict_json(binding[0]))
                if request.sha256 != selected_request:
                    raise ValueError("MEME_CAPTION_STALE_BINDING")
                state = "PENDING" if binding[2] in {"QUEUED", "RUNNING"} else "QUARANTINED"
                cached = self.db.execute(
                    "SELECT result_json FROM meme_caption_cache WHERE cache_key=?",
                    (request.cache_key,),
                ).fetchone()
                if (
                    binding[2] == "SUCCEEDED"
                    and cached
                    and caption_eligible(
                        parse(CaptionResult, cached[0]), self.settings["minimum_confidence"]
                    )
                ):
                    state = "READY"
                self.db.execute(
                    "UPDATE meme_semantic_assets SET state=?,cache_key=?,"
                    "binding_request=? WHERE sha=?",
                    (state, request.cache_key, request.sha256, sha),
                )
                if binding[2] not in {"QUEUED", "RUNNING"}:
                    return self._status(sha, cache_hit=bool(binding[2] == "SUCCEEDED" and cached))
                submit, job_id = False, binding[1]
            else:
                if self.db.execute("SELECT COUNT(*) FROM meme_caption_bindings").fetchone()[0] >= (
                    self.catalog.max_bindings
                ):
                    raise ValueError("MEME_CAPTION_BINDING_LIMIT")
                self.db.execute(
                    "INSERT INTO meme_caption_bindings VALUES (?,?,?,?,?,NULL,"
                    "'SUBMITTING',NULL,NULL)",
                    (
                        request.sha256,
                        sha,
                        request.asset.source_evidence_sha256,
                        request.cache_key,
                        request.context.model_dump_json(),
                    ),
                )
                self.db.execute(
                    "UPDATE meme_semantic_assets SET state='PENDING',cache_key=?,"
                    "binding_request=? WHERE sha=?",
                    (request.cache_key, request.sha256, sha),
                )
                submit, job_id = True, None
        try:
            await check()
            self._current(sha)
            value = await client.submit(request) if submit else await client.get(job_id)
            result = await self.accept(request, value, check=check, commit_check=commit_check)
            if request.sha256 != current_request.sha256:
                # One poll confirms the old operation ended before admission. A later
                # step can submit the newly authorized context, retaining the old proof.
                with self.catalog.atomic(commit_check):
                    commit_check()
                    binding = self.db.execute(
                        "SELECT b.state,b.outcome_json,b.usage_json FROM meme_semantic_assets a "
                        "JOIN meme_caption_bindings b ON b.request_sha=a.binding_request "
                        "WHERE a.sha=? AND a.binding_request=?", (sha, request.sha256),
                    ).fetchone()
                    if terminal_without_usage(binding):
                        self.db.execute(
                            "UPDATE meme_semantic_assets SET state='PENDING' WHERE sha=?", (sha,),
                        )
                        result = self._status(sha)
            return result
        except BaseException:
            with self.catalog.atomic():
                self.db.execute(
                    "UPDATE meme_caption_bindings SET state=CASE WHEN "
                    "json_extract(usage_json,'$.state')='SETTLED' THEN 'FAILED' "
                    "ELSE 'UNKNOWN' END "
                    "WHERE request_sha=? AND state IN ('SUBMITTING','QUEUED','RUNNING')",
                    (request.sha256,),
                )
                self.db.execute(
                    "UPDATE meme_semantic_assets SET state='QUARANTINED' "
                    "WHERE binding_request=? AND state='PENDING'",
                    (request.sha256,),
                )
            raise

    async def accept(self, request, value, *, check, commit_check):
        """Also accepts Core reconciliation of an existing request; never dispatches a model."""
        raw = (
            value.model_dump(mode="json")
            if isinstance(value, CaptionOutcome)
            else (strict_json(value) if isinstance(value, str) else value)
        )
        if not isinstance(raw, dict) or any(
            raw.get(name) != expected
            for name, expected in (
                ("asset_sha256", request.asset.asset_sha256),
                ("source_evidence_sha256", request.asset.source_evidence_sha256),
                ("request_sha256", request.sha256),
                ("caption_model_version", self.identity.model_version),
                ("prompt_version", self.identity.prompt_version),
            )
        ):
            raise ValueError("MEME_CAPTION_IDENTITY_MISMATCH")
        receipt = parse(UsageReceipt, raw["usage"]) if raw.get("usage") is not None else None
        with self.catalog.atomic():
            binding = self.db.execute(
                "SELECT job_id,usage_json,state FROM meme_caption_bindings WHERE request_sha=?",
                (request.sha256,),
            ).fetchone()
            if not binding or binding[0] is not None and raw.get("job_id") != binding[0]:
                raise ValueError("MEME_CAPTION_JOB_MISMATCH")
            if binding[2] == "SUCCEEDED" and raw.get("state") != "SUCCEEDED":
                raise ValueError("MEME_CAPTION_TERMINAL_STATE")
            if receipt is None and (
                binding[1] or raw.get("state") not in {"QUEUED", "FAILED", "CANCELLED"}
            ):
                raise ValueError("MEME_CAPTION_USAGE_REQUIRED")
            if binding[1] and not receipt_follows(parse(UsageReceipt, binding[1]), receipt):
                raise ValueError("MEME_CAPTION_USAGE_CONFLICT")
            # Accounting survives malformed semantics, deletion and a late cancelled result.
            if receipt is not None:
                self.db.execute(
                    "UPDATE meme_caption_bindings SET usage_json=? WHERE request_sha=?",
                    (receipt.model_dump_json(), request.sha256),
                )
        outcome = parse(CaptionOutcome, raw)
        await check()
        with self.catalog.atomic(commit_check):
            commit_check()
            data, _, source = self._current(request.asset.asset_sha256)
            row = self.db.execute(
                "SELECT binding_request FROM meme_semantic_assets WHERE sha=?",
                (request.asset.asset_sha256,),
            ).fetchone()
            if source != request.asset.source_evidence_sha256 or row[0] != request.sha256:
                raise ValueError("MEME_CAPTION_STALE_BINDING")
            state = "PENDING" if outcome.state in {"QUEUED", "RUNNING"} else "QUARANTINED"
            if outcome.result is not None:
                sampling, _ = sample_frames(data, self.catalog.library)
                if outcome.result.sampled_frames != sampling:
                    raise ValueError("MEME_CAPTION_SAMPLE_MISMATCH")
                cached = self.db.execute(
                    "SELECT result_json,usage_json FROM meme_caption_cache WHERE cache_key=?",
                    (request.cache_key,),
                ).fetchone()
                if cached and (
                    (not outcome.cache_hit and binding[2] != "SUCCEEDED")
                    or cached[0] != outcome.result.model_dump_json()
                    or not receipt_follows(parse(UsageReceipt, cached[1]), outcome.usage)
                ):
                    raise ValueError("MEME_CAPTION_CACHE_CONFLICT")
                if not cached:
                    if self.db.execute("SELECT COUNT(*) FROM meme_caption_cache").fetchone()[0] >= (
                        self.catalog.max_assets * 4
                    ):
                        raise ValueError("MEME_CAPTION_CACHE_LIMIT")
                    self.db.execute(
                        "INSERT INTO meme_caption_cache VALUES (?,?,?,?,?)",
                        (
                            request.cache_key,
                            request.asset.asset_sha256,
                            self.identity.model_dump_json(),
                            outcome.result.model_dump_json(),
                            outcome.usage.model_dump_json(),
                        ),
                    )
                else:
                    self.db.execute(
                        "UPDATE meme_caption_cache SET usage_json=? WHERE cache_key=?",
                        (outcome.usage.model_dump_json(), request.cache_key),
                    )
                if caption_eligible(outcome.result, self.settings["minimum_confidence"]):
                    state = "READY"
            self.db.execute(
                "UPDATE meme_caption_bindings SET job_id=?,state=?,outcome_json=? "
                "WHERE request_sha=?",
                (outcome.job_id, outcome.state, outcome.model_dump_json(), request.sha256),
            )
            self.db.execute(
                "UPDATE meme_semantic_assets SET state=? WHERE sha=?",
                (state, request.asset.asset_sha256),
            )
        return self._status(request.asset.asset_sha256, cache_hit=outcome.cache_hit)

    def caption(self, sha):
        self._current(sha)
        if not usable(self.catalog, sha):
            return None
        row = self.db.execute(
            "SELECT c.result_json,a.source_sha,a.cache_key FROM "
            "meme_semantic_assets a JOIN meme_caption_cache c "
            "ON c.cache_key=a.cache_key WHERE a.sha=?",
            (sha,),
        ).fetchone()
        return parse(CaptionResult, row[0]), row[1], row[2]

    async def index(self, sha, embedder, *, check, commit_check):
        await check()
        value = self.caption(sha)
        if not value:
            return False
        caption, source, cache_key = value
        caption_sha = digest(caption.model_dump(mode="json"))
        version = self.embedding_spec.model_version
        dimensions = self.embedding_spec.dimensions
        existing = self.db.execute(
            "SELECT source_sha,cache_key,caption_sha,model_version,dimensions "
            "FROM meme_semantic_vectors WHERE sha=?",
            (sha,),
        ).fetchone()
        identity = source, cache_key, caption_sha, version, dimensions
        if existing and tuple(existing) == identity:
            return True
        text = caption_text(caption)
        response = parse(EmbeddingBatch, await embedder.embed((text,)))
        await check()
        if (response.model_version, response.dimensions, len(response.vectors)) != (
            version,
            dimensions,
            1,
        ):
            raise ValueError("MEME_EMBEDDING_VERSION_MISMATCH")
        with self.catalog.atomic(commit_check):
            commit_check()
            current = self.caption(sha)
            if not current or (
                current[1],
                current[2],
                digest(current[0].model_dump(mode="json")),
            ) != (source, cache_key, caption_sha):
                raise ValueError("MEME_SEMANTIC_SOURCE_CHANGED")
            self.db.execute(
                "INSERT OR REPLACE INTO meme_semantic_vectors VALUES (?,?,?,?,?,?,?)",
                (sha, *identity, canonical(response.vectors[0])),
            )
        return True

    def candidates(self, delivery_key, query, *, query_vector=None, limit=8, check):
        from .meme_semantic_search import candidates

        return candidates(
            self, delivery_key, query, query_vector=query_vector, limit=limit, check=check
        )
