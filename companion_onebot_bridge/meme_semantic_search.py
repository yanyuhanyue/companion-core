"""Bounded context retrieval over already understood captions, without vision calls."""

import math
import re

from pydantic import Field

from .meme_caption import (
    Contract,
    EmbeddingBatch,
    Intent,
    Target,
    canonical,
    digest,
    parse,
    strict_json,
)
from .meme_catalog import key_hash
from .meme_semantic import caption_text


class SemanticQuery(Contract):
    text: str = Field(min_length=1, max_length=2000)
    intent: Intent
    target: Target = "neutral"


def tokens(text):
    terms = set(re.findall(r"[a-z0-9]{2,}", text.casefold()))
    for segment in re.findall(r"[\u3400-\u9fff]+", text):
        terms.update(segment[i : i + 2] for i in range(len(segment) - 1))
    return terms


def context_allows(caption, query):
    if query.intent not in caption.suitable or query.intent in caption.avoid:
        return False
    if caption.target not in {"neutral", query.target}:
        return False
    if caption.tone == "self_deprecating" and query.target != "self":
        return False
    if query.intent == "comfort" and caption.tone not in {"warm", "neutral", "sad"}:
        return False
    if query.intent == "comfort" and re.search(
        r"嘲笑|嘲讽|活该|哈哈|笨蛋|废物|mock|sarcas", caption_text(caption), re.I
    ):
        return False
    return True


def cosine(left, right):
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    )


def candidates(semantics, delivery_key, query, *, query_vector=None, limit=8, check):
    query = parse(SemanticQuery, query)
    if type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError("MEME_CANDIDATE_LIMIT")
    key = key_hash(delivery_key)
    vector = None
    spec = semantics.embedding_spec
    if query_vector is not None:
        vector = parse(EmbeddingBatch, query_vector)
        if (vector.model_version, vector.dimensions, len(vector.vectors)) != (
            spec.model_version,
            spec.dimensions,
            1,
        ):
            raise ValueError("MEME_EMBEDDING_VERSION_MISMATCH")
    catalog, db = semantics.catalog, semantics.db
    query_sha = digest(query.model_dump(mode="json"))
    profile_sha = digest(semantics.settings)
    with catalog.atomic(check):
        check()
        semantics._profile_check()
        if (
            not catalog.library.enabled
            or db.execute(
                "SELECT 1 FROM meme_pins WHERE key=? AND state<>'PREPARED'", (key,)
            ).fetchone()
        ):
            return []
        previous = db.execute(
            "SELECT query_sha,profile_sha,proof_json FROM meme_semantic_offers WHERE key=?", (key,)
        ).fetchone()
        if previous and (previous[0], previous[1]) != (query_sha, profile_sha):
            raise ValueError("MEME_OFFER_CONFLICT")
        existing_ids = db.execute("SELECT ids FROM meme_offers WHERE key=?", (key,)).fetchone()
        if existing_ids and not previous:
            raise ValueError("MEME_OFFER_CONFLICT")
        ranks, proofs = [], {}
        for sha, raw_metadata in db.execute(
            "SELECT sha,metadata FROM meme_assets WHERE active=1 ORDER BY sha LIMIT ?",
            (catalog.max_assets,),
        ).fetchall():
            try:
                value = semantics.caption(sha)
            except ValueError:
                continue
            if not value or catalog._blocked(sha, key):
                continue
            caption, source, cache_key = value
            if not context_allows(caption, query):
                continue
            overlap = len(tokens(query.text) & tokens(caption_text(caption)))
            score = min(0.49, overlap / 20) if overlap else 0.0
            caption_sha = digest(caption.model_dump(mode="json"))
            stored = db.execute(
                "SELECT source_sha,cache_key,caption_sha,model_version,dimensions,"
                "vector_json FROM meme_semantic_vectors WHERE sha=?",
                (sha,),
            ).fetchone()
            if (
                vector is not None
                and stored
                and tuple(stored[:5])
                == (source, cache_key, caption_sha, spec.model_version, spec.dimensions)
            ):
                try:
                    passage = parse(
                        EmbeddingBatch,
                        {
                            "model_version": spec.model_version,
                            "dimensions": spec.dimensions,
                            "local": True,
                            "vectors": [strict_json(stored[5])],
                        },
                    )
                    similarity = cosine(passage.vectors[0], vector.vectors[0])
                    if similarity >= 0.5:
                        score += similarity
                except (ValueError, TypeError):
                    pass  # Invalid/stale vectors only fall back to usable caption text.
            if score <= 0:
                continue
            proof = {"source_sha": source, "cache_key": cache_key, "caption_sha": caption_sha}
            if previous and strict_json(previous[2]).get(sha) != proof:
                continue
            metadata = catalog.metadata(raw_metadata)
            description = (
                caption.description
                + "\n图中文字："
                + caption.visible_text
                + "\n语气："
                + caption.tone
                + "；对象："
                + caption.target
            )[:2000]
            ranks.append(
                (
                    score,
                    sha,
                    {
                        "candidate_id": sha,
                        "description": description,
                        "tags": list(caption.tags),
                        "category": metadata.category,
                    },
                )
            )
            proofs[sha] = proof
        ranks.sort(key=lambda row: (-row[0], row[1]))
        selected = ranks[:limit]
        if previous:
            allowed = strict_json(existing_ids[0])
            selected = [row for row in ranks if row[1] in allowed][:limit]
        else:
            if db.execute("SELECT COUNT(*) FROM meme_offers").fetchone()[0] >= catalog.max_bindings:
                raise ValueError("MEME_BINDING_LIMIT")
            db.execute(
                "INSERT INTO meme_offers VALUES (?,?,NULL,?)",
                (key, query.intent, canonical([row[1] for row in selected])),
            )
            db.execute(
                "INSERT INTO meme_semantic_offers VALUES (?,?,?,?)",
                (
                    key,
                    query_sha,
                    profile_sha,
                    canonical({row[1]: proofs[row[1]] for row in selected}),
                ),
            )
        return [row[2] for row in selected]
