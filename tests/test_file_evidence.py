import hashlib
from copy import deepcopy
from dataclasses import replace

import pytest
from pydantic import ValidationError

from companion.file_evidence import DocumentEvidence, PrivateFilesConfig
from companion.file_parser import DEFAULT_LIMITS, parse_document


def extracted():
    value = parse_document(b"first\n\nsecond", "notes.txt", "text/plain")
    value["isolation"] = "docker_network_none"  # Synthetic receipt, not a native isolation test.
    return value


def test_document_preserves_positions_hashes_and_does_not_duplicate_body():
    result = extracted()
    evidence = DocumentEvidence.from_parser(result, file_id="native", source_sha256="a" * 64)
    assert [(row.start, row.end) for row in evidence.records] == [(0, 5), (6, 12)]
    assert evidence.records[1].source == {"line_start": 3, "line_end": 3}
    assert "text" not in evidence.model_dump()
    assert evidence.validate_text("first\nsecond") is evidence
    with pytest.raises(ValueError, match="DOCUMENT_TEXT_CHANGED"):
        evidence.validate_text("first\nchanged")


@pytest.mark.parametrize("change", [
    {"isolation": "not_asserted"}, {"file_sha256": "bad"}, {"partial": True},
    {"text_sha256": "0" * 64}, {"text": "changed"}, {"ocr": "completed"},
])
def test_evidence_rejects_unisolated_or_changed_result(change):
    result = extracted() | change
    with pytest.raises((ValueError, ValidationError)):
        DocumentEvidence.from_parser(result, file_id="native", source_sha256="a" * 64)


def test_frozen_nondefault_limits_are_used_for_evidence_validation():
    result = extracted()
    limits = replace(DEFAULT_LIMITS, max_text_chars=12000, max_pages=500)
    assert DocumentEvidence.from_parser(result, file_id="native", source_sha256="a" * 64,
                                         limits=limits)
    with pytest.raises(ValueError):
        DocumentEvidence.from_parser(result, file_id="native", source_sha256="a" * 64,
                                      limits=replace(limits, max_records=1))


def test_record_position_or_hash_tampering_is_rejected():
    evidence = DocumentEvidence.from_parser(extracted(), file_id="native", source_sha256="a" * 64)
    altered = deepcopy(evidence.model_dump())
    altered["records"][1]["start"] = 7
    with pytest.raises(ValidationError, match="INVALID_DOCUMENT_SPAN"):
        DocumentEvidence.model_validate(altered)
    altered = deepcopy(evidence.model_dump())
    altered["records"][1]["sha256"] = "f" * 64
    with pytest.raises(ValueError, match="DOCUMENT_RECORD_CHANGED"):
        DocumentEvidence.model_validate(altered).validate_text("first\nsecond")


def test_empty_page_evidence_is_partial_and_does_not_claim_ocr():
    result = extracted()
    empty_sha = hashlib.sha256(b"").hexdigest()
    result.update(status="PARTIAL", partial=True, text="", text_sha256=empty_sha,
                  issues=["PAGE_WITHOUT_TEXT_LAYER"], records=[{
                      "kind": "page", "source": {"page": 1}, "text": "",
                      "sha256": empty_sha, "truncated": False,
                  }])
    evidence = DocumentEvidence.from_parser(result, file_id="native", source_sha256="a" * 64)
    assert evidence.validate_text("").partial
    assert evidence.ocr == "not_attempted"


@pytest.mark.parametrize("value", [
    {"enabled": True}, {"parser_image": "parser:latest"}, {"limits": {"max_file_bytes": True}},
    {"limits": {"max_text_chars": 12001}}, {"limits": {"max_file_bytes": 2**28}},
    {"worker_lease_seconds": 20}, {"spool_retention_seconds": 30},
    {"parser_socket": "relative.sock"}, {"parser_socket": "//shared/parser.sock"},
    {"parser_socket": "/run/../parser.sock"}, {"parser_socket": "/run/parse\x00.sock"},
    {"parser_socket": "/run/" + "中" * 36},
])
def test_file_configuration_is_closed_and_bounded(value):
    with pytest.raises(ValidationError):
        PrivateFilesConfig.model_validate(value)


def test_configurable_file_size_preserves_default_and_serializes_typed_limits():
    assert not PrivateFilesConfig().enabled
    assert PrivateFilesConfig().max_file_bytes == 8 * 1024 * 1024
    config = PrivateFilesConfig(limits=replace(DEFAULT_LIMITS, max_text_chars=12000,
                                              max_file_bytes=128 * 1024 * 1024))
    assert config.model_dump(mode="json")["limits"]["max_file_bytes"] == 128 * 1024 * 1024
    assert PrivateFilesConfig(parser_socket="/run/companion-parser/parser.sock").parser_socket
