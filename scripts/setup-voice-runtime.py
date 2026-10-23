"""Prepare pinned GPT-SoVITS and Whisper assets inside an existing local runtime."""

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import urllib.request
import zipfile
from pathlib import Path

import imageio_ffmpeg
import yaml
from huggingface_hub import hf_hub_download


def file_sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def checked_path(root, relative):
    result = (root / relative.replace("\\", "/")).resolve()
    if not result.is_relative_to(root):
        raise ValueError("Asset path escapes runtime directory")
    return result


def fetch(url, destination, sha, limit):
    if destination.is_file() and file_sha(destination) == sha:
        return
    if not url.startswith("https://"):
        raise ValueError("Setup downloads require HTTPS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    request = urllib.request.Request(url, headers={"User-Agent": "Companion-Core-voice-setup"})
    size = 0
    with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as handle:
        while block := response.read(1024 * 1024):
            size += len(block)
            if size > limit:
                raise ValueError("Setup download exceeds asset size bound")
            handle.write(block)
    if file_sha(partial) != sha:
        raise ValueError("Downloaded asset digest does not match fixed source")
    partial.replace(destination)


def extract(archive, destination, *, preserve_source=False):
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            target = checked_path(destination, item.filename)
            if item.is_dir():
                continue
            data = source.read(item)
            if target.exists():
                if target.read_bytes() == data:
                    continue
                if preserve_source:
                    raise ValueError("Existing upstream source differs from the fixed archive")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)


def pinned_hf(repo, revision, name, destination, expected):
    if destination.is_file() and file_sha(destination) == expected:
        return
    # The temporary HF cache also lives under HF_HOME configured by the launcher.
    downloaded = Path(hf_hub_download(repo, name, revision=revision))
    if file_sha(downloaded) != expected:
        raise ValueError("Model digest does not match the recorded upstream LFS content")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(downloaded, destination)


def write_new(path, value):
    if not path.exists():
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare(root, specification):
    root = root.resolve(strict=True)
    spec = json.loads(specification.read_text(encoding="utf-8-sig"))
    if spec["schema_version"] != 1:
        raise ValueError("Unsupported voice setup specification")
    revision = spec["gpt_release_commit"]
    upstream = root / "upstream" / ("GPT-SoVITS-" + revision)
    archive = root / "tmp/gpt-sovits.zip"
    fetch(spec["source_url"], archive, spec["source_zip_sha256"], 100_000_000)
    extract(archive, root / "upstream", preserve_source=True)
    write_new(
        root / "upstream-pins.json",
        {
            "gpt_release": spec["gpt_release"],
            "gpt_release_commit": revision,
            "gpt_variant": spec["gpt_variant"],
        },
    )
    for group in spec["models"]:
        for entry in group["files"]:
            destination = checked_path(root, entry["path"])
            relative = destination.relative_to(root).as_posix()
            name = (
                relative.split("/pretrained_models/", 1)[1]
                if "/pretrained_models/" in relative
                else destination.name
            )
            pinned_hf(group["repository"], group["revision"], name, destination, entry["sha256"])
    auxiliary = spec["auxiliary"]
    archive = checked_path(root, auxiliary["archive"])
    pinned_hf(
        auxiliary["repository"],
        auxiliary["revision"],
        "G2PWModel.zip",
        archive,
        auxiliary["sha256"],
    )
    extract(archive, upstream / "GPT_SoVITS/text")
    for entry in spec["nltk"]:
        relative = entry["url"].split("/packages/", 1)[1]
        archive = checked_path(root / "cache/nltk", relative)
        fetch(entry["url"], archive, entry["sha256"], 12_000_000)
        extract(archive, archive.parent)
    reference = root / "samples/reference-ljspeech.wav"
    baseline = spec["baseline"]
    fetch(baseline["url"], reference, baseline["sha256"], 2_000_000)
    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
    shutil.copyfile(ffmpeg, root / "bin/ffmpeg.exe")
    models = upstream / "GPT_SoVITS/pretrained_models"
    config = root / "tts-infer.yaml"
    if not config.exists():
        config.write_text(
            yaml.safe_dump(
                {
                    "custom": {
                        "device": "cuda",
                        "is_half": True,
                        "version": spec["gpt_variant"],
                        "bert_base_path": str(models / "chinese-roberta-wwm-ext-large"),
                        "cnhuhbert_base_path": str(models / "chinese-hubert-base"),
                        "t2s_weights_path": str(models / "s1v3.ckpt"),
                        "vits_weights_path": str(models / "v2Pro/s2Gv2ProPlus.pth"),
                    }
                }
            ),
            encoding="utf-8",
        )
    write_new(
        root / "profile-baseline.json",
        {
            "tts": {
                "base_url": "http://127.0.0.1:19880",
                "reference_audio_path": str(reference),
                "prompt_text": "The examination and testimony of the experts enabled "
                "the Commission to conclude that five shots may have been fired,",
                "prompt_lang": "en",
                "text_lang": "all_zh",
                "timeout_seconds": 60,
                "max_concurrency": 1,
                "max_text_chars": 200,
                "parallel_infer": False,
            },
            "asr": {
                "model_dir": str(root / "models/asr-small"),
                "device": "cuda",
                "compute_type": "int8_float16",
                "language": None,
                "beam_size": 1,
                "vad_filter": True,
                "timeout_seconds": 60,
                "max_concurrency": 1,
            },
        },
    )
    sample = root / "samples/synthesis-input.txt"
    if not sample.exists():
        sample.write_text(
            "你好，这是本机语音节点的测试。文字可以转换成声音，语音也可以识别成文字。",
            encoding="utf-8",
        )
    receipt = {
        "source_commit": revision,
        "source_archive_sha256": spec["source_zip_sha256"],
        "verified_model_files": sum(len(group["files"]) for group in spec["models"]),
        "ffmpeg_sha256": file_sha(ffmpeg),
        "reference_sha256": file_sha(reference),
        "packages": {
            package: importlib.metadata.version(package)
            for package in (
                "torch",
                "torchaudio",
                "faster-whisper",
                "ctranslate2",
                "av",
                "transformers",
            )
        },
    }
    (root / "setup-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(json.dumps(receipt))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--specification", type=Path, required=True)
    args = parser.parse_args()
    prepare(args.runtime_root, args.specification)
