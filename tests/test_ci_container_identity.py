"""One authenticated OCI image, with classic and containerd daemon identities."""

import gzip
import hashlib
import io
import json
import tarfile
from copy import deepcopy

import pytest

from scripts import ci_container_artifacts as images


def oci_archive(name="core", *, nested=False, architecture="amd64", fault=None, compressed=False):
    blobs = {}

    def blob(value, media_type):
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        blobs["blobs/sha256/" + digest[7:]] = data
        return {"mediaType": media_type, "digest": digest, "size": len(data)}

    layer_bytes = b"SYNTHETIC_LAYER_BYTES"
    diff_id = "sha256:" + hashlib.sha256(layer_bytes).hexdigest()
    layer = blob(gzip.compress(layer_bytes) if compressed else layer_bytes,
                 "application/vnd.oci.image.layer.v1.tar" + ("+gzip" if compressed else ""))
    if fault == "layer-diffid":
        diff_id = "sha256:" + "f" * 64
    config = blob({"os": "linux", "architecture": architecture,
        "config": {"Labels": {"synthetic.component": name}},
        "rootfs": {"type": "layers", "diff_ids": [diff_id]}},
        "application/vnd.oci.image.config.v1+json")
    manifest = blob({"schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": config, "layers": [layer]}, "application/vnd.oci.image.manifest.v1+json")
    root = manifest
    if nested:
        root = blob({"schemaVersion": 2,
            "mediaType": "wrong" if fault == "index-media-type" else
                "application/vnd.oci.image.index.v1+json", "manifests": [manifest]},
            "application/vnd.oci.image.index.v1+json")
    index = {"schemaVersion": 2, "manifests": [deepcopy(root)]}
    if fault == "descriptor-size":
        index["manifests"][0]["size"] += 1
    elif fault == "descriptor-digest":
        index["manifests"][0]["digest"] = "sha256:" + "f" * 64
    elif fault == "ambiguous":
        index["manifests"].append(deepcopy(root))
    elif fault == "extra-platform":
        index["manifests"][0]["platform"] = {"os": "linux", "architecture": "arm64"}
    elif fault == "blob-bytes":
        blobs["blobs/sha256/" + config["digest"][7:]] += b" "
    files = {"oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
             "index.json": json.dumps(index).encode(),
             "manifest.json": json.dumps([{
                 "Config": "blobs/sha256/" + config["digest"][7:], "Layers": [
                     "blobs/sha256/" + layer["digest"][7:]]}]).encode(), **blobs}
    if fault == "docker-config":
        alternate = json.loads(blobs["blobs/sha256/" + config["digest"][7:]])
        alternate["config"]["Labels"]["synthetic.component"] = "other-config-same-rootfs"
        other = blob(alternate, "application/vnd.oci.image.config.v1+json")
        files.update(blobs)
        files["manifest.json"] = json.dumps([{"Config": "blobs/sha256/" + other["digest"][7:],
            "Layers": ["blobs/sha256/" + layer["digest"][7:]]}]).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for path, data in files.items():
            if fault == "empty-duplicate" and path == "index.json":
                archive.addfile(tarfile.TarInfo(path), io.BytesIO())
            entry = tarfile.TarInfo(path)
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    return output.getvalue(), {"config_digest": config["digest"],
        "archive_descriptor": root, "platform_manifest_descriptor": manifest,
        "rootfs_diff_ids": [diff_id]}


def component_archive(tmp_path, **options):
    data, identity = oci_archive(**options)
    path = tmp_path / "core.tar"
    path.write_bytes(data)
    component = {"component": "core", "image_id": identity["config_digest"],
                 "config_digest": identity["config_digest"], "platform": "linux/amd64",
                 "rootfs_diff_ids": identity["rootfs_diff_ids"],
                 "archive": {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}
    return path, component, identity


def native_image(component, identity, *, containerd):
    return {"Id": identity["archive_descriptor"]["digest"] if containerd
            else identity["config_digest"], "Os": "linux", "Architecture": "amd64",
            "RootFS": {"Layers": component["rootfs_diff_ids"]},
            **({"Descriptor": deepcopy(identity["archive_descriptor"])} if containerd else {})}


@pytest.mark.parametrize("present", [False, True])
def test_containerd_uses_archive_manifest_id_and_preserves_existing_core(tmp_path, present):
    path, component, identity = component_archive(tmp_path)
    native = native_image(component, identity, containerd=True)
    calls, loaded = [], present

    def runner(command):
        nonlocal loaded
        calls.append(command)
        if command[:3] == ["docker", "image", "ls"]:
            return (native["Id"] + "\n").encode() if loaded else b""
        if command[:3] == ["docker", "image", "load"]:
            assert not loaded, "An existing authenticated image must not be loaded again"
            loaded = True
            return b"loaded"
        assert command == ["docker", "image", "inspect", native["Id"]]
        return json.dumps([native]).encode()

    result = images.load_archive(component, path, runner=runner, reuse_existing=True)
    assert result["consumer_local_image_id"] == native["Id"]
    assert result["config_digest"] == component["config_digest"]
    assert result["action"] == ("already_present" if present else "loaded")
    assert sum("load" in call for call in calls) == (0 if present else 1)


@pytest.mark.parametrize("containerd", [False, True])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("compressed", [False, True])
def test_producer_default_load_exercises_archive_codec_even_when_present(
    tmp_path, containerd, nested, compressed,
):
    path, component, identity = component_archive(tmp_path, nested=nested, compressed=compressed)
    native = native_image(component, identity, containerd=containerd)
    calls = []

    def runner(command):
        calls.append(command)
        if "load" in command:
            return b"loaded"
        if "ls" in command:
            return (native["Id"] + "\n").encode()
        assert command == ["docker", "image", "inspect", native["Id"]]
        return json.dumps([native]).encode()

    result = images.load_archive(component, path, runner=runner)
    assert result["action"] == "loaded"
    assert result["consumer_local_image_id"] == native["Id"]
    assert sum("load" in command for command in calls) == 1
    assert calls[0][:3] == ["docker", "image", "load"]


@pytest.mark.parametrize("fault", [
    "descriptor-size", "descriptor-digest", "blob-bytes", "ambiguous", "extra-platform",
    "layer-diffid", "docker-config", "empty-duplicate",
])
def test_invalid_archive_graph_never_contacts_daemon(tmp_path, fault):
    path, component, _ = component_archive(tmp_path, fault=fault)
    with pytest.raises(ValueError, match="PUBLIC_IMAGE_ARCHIVE_"):
        images.load_archive(component, path, runner=lambda command: pytest.fail(str(command)))


@pytest.mark.parametrize("fault", ["config-digest", "producer-id", "rootfs", "platform"])
def test_archive_must_bind_all_declared_identities(tmp_path, fault):
    path, component, _ = component_archive(tmp_path)
    if fault == "config-digest":
        component["config_digest"] = "sha256:" + "f" * 64
    elif fault == "producer-id":
        component["image_id"] = "sha256:" + "f" * 64
    elif fault == "rootfs":
        component["rootfs_diff_ids"] = ["sha256:" + "f" * 64]
    else:
        component["platform"] = "linux/arm64"
    with pytest.raises(ValueError, match="PUBLIC_IMAGE_ARCHIVE_"):
        images.load_archive(component, path, runner=lambda command: pytest.fail(str(command)))


@pytest.mark.parametrize("fault", ["descriptor", "missing-descriptor", "rootfs", "platform", "id"])
def test_present_image_with_unbound_identity_is_rejected_without_load(tmp_path, fault):
    path, component, identity = component_archive(tmp_path)
    native = native_image(component, identity, containerd=True)
    if fault == "descriptor":
        native["Descriptor"]["digest"] = "sha256:" + "f" * 64
    elif fault == "missing-descriptor":
        native.pop("Descriptor")
    elif fault == "rootfs":
        native["RootFS"]["Layers"] = ["sha256:" + "f" * 64]
    elif fault == "platform":
        native["Architecture"] = "arm64"
    else:
        native["Id"] = "sha256:" + "f" * 64

    def runner(command):
        assert "load" not in command
        if "ls" in command:
            return (identity["archive_descriptor"]["digest"] + "\n").encode()
        return json.dumps([native]).encode()

    with pytest.raises(ValueError, match="PUBLIC_IMAGE_LOADED_"):
        images.load_archive(component, path, runner=runner, reuse_existing=True)


def test_daemon_error_does_not_mean_image_missing(tmp_path):
    path, component, _ = component_archive(tmp_path)

    def runner(command):
        assert "load" not in command
        raise RuntimeError("SYNTHETIC_DAEMON_UNAVAILABLE")

    with pytest.raises(RuntimeError, match="DAEMON_UNAVAILABLE"):
        images.load_archive(component, path, runner=runner, reuse_existing=True)


def test_capture_keeps_containerd_native_id_before_export_derives_config(tmp_path, monkeypatch):
    _, component, identity = component_archive(tmp_path)
    native = native_image(component, identity, containerd=True)
    native["Size"] = 1000
    monkeypatch.setattr(images, "inspect_image", lambda *a, **k: native)
    (tmp_path / "Dockerfile").write_text("FROM synthetic\n")
    (tmp_path / "uv.lock").write_text("synthetic lock\n")
    captured = images.capture("core", root=tmp_path, identity={"commit": "a", "tree": "b"})
    assert captured["image_id"] == identity["archive_descriptor"]["digest"]
    assert "config_digest" not in captured
    assert captured["producer_descriptor"] == native["Descriptor"]


def test_nested_index_declared_media_type_must_match_raw_content(tmp_path):
    path, component, _ = component_archive(tmp_path, nested=True, fault="index-media-type")
    with pytest.raises(ValueError, match="PUBLIC_IMAGE_ARCHIVE_MANIFEST_INVALID"):
        images.load_archive(component, path, runner=lambda command: pytest.fail(str(command)))
