"""Local Chinese embeddings; SQLite remains the only memory/index store."""

import hashlib
from functools import lru_cache
from pathlib import Path

from companion.contracts import CoreError

MODEL = "BAAI/bge-small-zh-v1.5"
REPOSITORY = "Qdrant/bge-small-zh-v1.5"
REVISION = "46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59"
INDEX_VERSION = f"{MODEL}@{REVISION}"


@lru_cache(maxsize=2)
def encoder(cache_dir):
    from fastembed import TextEmbedding
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    options = dict(
        repo_id=REPOSITORY,
        revision=REVISION,
        cache_dir=cache_dir,
        allow_patterns=["*.json", "*.txt", "model_optimized.onnx"],
        max_workers=2,
    )
    try:
        path = snapshot_download(**options, local_files_only=True)
        if not (Path(path) / "model_optimized.onnx").is_file():
            raise LocalEntryNotFoundError("Incomplete model snapshot")
    except LocalEntryNotFoundError:
        path = snapshot_download(**options)
    return TextEmbedding(
        MODEL,
        specific_model_path=path,
        cache_dir=cache_dir,
        threads=2,
        providers=["CPUExecutionProvider"],
        local_files_only=True,
    )


class SemanticIndex:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings

    def warmup(self):
        try:
            return encoder(str(Path(self.settings.embedding_cache_dir).resolve()))
        except Exception as error:
            raise CoreError("MEMORY_INDEX_UNAVAILABLE", 503) from error

    def scores(self, query, rows):
        if not rows:
            return {}
        import numpy as np

        model = self.warmup()
        cached = {
            row["memory_id"]: row
            for row in self.store.rows(
                "SELECT * FROM memory_vectors WHERE model_version=?", (INDEX_VERSION,)
            )
        }
        vectors, missing = {}, []
        for row in rows:
            key = hashlib.sha256(row["content"].encode()).hexdigest()
            old = cached.get(row["memory_id"])
            if old and old["content_hash"] == key:
                vectors[row["memory_id"]] = np.frombuffer(old["vector"], dtype="<f4")
            else:
                missing.append((row, key))
        if missing:
            encoded = model.passage_embed([row["content"] for row, _ in missing], batch_size=32)
            for (row, key), vector in zip(missing, encoded, strict=True):
                vector = np.asarray(vector, dtype="<f4")
                vectors[row["memory_id"]] = vector
                # A concurrent forget/correction must not recreate a stale derived index.
                self.store.execute(
                    "INSERT OR REPLACE INTO memory_vectors "
                    "SELECT memory_id,?,?,? FROM memories WHERE memory_id=? AND content=? "
                    "AND status IN ('HYPOTHESIS','CONFIRMED') AND confidence>0",
                    (INDEX_VERSION, key, vector.tobytes(), row["memory_id"], row["content"]),
                )
        question = next(model.query_embed(query))
        # ponytail: exact scan suits a single owner's memories; use an ANN index at larger scale.
        return {key: float(np.dot(value, question)) for key, value in vectors.items()}


def fuse(lexical, semantic):
    """Reciprocal rank fusion, independent of the two scorers' numeric scales."""
    scores = {}
    for ranking in (lexical, semantic):
        for rank, key in enumerate(ranking, 1):
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
    return scores
