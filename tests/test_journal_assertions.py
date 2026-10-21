import json

import pytest

from tests.journal_assertions import assert_private_journal


def test_valid_digest_can_contain_coincidental_private_identity_digits():
    record = {"previous": "a" * 64, "data": {
        "message_hash": "10002" + "b" * 59, "input_sha256": "c" * 59 + "10001",
    }}
    assert_private_journal(json.dumps(record), ("10001", "10002", "private canary"))


@pytest.mark.parametrize("data", [
    {"owner": "10002"}, {"owner": 10002}, {"text": "private input 10002"},
    {"text": "10002" + "a" * 59}, {"input_sha256": "10002"},
    {"text": "private canary"}, {"input_sha256": None},
    {"unknown_sha256": "10002" + "a" * 59},
])
def test_plaintext_and_malformed_digests_still_fail_privacy_assertion(data):
    with pytest.raises(AssertionError):
        assert_private_journal(json.dumps({"data": data}), ("10002", "private canary"))
