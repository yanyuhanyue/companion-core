"""Privacy assertions must distinguish a digest from coincidental short ID digits."""

import json
import re

DIGEST_FIELDS = {
    "previous", "plan_sha256", "event_hash", "message_hash", "text_sha256",
    "approval_sha256", "item_sha256", "capture_sha256", "frame_sha256", "input_sha256",
}


def assert_private_journal(journal, secrets):
    def normalize(value, key=""):
        if key in DIGEST_FIELDS:
            assert isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value), (
                key, "invalid journal digest",
            )
            return "[SHA256]"
        if isinstance(value, dict):
            return {name: normalize(item, name) for name, item in value.items()}
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    records = [normalize(json.loads(line)) for line in journal.splitlines()]
    material = json.dumps(records, ensure_ascii=False)
    for secret in secrets:
        assert secret not in material
