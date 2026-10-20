"""Submit one authorized read-only Android UI observation; tokens are environment-only."""

import argparse
import json
import os
from urllib.parse import urlsplit

from companion.android_observer import AndroidObserver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-url", default="http://127.0.0.1:8080")
    parser.add_argument("--session", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    parsed = urlsplit(args.core_url)
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        parser.error("core-url requires HTTPS or loopback HTTP")
    token = os.environ.get("COMPANION_MEDIA_SESSION_TOKEN", "")
    if not token:
        parser.error("COMPANION_MEDIA_SESSION_TOKEN is required")
    try:
        result = AndroidObserver(
            args.core_url, args.session, token, args.serial, args.adb
        ).capture()
        print(json.dumps({"event_id": result["event_id"], "raw_media_retention": "discard"}))
    except Exception:
        # Do not echo remote content, authorization headers, device stdout, or request bodies.
        raise SystemExit("ANDROID_OBSERVATION_FAILED") from None


if __name__ == "__main__":
    main()
