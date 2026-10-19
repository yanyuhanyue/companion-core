import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "astrbot_plugin_companion_bridge"
output = root / "dist/astrbot_plugin_companion_bridge-v0.5.0.zip"
output.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(source.iterdir()):
        if path.is_file() and path.suffix in {".py", ".json", ".yaml", ".txt"}:
            archive.write(path, path.name)
print(output)
