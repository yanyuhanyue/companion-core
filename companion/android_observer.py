"""Read-only Android UI acquisition, separate from Core and disabled without a session."""

import base64
import re
import subprocess
import uuid
from urllib.parse import urlsplit

import httpx


class AndroidObserver:
    def __init__(self, core_url, session_id, session_token, serial, adb="adb", transport=None):
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,124}", serial):
            raise ValueError("invalid Android serial")
        if not re.fullmatch(r"media_[a-f0-9]{32}", session_id):
            raise ValueError("invalid media session ID")
        url = urlsplit(core_url)
        if (
            not url.hostname
            or url.username
            or url.query
            or url.fragment
            or (
                url.scheme != "https"
                and not (url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost", "::1"})
            )
        ):
            raise ValueError("Core requires HTTPS or loopback HTTP")
        self.core_url, self.session_id, self.token = core_url.rstrip("/"), session_id, session_token
        self.serial, self.adb, self.transport = serial, adb, transport

    def _require_device(self, session):
        if "android" not in session["modalities"]:
            raise ValueError("Android observation is not granted")
        if session.get("device_id") != "adb:" + self.serial:
            raise ValueError("ANDROID_DEVICE_MISMATCH")

    def capture(self, runner=subprocess.run):
        """One complete UI hierarchy. Never tap, type, launch apps, or accept a shell command."""
        root = self.core_url + "/internal/v2/media/sessions/" + self.session_id
        with httpx.Client(
            transport=self.transport,
            timeout=20,
            follow_redirects=False,
            trust_env=False,
            headers={"Authorization": "Bearer " + self.token},
        ) as client:
            context = client.get(root)
            context.raise_for_status()
            session = context.json()
            self._require_device(session)
            if session["max_bytes"] - session["bytes_reserved"] < 1024:
                raise ValueError("Android traffic budget is exhausted")
            snapshot = runner(
                [self.adb, "-s", self.serial, "exec-out", "uiautomator", "dump", "/dev/tty"],
                capture_output=True,
                timeout=15,
                check=False,
            )
            if snapshot.returncode != 0:
                raise RuntimeError("ANDROID_CAPTURE_FAILED")
            if not snapshot.stdout or len(snapshot.stdout) > session["max_chunk_bytes"]:
                raise RuntimeError("ANDROID_CAPTURE_SIZE_INVALID")
            # Recheck immediately after acquisition, before transmitting any device content.
            fresh = client.get(root)
            fresh.raise_for_status()
            self._require_device(fresh.json())
            result = client.post(
                root + "/chunks",
                json={
                    "request_key": str(uuid.uuid4()),
                    "operation": "android",
                    "mime_type": "application/xml",
                    "data_base64": base64.b64encode(snapshot.stdout).decode(),
                },
            )
            result.raise_for_status()
            return result.json()
