"""Shared caption contracts stay importable in both deployment artifacts."""

import io
import re
import shutil
import tomllib
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

import companion_meme_protocol as shared
from companion_onebot_bridge import meme_caption, meme_library, meme_semantic
from scripts.verify_package import verify_meme_protocol

ROOT = Path(__file__).resolve().parents[1]


def test_extraction_preserves_contract_hashes_and_compatibility_exports():
    assert shared.PROMPT_SHA256 == (
        "a4affd201d689bc1275738db0d33f75bbb5176942fd715501abdf30e8bd6f5d3"
    )
    assert shared.SCHEMA_SHA256 == (
        "af1d5d130b0015203e92a5315223fcfaeef47c0191f595c75f430e8eddb406a9"
    )
    for name in ("CaptionAsset", "CaptionContext", "CaptionIdentity", "CaptionOutcome",
                 "CaptionRequest", "CaptionResult", "EmbeddingBatch", "EmbeddingSpec",
                 "UsageReceipt", "canonical", "digest", "parse", "sample_frames",
                 "source_evidence", "strict_json"):
        assert getattr(meme_caption, name) is getattr(shared, name)
    assert meme_semantic.caption_text is shared.caption_text
    assert meme_library.PreparedImage is shared.PreparedImage
    caption = SimpleNamespace(description="小猫", visible_text="抱抱", tags=("安慰",),
                              use_when=("需要陪伴",), avoid_when=("不要索引这一句",))
    assert shared.caption_text(caption) == "小猫 抱抱 安慰 需要陪伴"


def test_shared_protocol_imports_without_either_application_or_storage(tmp_path):
    shutil.copytree(ROOT / "companion_meme_protocol", tmp_path / "companion_meme_protocol")
    shutil.copytree(ROOT / "companion_meme_sources", tmp_path / "companion_meme_sources")
    bridge = tmp_path / "companion_onebot_bridge"
    bridge.mkdir()
    for name in ("__init__.py", "meme_caption.py", "meme_library.py", "meme_semantic.py"):
        shutil.copyfile(ROOT / "companion_onebot_bridge" / name, bridge / name)
    verify_meme_protocol(tmp_path)


def test_shared_media_validation_and_bridge_limit_forwarding(tmp_path, monkeypatch):
    stream = io.BytesIO()
    Image.new("RGB", (7, 3), "blue").save(stream, "PNG")
    data = stream.getvalue()
    prepared = shared.validate_bytes(data)
    assert prepared is not None
    assert (prepared.data, prepared.width, prepared.height, prepared.mime_type) == (
        data, 7, 3, "image/png",
    )
    assert shared.validate_bytes(data, max_bytes=len(data) - 1) is None
    assert shared.validate_bytes(data, max_dimension=6) is None
    assert shared.validate_bytes(data, max_frames=0) is None
    assert shared.validate_bytes(data, max_total_pixels=20) is None
    limits = dict(max_bytes=len(data), max_dimension=7, max_frames=2, max_total_pixels=21)
    calls = []

    def validate(payload, **received):
        calls.append((payload, received))
        return shared.validate_bytes(payload, **received)

    monkeypatch.setattr(meme_library, "validate_bytes", validate)
    library = meme_library.MemeLibrary(tmp_path, **limits)
    assert library.validate_bytes(data) == prepared
    assert calls == [(data, limits)]


def test_bridge_pydantic_dependencies_match_core_pins_and_hashes():
    packages = {row["name"]: row for row in tomllib.loads(
        (ROOT / "uv.lock").read_text("utf-8"),
    )["package"]}
    lock = (ROOT / "deploy/onebot-bridge.lock").read_text("utf-8")
    sections = {part.split("==", 1)[0]: part
                for part in re.split(r"(?m)(?=^[a-z][a-z0-9-]*==)", lock)[1:]}
    for name in ("pydantic", "pydantic-core", "annotated-types", "typing-extensions",
                 "typing-inspection"):
        package, section = packages[name], sections[name]
        assert section.splitlines()[0].split()[0] == name + "==" + package["version"]
        hashes = {artifact["hash"].removeprefix("sha256:")
                  for artifact in [package["sdist"], *package["wheels"]]}
        assert set(re.findall(r"--hash=sha256:([a-f0-9]{64})", section)) == hashes


def test_wheel_and_both_images_include_shared_contract():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    assert "companion_meme_protocol" in project["tool"]["hatch"]["build"]["targets"][
        "wheel"
    ]["packages"]
    for path in ("Dockerfile", "deploy/onebot-bridge.Dockerfile"):
        assert "COPY companion_meme_protocol ./companion_meme_protocol" in (
            ROOT / path
        ).read_text("utf-8")


def test_shared_protocol_changes_invalidate_both_runtime_attestations(monkeypatch):
    from companion.live_text import code_sha256
    from companion_onebot_bridge.window import bridge_code_sha256

    original = Path.read_bytes
    before = code_sha256(), bridge_code_sha256()

    def changed(path):
        value = original(path)
        return value + b"\n# synthetic protocol edit\n" if (
            path.parent.name == "companion_meme_protocol") else value

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert code_sha256() != before[0]
    assert bridge_code_sha256() != before[1]
