"""Read-only host/ADB preflight. Does not install, start an emulator, or capture UI/media."""

import argparse
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path


def inspect_host(adb="adb", runner=subprocess.run):
    resolved = shutil.which(adb)
    result = {
        "os": platform.system(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "kvm_device": Path("/dev/kvm").exists(),
        "binder_device": any(Path(p).exists() for p in ("/dev/binder", "/dev/binderfs/binder")),
        "binder_module": Path("/sys/module/binder_linux").exists(),
        "adb_available": bool(resolved),
        "authorized_devices": 0,
        "unauthorized_devices": 0,
        "android_ready": False,
    }
    if platform.system() == "Linux":
        memory = Path("/proc/meminfo")
        if memory.exists():
            for line in memory.read_text().splitlines():
                if line.startswith(("MemTotal:", "MemAvailable:")):
                    name, size, _ = line.split()
                    result[name.rstrip(":").lower() + "_kib"] = int(size)
    if resolved:
        try:
            run = runner(
                [resolved, "devices", "-l"], capture_output=True, text=True, timeout=10, check=False
            )
            if run.returncode == 0:
                for line in run.stdout.splitlines()[1:]:
                    cells = line.split()
                    if len(cells) >= 2:
                        if cells[1] == "device":
                            result["authorized_devices"] += 1
                        elif cells[1] == "unauthorized":
                            result["unauthorized_devices"] += 1
            else:
                result["adb_error"] = "ADB_DEVICE_LIST_FAILED"
        except (OSError, subprocess.TimeoutExpired):
            result["adb_error"] = "ADB_UNAVAILABLE"
    result["android_ready"] = result["authorized_devices"] > 0
    result["next_requirement"] = (
        "explicit Android session consent before observation"
        if result["android_ready"]
        else "an authorized physical Android device or a separately provisioned emulator"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    print(json.dumps(inspect_host(args.adb), ensure_ascii=False, indent=2))
