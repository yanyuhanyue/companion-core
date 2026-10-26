"""Exact local repository/license policy for the shared acquisition evidence."""

import hashlib
import re
from urllib.parse import unquote, urlsplit

from companion_meme_sources import CATEGORY, INTENTS, MOCKERY, Metadata, ReviewApproval
from companion_meme_sources.community import relative_path

SHA = re.compile(r"[a-f0-9]{64}")
CATEGORIES = {
    "celebrate": "happy",
    "comfort": "like",
    "like": "like",
    "surprised": "surprised",
    "morning": "morning",
    "sleep": "sleep",
    "playful": "happy",
}


class TrustedReviewer:
    """Local approval of exact repository revisions and actual license file bytes.

    Rules are trusted configuration, never fields copied out of an index or a
    search response. The pack parser independently binds completed captions and
    tags to the downloaded image's original hash and semantic entry identity.
    """

    def __init__(self, rules, *, require_semantics=True):
        self.rules = tuple(dict(rule) for rule in rules)
        self.require_semantics = require_semantics
        self.rejected = 0
        if len(self.rules) > 32:
            raise ValueError("MEME_TRUST_RULE_INVALID")
        self.license_files = {}
        licenses = {}
        for rule in self.rules:
            if (
                set(rule)
                != {
                    "repo",
                    "revision",
                    "license",
                    "license_path",
                    "license_sha256",
                    "asset_prefix",
                    "usage_basis",
                }
                or not isinstance(rule["repo"], str)
                or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", rule["repo"])
                or any(part in {".", ".."} for part in rule["repo"].split("/"))
                or not isinstance(rule["revision"], str)
                or not re.fullmatch(r"[a-f0-9]{40}", rule["revision"])
                or not isinstance(rule["license_sha256"], str)
                or not SHA.fullmatch(rule["license_sha256"])
                or not isinstance(rule["license"], str)
                or not 1 <= len(rule["license"].strip()) <= 128
                or rule["license"].casefold() in {"unknown", "unlicensed", "none"}
                or not isinstance(rule["usage_basis"], str)
                or not 1 <= len(rule["usage_basis"].strip()) <= 256
            ):
                raise ValueError("MEME_TRUST_RULE_INVALID")
            relative_path(rule["license_path"])
            relative_path(rule["asset_prefix"])
            if (
                len(
                    rule["usage_basis"]
                    + "; license-file="
                    + relative_path(rule["license_path"]).as_posix()
                    + "; license-sha256="
                    + rule["license_sha256"]
                )
                > 512
            ):
                raise ValueError("MEME_TRUST_RULE_INVALID")
            identity = rule["repo"], rule["revision"]
            license_identity = rule["license_path"], rule["license_sha256"], rule["license"]
            if licenses.get(identity, license_identity) != license_identity:
                raise ValueError("MEME_TRUST_RULE_INVALID")
            licenses[identity] = license_identity
            if self.require_semantics:
                self.license_files[identity] = rule["license_path"]
            else:
                policy = self.license_files.setdefault(identity, {**rule, "asset_prefixes": ()})
                prefix = relative_path(rule["asset_prefix"]).as_posix()
                if prefix not in policy["asset_prefixes"]:
                    policy["asset_prefixes"] += (prefix,)

    @staticmethod
    def _exact_fetch(fetch, url):
        return (fetch.requested_url == fetch.final_url == url and len(fetch.hops) == 1
                and fetch.hops[0].url == url and fetch.hops[0].status == 200)

    def _file_source(self, rule, evidence, path):
        from urllib.parse import quote

        files = evidence.source_files
        parts = path.split("/")
        if (not isinstance(files, dict) or set(files) != {"manifest", "license", "listing"}
            or len(parts) < 3 or parts[-3] != "memes" or evidence.archive_member):
            return False
        prefix = f"https://raw.githubusercontent.com/{rule['repo']}/{rule['revision']}/"
        root = "/".join(parts[:-3])
        manifest_path = (root + "/" if root else "") + "manifest.json"
        expected = {
            "manifest": prefix + quote(manifest_path),
            "license": prefix + quote(relative_path(rule["license_path"]).as_posix()),
            "listing": f"https://api.github.com/repos/{rule['repo']}/contents/"
                       + quote("/".join(parts[:-1])) + "?ref=" + rule["revision"],
        }
        return (
            evidence.discovered_asset_url == prefix + quote(path)
            and self._exact_fetch(evidence.fetch, evidence.discovered_asset_url)
            and evidence.fetch.body_sha256 == evidence.image_sha256
            and all(self._exact_fetch(files[role], url) for role, url in expected.items())
            and files["license"].body_sha256 == rule["license_sha256"]
        )

    async def __call__(self, candidate, image, evidence):
        for rule in self.rules:
            prefix = f"https://raw.githubusercontent.com/{rule['repo']}/{rule['revision']}/"
            archive_url = f"https://codeload.github.com/{rule['repo']}/zip/{rule['revision']}"
            asset = urlsplit(evidence.discovered_asset_url)
            if (
                candidate.source_page
                != f"https://github.com/{rule['repo']}/tree/{rule['revision']}"
                or not evidence.discovered_asset_url.startswith(prefix)
                or asset.query
                or asset.fragment
                or candidate.asset_url != evidence.fetch.final_url
                or candidate.revision != rule["revision"]
                or candidate.license != rule["license"]
                or candidate.license_sha256 != rule["license_sha256"]
                or evidence.image_sha256 != image.sha256
                or hashlib.sha256(image.data).hexdigest() != image.sha256
            ):
                continue
            if self.require_semantics and (
                candidate.semantic_verified is not True
                or not isinstance(candidate.description, str)
                or not candidate.description.strip()
                or not isinstance(candidate.tags, tuple)
                or not candidate.tags
            ):
                continue
            try:
                path = relative_path(
                    unquote(evidence.discovered_asset_url[len(prefix) :])
                ).as_posix()
            except ValueError:
                continue
            member = rule["repo"].split("/")[-1] + "-" + rule["revision"] + "/" + path
            if not path.startswith(relative_path(rule["asset_prefix"]).as_posix() + "/"):
                continue
            if evidence.source_files is not None:
                if not self._file_source(rule, evidence, path):
                    continue
            elif evidence.archive_member != member or not self._exact_fetch(
                evidence.fetch, archive_url
            ):
                continue
            description = candidate.description if self.require_semantics else "待理解素材"
            tags = candidate.tags if self.require_semantics else ()
            words = (description + " " + " ".join(tags)).casefold()
            suitable = tuple(
                intent for intent, terms in INTENTS.items() if any(term in words for term in terms)
            )
            if self.require_semantics and not suitable:
                continue
            avoid = ("comfort",) if any(word in words for word in MOCKERY) else ()
            category = path.rsplit("/", 2)[-2]
            if not CATEGORY.fullmatch(category):
                category = CATEGORIES[suitable[0]] if suitable else "happy"
            metadata = Metadata(
                category,
                description,
                tags,
                suitable,
                avoid,
                candidate.source_page,
                candidate.asset_url,
                rule["license"],
                rule["usage_basis"]
                + "; license-file="
                + relative_path(rule["license_path"]).as_posix()
                + "; license-sha256="
                + rule["license_sha256"],
                evidence.fetch.fetched_at,
                candidate.revision,
            )
            return ReviewApproval(metadata, evidence.sha256)
        self.rejected += 1
        return None

