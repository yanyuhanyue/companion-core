"""Pinned community-index discovery and bounded in-memory Meme Manager pack import."""

import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import PurePosixPath
from urllib.parse import quote

from .meme_catalog import INTENTS
from .meme_discovery import ArchiveMember, SearchImage
from .meme_library import CATEGORY

INDEX_REVISION = "2f173ca5dc0b460d9c6408a4f7f0e9a83371677f"
INDEX_URL = ("https://raw.githubusercontent.com/anka-afk/astrbot-meme-pack-index/"
             + INDEX_REVISION + "/community-index.json")


def relative_path(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 512 or "\\" in value
        or ":" in value or ".." in value or value.startswith("/")
        or any(ord(c) < 32 for c in value)):
        raise ValueError("MEME_PACK_PATH_INVALID")
    parts = value.rstrip("/").split("/")
    if any(not p or p == "." or p.endswith((" ", ".")) for p in parts):
        raise ValueError("MEME_PACK_PATH_INVALID")
    return PurePosixPath(*parts)


def bounded_archive(data):
    if not 0 < len(data) <= 8 * 1024 * 1024:
        raise ValueError("MEME_PACK_COMPRESSED_LIMIT")
    archive = zipfile.ZipFile(io.BytesIO(data))
    try:
        entries = archive.infolist()
        if not 1 <= len(entries) <= 1024:
            raise ValueError("MEME_PACK_ENTRY_LIMIT")
        seen, expanded = set(), 0
        for entry in entries:
            # ZipInfo sanitizes backslashes/NUL on Windows even while reading. Validate
            # the original central-directory name, before its normalized projection.
            path = relative_path(entry.orig_filename).as_posix().casefold()
            if path in seen:
                raise ValueError("MEME_PACK_DUPLICATE_PATH")
            seen.add(path)
            mode = entry.external_attr >> 16
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or entry.flag_bits & 1:
                raise ValueError("MEME_PACK_SPECIAL_FILE")
            if entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise ValueError("MEME_PACK_COMPRESSION_INVALID")
            expanded += entry.file_size
            if (expanded > 16 * 1024 * 1024 or entry.file_size > 4 * 1024 * 1024
                or entry.file_size > max(1024, entry.compress_size * 100)):
                raise ValueError("MEME_PACK_EXPANSION_LIMIT")
        return archive
    except BaseException:
        archive.close()
        raise


def archive_images(data, pack, revision, intent, *, license_path=None):
    """Never extract files. Validate every path before reading any selected image."""
    with bounded_archive(data) as archive:
        paths = {relative_path(i.filename).as_posix(): i for i in archive.infolist()
                 if not i.is_dir()}
        roots = {p.split("/")[0] for p in paths}
        if len(roots) != 1:
            raise ValueError("MEME_PACK_WRAPPER_INVALID")
        subpath = pack["source"]["subpath"]
        wrapper = next(iter(roots)) + "/"
        prefix = wrapper + ("" if subpath == "." else subpath + "/")
        license_sha256 = ""
        if license_path is not None:
            entry = paths.get(wrapper + relative_path(license_path).as_posix())
            if entry is not None and 0 < entry.file_size <= 64 * 1024:
                with archive.open(entry) as stream:
                    license_bytes = stream.read(64 * 1024 + 1)
                if len(license_bytes) <= 64 * 1024:
                    license_sha256 = hashlib.sha256(license_bytes).hexdigest()

        def read(name, maximum=512 * 1024):
            entry = paths.get(prefix + name)
            if entry is None or entry.file_size > maximum:
                raise ValueError("MEME_PACK_FILE_INVALID")
            with archive.open(entry) as stream:
                body = stream.read(maximum + 1)
            if len(body) > maximum:
                raise ValueError("MEME_PACK_EXPANSION_LIMIT")
            return body

        manifest = json.loads(read("manifest.json"))
        categories = manifest.get("categories")
        if (manifest.get("schema_version") != 1 or manifest.get("id") != pack["id"]
            or not isinstance(manifest.get("version"), str)
            or not 1 <= len(manifest["version"]) <= 64
            or not isinstance(categories, dict) or not 1 <= len(categories) <= 128):
            raise ValueError("MEME_PACK_MANIFEST_INVALID")
        for category, value in categories.items():
            if (not isinstance(category, str) or not CATEGORY.fullmatch(category)
                or not isinstance(value, dict) or not isinstance(value.get("description"), str)
                or not 1 <= len(value["description"]) <= 2000):
                raise ValueError("MEME_PACK_CATEGORY_INVALID")
        semantics = {}
        semantic_declared = manifest.get("extensions", {}).get("semantic")
        if semantic_declared is not None and semantic_declared != {
            "version": 1, "file": "semantic_metadata.json"
        }:
            raise ValueError("MEME_PACK_SEMANTIC_INVALID")
        if semantic_declared or prefix + "semantic_metadata.json" in paths:
            semantic = json.loads(read("semantic_metadata.json"))
            records = semantic.get("images")
            if (semantic.get("schema_version") != "2.0" or semantic.get("pack_id") != pack["id"]
                or not isinstance(records, dict) or len(records) > 1024):
                raise ValueError("MEME_PACK_SEMANTIC_INVALID")
            for entry_id, row in records.items():
                path = relative_path(row["relative_path"]).as_posix()
                pieces = path.split("/")
                digest = row["content_sha256"]
                calculated = hashlib.sha256(
                    (digest + "\0" + row["category"] + "\0" + path).encode()
                ).hexdigest()
                if (len(pieces) != 3 or pieces[0] != "memes" or pieces[1] not in categories
                    or row["category"] != pieces[1]
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    or row["entry_id"] != entry_id
                    or calculated != entry_id or path in semantics):
                    raise ValueError("MEME_PACK_SEMANTIC_ID_INVALID")
                if hashlib.sha256(read(path, 4 * 1024 * 1024)).hexdigest() != digest:
                    raise ValueError("MEME_PACK_HASH_MISMATCH")
                if (row.get("caption_status") not in {"pending", "running", "done", "failed"}
                    or not isinstance(row.get("caption"), str) or len(row["caption"]) > 2000
                    or (row["caption_status"] == "done" and not row["caption"].strip())):
                    raise ValueError("MEME_PACK_CAPTION_INVALID")
                tags = row.get("tags", [])
                if (not isinstance(tags, list) or len(tags) > 32
                    or any(not isinstance(t, str) or not 1 <= len(t) <= 64 for t in tags)):
                    raise ValueError("MEME_PACK_TAGS_INVALID")
                semantics[path] = row
        repo = pack["source"]["repo"]
        page = f"https://github.com/{repo}/tree/{revision}"
        for full_path in sorted(paths):
            if not full_path.startswith(prefix + "memes/"):
                continue
            path = full_path[len(prefix):]
            pieces = path.split("/")
            if (len(pieces) != 3 or pieces[1] not in categories
                or PurePosixPath(path).suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif"}):
                continue
            row = semantics.get(path, {})
            description = row.get("caption") or categories[pieces[1]]["description"]
            tags = tuple(row.get("tags", []))
            text = (description + " " + pieces[1] + " " + " ".join(tags)).casefold()
            if not any(term in text for term in INTENTS[intent]):
                continue
            asset_path = ("" if subpath == "." else subpath + "/") + path
            url = f"https://raw.githubusercontent.com/{repo}/{revision}/{quote(asset_path)}"
            yield (SearchImage(
                       page, url, description, tags, pack["license"], revision,
                       semantic_verified=row.get("caption_status") == "done" and bool(tags),
                       license_sha256=license_sha256,
                   ),
                   read(path, 4 * 1024 * 1024), full_path)


class CommunityPacks:
    def __init__(self, license_files=None):
        self.license_files = dict(license_files or {})

    async def discover(self, intent, fetcher):
        await fetcher.begin_query()
        resource = await fetcher.get(INDEX_URL, 512 * 1024, redirects=False)
        index = json.loads(resource.data)
        if index.get("schema_version") not in (1, "1.0.0") or not isinstance(
            index.get("packs"), list
        ) or len(index["packs"]) > 256:
            raise ValueError("MEME_INDEX_INVALID")
        ranked = []
        for pack in index["packs"]:
            source = pack.get("source", {})
            if (source.get("type") != "github" or not isinstance(source.get("repo"), str)
                or not re.fullmatch(r"[\w.-]+/[\w.-]+", source["repo"], re.ASCII)
                or any(p in {".", ".."} for p in source["repo"].split("/"))
                or not isinstance(source.get("ref"), str) or not 1 <= len(source["ref"]) <= 200
                or not isinstance(pack.get("license"), str) or not 1 <= len(pack["license"]) <= 256
                or not isinstance(pack.get("id"), str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", pack["id"])):
                continue
            if source.get("subpath") != ".":
                relative_path(source.get("subpath"))
            text = json.dumps([pack.get("description", ""), pack.get("tags", [])],
                              ensure_ascii=False).casefold()
            score = sum(term in text for term in INTENTS[intent])
            score += int("default" in text or "starter" in text)
            if score:
                ranked.append((score, pack))
        ranked.sort(key=lambda row: (-row[0], row[1]["id"]))
        for _, pack in ranked[:2]:
            source = pack["source"]
            revision = source["ref"]
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                endpoint = f"https://api.github.com/repos/{source['repo']}/commits/"
                resource = await fetcher.get(
                    endpoint + quote(revision, safe=""),
                    256 * 1024, redirects=False,
                )
                revision = json.loads(resource.data).get("sha", "")
                if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
                    raise ValueError("MEME_PACK_REVISION_INVALID")
            url = f"https://codeload.github.com/{source['repo']}/zip/{revision}"
            resource = await fetcher.get(url, 8 * 1024 * 1024, redirects=False)
            for candidate, _, path in archive_images(
                resource.data, pack, revision, intent,
                license_path=self.license_files.get((source["repo"], revision)),
            ):
                await fetcher.check()
                yield candidate, ArchiveMember(resource, path)
