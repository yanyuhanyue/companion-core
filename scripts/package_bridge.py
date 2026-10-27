"""Bundle canonical transport sources under the plugin namespace, without Core."""

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "companion_onebot_bridge": "_transport",
    "companion_delivery_protocol": "_delivery_protocol",
    "companion_meme_protocol": "_meme_protocol",
    "companion_meme_sources": "_meme_sources",
}


def bundled_imports(source, *, plugin=False):
    # Only import paths change; dispatch and receipt implementations stay canonical.
    level = "." if plugin else ".."
    for package, local in PACKAGES.items():
        source = source.replace("from " + package, "from " + level + local)
        source = source.replace("import " + package + " as ",
                                "from " + level + " import " + local + " as ")
        source = source.replace("import " + package + "\n",
            "from " + level + " import " + local + " as " + package + "\n")
    return source


def package_bridge(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "astrbot_plugin_companion_bridge").iterdir()):
            if path.is_file() and path.suffix in {".py", ".json", ".yaml", ".txt"}:
                source = path.read_text("utf-8")
                if path.suffix == ".py":
                    source = bundled_imports(source, plugin=True)
                archive.writestr(path.name, source)
        for package, local in PACKAGES.items():
            for path in sorted((ROOT / package).glob("*.py")):
                archive.writestr(local + "/" + path.name,
                                 bundled_imports(path.read_text("utf-8")))
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path,
                        default=ROOT / "dist/astrbot_plugin_companion_bridge-v0.6.0.zip")
    print(package_bridge(parser.parse_args().output))
