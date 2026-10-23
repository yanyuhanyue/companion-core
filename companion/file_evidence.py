"""Bounded private-file configuration and evidence; no storage or service imports."""

import hashlib
import re
from dataclasses import asdict, replace
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from companion.contracts import StrictModel
from companion.file_parser import DEFAULT_LIMITS, VERSION, Limits, _result, _validate_result

SHA = r"^[a-f0-9]{64}$"


def safe_filename(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 255 or not value.strip()
        or value in {".", ".."}
        or re.search(r"[\\/:\x00-\x1f\x7f-\x9f\ud800-\udfff]", value)):
        raise ValueError("INVALID_FILE_NAME")
    return value


def safe_file_id(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 512 or not value.strip()
        or re.search(r"[\x00-\x1f\x7f-\x9f\ud800-\udfff]", value)):
        raise ValueError("INVALID_FILE_ID")
    return value


def _default_limits():
    return replace(DEFAULT_LIMITS, max_text_chars=12000)


class PrivateFilesConfig(StrictModel):
    enabled: bool = Field(default=False, strict=True)
    parser_image: str = Field(default="", max_length=256, strict=True)
    parser_socket: str = Field(default="", max_length=107, strict=True)
    adapter_id: str = Field(default="content_file", min_length=1, max_length=128, strict=True)
    max_files_per_event: int = Field(default=2, ge=1, le=64, strict=True)
    max_daily_files: int = Field(default=32, ge=0, le=1000, strict=True)
    max_daily_bytes: int = Field(default=64 * 1024 * 1024, ge=0, le=2**30, strict=True)
    wait_seconds: int = Field(default=75, ge=10, le=900, strict=True)
    upload_window_seconds: int = Field(default=300, ge=30, le=900, strict=True)
    upload_lease_seconds: int = Field(default=20, ge=5, le=90, strict=True)
    worker_lease_seconds: int = Field(default=50, ge=10, le=180, strict=True)
    spool_retention_seconds: int = Field(default=300, ge=30, le=900, strict=True)
    limits: Limits = Field(default_factory=_default_limits)

    @property
    def max_file_bytes(self):
        return self.limits.max_file_bytes

    @field_validator("limits", mode="before")
    @classmethod
    def typed_limits(cls, value):
        if isinstance(value, dict) and (not set(value) <= set(asdict(DEFAULT_LIMITS))
                                      or any(type(item) is not int for item in value.values())):
            raise ValueError("INVALID_PARSER_LIMITS")
        return asdict(_default_limits()) | value if isinstance(value, dict) else value

    @model_validator(mode="after")
    def bounded(self):
        if self.parser_socket and (
            not PurePosixPath(self.parser_socket).is_absolute()
            or str(PurePosixPath(self.parser_socket)) != self.parser_socket
            or self.parser_socket.startswith("//")
            or ".." in PurePosixPath(self.parser_socket).parts
            or re.search(r"[\x00-\x1f\x7f]", self.parser_socket)
            or len(self.parser_socket.encode()) > 107
        ):
            raise ValueError("INVALID_PARSER_SOCKET_PATH")
        if self.parser_image and not re.fullmatch(
            r"(?:[a-z0-9][a-z0-9./:_-]*@)?sha256:[0-9a-f]{64}", self.parser_image,
        ):
            raise ValueError("PINNED_PARSER_IMAGE_REQUIRED")
        if self.enabled and not self.parser_image:
            raise ValueError("PINNED_PARSER_IMAGE_REQUIRED")
        limits = self.limits
        if (limits.max_file_bytes > 128 * 1024 * 1024 or limits.max_text_chars > 12000
            or limits.max_records > 2000 or limits.max_pages > 1000
            or limits.max_table_depth > 8 or limits.max_expanded_bytes > 128 * 1024 * 1024
            or limits.max_zip_entries > 4096 or limits.max_compression_ratio > 1000
            or limits.max_pdf_stream_bytes > 32 * 1024 * 1024
            or limits.memory_bytes > 2**31 or limits.scratch_bytes > 512 * 1024 * 1024
            or limits.max_processes > 128 or limits.cpu_seconds > 60 or limits.wall_seconds > 90
            or self.worker_lease_seconds < limits.wall_seconds + 15
            or self.wait_seconds < self.upload_lease_seconds + self.worker_lease_seconds
            or self.spool_retention_seconds < self.worker_lease_seconds):
            raise ValueError("PRIVATE_FILE_LIMITS_INCONSISTENT")
        return self


class DocumentRecord(StrictModel):
    kind: Literal["paragraph", "table_cell", "page"]
    source: dict
    start: int = Field(ge=0, le=12000, strict=True)
    end: int = Field(ge=0, le=12000, strict=True)
    sha256: str = Field(pattern=SHA, strict=True)
    truncated: bool = Field(strict=True)

    @model_validator(mode="after")
    def location(self):
        source = self.source
        if self.end < self.start:
            raise ValueError("INVALID_DOCUMENT_SPAN")
        if self.kind == "page":
            keys = {"page"}
        elif self.kind == "paragraph" and "line_start" in source:
            keys = {"line_start", "line_end"}
        else:
            keys = {"part", "block", "paragraph"}
            if self.kind == "table_cell":
                keys |= {"table_path", "table", "row", "cell"}
        if set(source) != keys:
            raise ValueError("INVALID_DOCUMENT_LOCATION")
        for key, value in source.items():
            if key == "part":
                valid = value == "word/document.xml"
            elif key == "table_path":
                valid = (isinstance(value, list) and 3 <= len(value) <= 24 and len(value) % 3 == 0
                         and all(type(index) is int and index > 0 for index in value)
                         and value[-3:] == [source["table"], source["row"], source["cell"]])
            else:
                valid = type(value) is int and value > 0
            if not valid:
                raise ValueError("INVALID_DOCUMENT_LOCATION")
        if "line_end" in source and source["line_end"] < source["line_start"]:
            raise ValueError("INVALID_DOCUMENT_LOCATION")
        return self


class DocumentEvidence(StrictModel):
    version: Literal[1] = 1
    file_id: str = Field(min_length=1, max_length=512, strict=True)
    filename: str = Field(min_length=1, max_length=255, strict=True)
    mime_type: str = Field(max_length=256, strict=True)
    file_sha256: str = Field(pattern=SHA, strict=True)
    source_sha256: str = Field(pattern=SHA, strict=True)
    parser_version: Literal[VERSION]
    engine: str | None = Field(default=None, max_length=256, strict=True)
    text_sha256: str = Field(pattern=SHA, strict=True)
    partial: bool = Field(strict=True)
    issues: list[str] = Field(max_length=32)
    ocr: Literal["not_attempted"]
    isolation: Literal["docker_network_none"]
    records: list[DocumentRecord] = Field(max_length=2000)

    @field_validator("filename")
    @classmethod
    def filename_shape(cls, value):
        return safe_filename(value)

    @field_validator("file_id")
    @classmethod
    def identifier_shape(cls, value):
        return safe_file_id(value)

    @model_validator(mode="after")
    def consistent(self):
        if (len(set(self.issues)) != len(self.issues)
            or any(not re.fullmatch(r"[A-Z_]{1,64}", code) for code in self.issues)
            or self.partial != bool(self.issues)
            or (any(row.truncated for row in self.records) and "TEXT_LIMIT" not in self.issues)):
            raise ValueError("INVALID_DOCUMENT_COVERAGE")
        expected = 0
        for row in self.records:
            if row.start != expected:
                raise ValueError("INVALID_DOCUMENT_SPAN")
            expected = row.end + 1
        return self

    def validate_text(self, text):
        if (not isinstance(text, str) or len(text) > 12000
            or hashlib.sha256(text.encode()).hexdigest() != self.text_sha256
            or (self.records[-1].end if self.records else 0) != len(text)):
            raise ValueError("DOCUMENT_TEXT_CHANGED")
        for index, row in enumerate(self.records):
            if (hashlib.sha256(text[row.start:row.end].encode()).hexdigest() != row.sha256
                or (index and text[row.start - 1:row.start] != "\n")):
                raise ValueError("DOCUMENT_RECORD_CHANGED")
        return self

    @classmethod
    def from_parser(cls, result, *, file_id, source_sha256, limits=None):
        if (not isinstance(result, dict) or result.get("status") not in {"SUCCESS", "PARTIAL"}
            or result.get("isolation") != "docker_network_none"):
            raise ValueError("ISOLATED_DOCUMENT_RESULT_REQUIRED")
        expected = _result(b"", result.get("filename"), result.get("mime_type"))
        expected["file_sha256"] = result.get("file_sha256")
        _validate_result({**result, "isolation": "not_asserted"}, expected,
                         limits or _default_limits())
        rows, position = [], 0
        for row in result["records"]:
            rows.append({key: row[key] for key in ("kind", "source", "sha256", "truncated")}
                        | {"start": position, "end": position + len(row["text"])})
            position += len(row["text"]) + 1
        result = cls(file_id=file_id, source_sha256=source_sha256, records=rows,
                     **{key: result[key] for key in (
                         "filename", "mime_type", "file_sha256", "parser_version", "engine",
                         "text_sha256", "partial", "issues", "ocr", "isolation",
                     )}).validate_text(result["text"])
        return result
