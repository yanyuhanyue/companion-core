"""Shared acquisition facts keep one identity without importing an application."""

import subprocess
import sys
from pathlib import Path


def test_source_facts_import_with_only_the_standard_library():
    root = Path(__file__).resolve().parents[1]
    code = """
import sys
sys.path.insert(0, sys.argv[1])
import companion_meme_sources as sources
assert sources.Metadata.__module__ == 'companion_meme_sources'
blocked = ('companion', 'companion_onebot_bridge', 'companion_meme_protocol',
           'httpx', 'pydantic', 'PIL', 'sqlite3', 'sentence_transformers')
assert not any(name == prefix or name.startswith(prefix + '.')
               for name in sys.modules for prefix in blocked)
"""
    subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(root)],
                   check=True, timeout=20)


def test_bridge_consumers_share_the_extracted_classes_and_fetch_helpers():
    import companion_meme_sources as sources
    from companion_meme_sources import fetch, review
    from companion_onebot_bridge import images, meme_catalog, meme_discovery, meme_runtime

    assert meme_catalog.Metadata is sources.Metadata
    assert meme_catalog.INTENTS is sources.INTENTS
    assert meme_catalog.CATEGORY is sources.CATEGORY
    assert meme_discovery.ReviewApproval is sources.ReviewApproval
    assert meme_discovery.RepositoryImage is sources.RepositoryImage
    assert meme_discovery.PublicFetcher is fetch.PublicFetcher
    assert meme_runtime.TrustedReviewer is review.TrustedReviewer
    assert images.global_address is fetch.global_address
    assert images.resolve_public is fetch.resolve_public
