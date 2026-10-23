"""Bounded text extraction, without Core, storage, model, or delivery responsibilities.

Use ``parse_isolated`` for untrusted input. ``parse_document`` is the low-level
parser for an already isolated worker (and synthetic fixtures), not a sandbox.
The pinned image must copy this module to /app/file_parser.py and install
python-docx==1.2.0, pypdf==6.21.2 and, optionally, LibreOffice. Windows hosts
use the same Linux container through Docker Desktop. No host-parser fallback.

Upstream interfaces: https://python-docx.readthedocs.io/en/latest/api/document.html
https://pypdf.readthedocs.io/en/stable/modules/configuration.html
https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html
https://github.com/LibreOffice/core/blob/master/officecfg/registry/schema/org/openoffice/Office/Common.xcs
"""

import argparse
import hashlib
import io
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

VERSION = "file-parser/1"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIMES = {".docx": {DOCX_MIME}, ".pdf": {"application/pdf"}, ".doc": {"application/msword"},
         ".txt": {"text/plain"}, ".md": {"text/markdown", "text/plain"}}
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 8 * 1024 * 1024
    max_expanded_bytes: int = 32 * 1024 * 1024
    max_zip_entries: int = 512
    max_compression_ratio: int = 200
    max_pages: int = 200
    max_records: int = 2000
    max_text_chars: int = 100_000
    max_table_depth: int = 8
    max_pdf_stream_bytes: int = 8 * 1024 * 1024
    cpu_seconds: int = 15
    wall_seconds: int = 30
    memory_bytes: int = 768 * 1024 * 1024
    scratch_bytes: int = 128 * 1024 * 1024
    max_processes: int = 96

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise ValueError("INVALID_PARSER_LIMITS")
        if self.memory_bytes < 64 * 1024 * 1024 or self.max_file_bytes > self.scratch_bytes:
            raise ValueError("INVALID_PARSER_LIMITS")

    @property
    def max_result_bytes(self):
        return self.max_text_chars * 12 + self.max_records * 1024 + 8192


DEFAULT_LIMITS = Limits()


class _Failure(Exception):
    def __init__(self, code, *, unavailable=False):
        self.code, self.unavailable = code, unavailable


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _result(data, filename, mime_type):
    return {"schema_version": 1, "file_sha256": _sha(data), "filename": filename,
            "mime_type": mime_type, "parser_version": VERSION, "engine": None,
            "status": "ERROR", "error_code": None, "partial": False, "issues": [],
            "text": "", "text_sha256": _sha(b""), "records": [],
            "trust": "untrusted_file", "ocr": "not_attempted", "isolation": "not_asserted"}


def _identify(data, filename, mime_type, limits):
    if not isinstance(data, bytes):
        raise TypeError("FILE_BYTES_REQUIRED")
    if (not isinstance(filename, str) or not 1 <= len(filename) <= 255
        or filename in {".", ".."} or re.search(r'[\\/:\x00-\x1f\x7f]', filename)):
        raise _Failure("INVALID_FILENAME")
    if not isinstance(mime_type, str) or len(mime_type) > 256:
        raise _Failure("INVALID_MIME_TYPE")
    extension = Path(filename).suffix.lower()
    mime = mime_type.split(";", 1)[0].strip().lower()
    if extension not in MIMES:
        raise _Failure("UNSUPPORTED_FILE_TYPE")
    if mime not in MIMES[extension] | {"", "application/octet-stream"}:
        raise _Failure("FILE_TYPE_MISMATCH")
    if not data:
        raise _Failure("EMPTY_FILE")
    if len(data) > limits.max_file_bytes:
        raise _Failure("FILE_TOO_LARGE")
    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        raise _Failure("INVALID_PDF")
    if extension == ".doc" and not data.startswith(OLE):
        raise _Failure("INVALID_DOC")
    return extension


class _Records:
    def __init__(self, limits):
        self.limits, self.rows, self.issues, self.chars = limits, [], [], 0

    def issue(self, code):
        if code not in self.issues:
            self.issues.append(code)

    def add(self, text, kind, **source):
        if not text.strip() and kind != "page":
            return True
        if len(self.rows) >= self.limits.max_records:
            self.issue("RECORD_LIMIT")
            return False
        remaining = self.limits.max_text_chars - self.chars - bool(self.rows)
        if remaining <= 0:
            self.issue("TEXT_LIMIT")
            return False
        truncated = len(text) > remaining
        text = text[:remaining]
        self.rows.append({"kind": kind, "source": source, "text": text,
                          "sha256": _sha(text.encode("utf-8")), "truncated": truncated})
        self.chars += len(text) + (len(self.rows) > 1)
        if truncated:
            self.issue("TEXT_LIMIT")
        return not truncated


def _check_docx(data, records):
    limits = records.limits
    if data.startswith(OLE):
        raise _Failure("ENCRYPTED_OR_LEGACY_DOCX")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) > limits.max_zip_entries:
            raise _Failure("ZIP_ENTRY_LIMIT")
        names, expanded = set(), 0
        for info in infos:
            name = info.filename
            path = PurePosixPath(name)
            if (info.orig_filename != name or name in names or path.is_absolute()
                or ".." in path.parts or "\\" in name
                or ":" in name or stat.S_ISLNK(info.external_attr >> 16)):
                raise _Failure("UNSAFE_ZIP_ENTRY")
            names.add(name)
            if info.flag_bits & 1:
                raise _Failure("ENCRYPTED_DOCUMENT")
            expanded += info.file_size
            if expanded > limits.max_expanded_bytes or (
                info.file_size > max(1, info.compress_size) * limits.max_compression_ratio
            ):
                raise _Failure("ZIP_EXPANSION_LIMIT")
            # Check every entry's actual bytes/CRC; never extract ZIP paths onto disk.
            with archive.open(info) as entry:
                blob = entry.read(min(info.file_size, limits.max_expanded_bytes) + 1)
            if len(blob) != info.file_size:
                raise _Failure("INVALID_ZIP_ENTRY")
            if name.endswith((".xml", ".rels")) and re.search(
                rb"<!\s*(DOCTYPE|ENTITY)\b", blob.replace(b"\x00", b""), re.I,
            ):
                raise _Failure("XML_DTD_FORBIDDEN")
            if name.startswith("word/media/"):
                records.issue("IMAGE_CONTENT_NOT_EXTRACTED")
            if re.match(r"word/(header|footer|footnotes|endnotes|comments)", name):
                records.issue("NON_BODY_CONTENT_NOT_EXTRACTED")
            if "vbaProject" in name or name.startswith("word/embeddings/"):
                records.issue("ACTIVE_CONTENT_NOT_EXTRACTED")
        if not {"[Content_Types].xml", "word/document.xml"} <= names:
            raise _Failure("INVALID_DOCX")


def _docx(data, records):
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    _check_docx(data, records)
    document = docx.Document(io.BytesIO(data))

    def walk(parent, path=(), depth=0):
        if depth > records.limits.max_table_depth:
            records.issue("TABLE_DEPTH_LIMIT")
            return True
        paragraph_number, table_number = 0, 0
        for index, block in enumerate(parent.iter_inner_content(), 1):
            if isinstance(block, Paragraph):
                paragraph_number += 1
                source = {"part": "word/document.xml", "block": index,
                          "paragraph": paragraph_number}
                if path:
                    source.update(table_path=list(path), table=path[-3], row=path[-2],
                                  cell=path[-1])
                if not records.add(block.text, "table_cell" if path else "paragraph",
                                   **source):
                    return False
            elif isinstance(block, Table):
                table_number += 1
                seen = set()
                for row_number, row in enumerate(block.rows, 1):
                    for cell_number, cell in enumerate(row.cells, 1):
                        # A merged cell appears at each grid coordinate in python-docx.
                        if cell._tc in seen:
                            continue
                        seen.add(cell._tc)
                        location = (*path, table_number, row_number, cell_number)
                        if not walk(cell, location, depth + 1):
                            return False
        return True

    if any(element.tag.rsplit("}", 1)[-1] in {"ins", "del", "sdt", "txbxContent"}
           for element in document.element.iter()):
        records.issue("DOCX_STRUCTURE_NOT_FULLY_EXTRACTED")
    walk(document)
    return "python-docx/" + docx.__version__


def _pdf(data, records):
    import pypdf
    from pypdf.errors import LimitReachedError, PdfReadError

    cap = records.limits.max_pdf_stream_bytes
    # Public pypdf configuration limits decoding before extract_text allocates a page stream.
    with pypdf.apply_configuration(
        maximum_declared_stream_length=cap, array_based_stream_maximum_output_length=cap,
        zlib_maximum_output_length=cap, lzw_maximum_output_length=cap,
        run_length_maximum_output_length=cap, brotli_maximum_output_length=cap,
        jbig2_maximum_output_length=cap, image_maximum_buffer_size=cap, jbig2dec_binary=None,
        stream_decoding_work_maximum_length=records.limits.max_expanded_bytes,
        page_tree_maximum_entries=max(1000, records.limits.max_pages * 8),
        page_tree_maximum_depth=32, stream_parsing_work_maximum_count=200_000,
    ):
        try:
            reader = pypdf.PdfReader(io.BytesIO(data), strict=True)
        except (LimitReachedError, PdfReadError):
            raise _Failure("INVALID_PDF") from None
        if reader.is_encrypted:
            raise _Failure("ENCRYPTED_DOCUMENT")
        total = len(reader.pages)
        if total > records.limits.max_pages:
            records.issue("PAGE_LIMIT")
        for index in range(min(total, records.limits.max_pages)):
            try:
                page = reader.pages[index]
                content = page.get_contents()
                if content is not None and len(content.get_data()) > cap:
                    records.issue("PDF_STREAM_LIMIT")
                    continue
                text = page.extract_text() or ""
            except (LimitReachedError, PdfReadError, RecursionError, ValueError):
                records.issue("PDF_PAGE_NOT_EXTRACTED")
                continue
            if not text.strip():
                records.issue("PAGE_WITHOUT_TEXT_LAYER")
            if not records.add(text, "page", page=index + 1):
                break
    return "pypdf/" + pypdf.__version__


def _text(data, mime_type, records):
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    if charset := re.search(r"(?:^|;)\s*charset=\"?([A-Za-z0-9_-]+)", mime_type, re.I):
        encoding = charset[1].lower()
        if encoding not in {"utf-8", "utf-8-sig", "utf-16", "utf-16le", "utf-16be", "gb18030"}:
            raise _Failure("UNSUPPORTED_TEXT_ENCODING")
    try:
        text = data.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError:
        raise _Failure("INVALID_TEXT_ENCODING") from None
    if "\x00" in text:
        raise _Failure("BINARY_TEXT_INPUT")
    start, paragraph = 1, []
    for line_number, line in enumerate([*text.splitlines(), ""], 1):
        if line.strip():
            if not paragraph:
                start = line_number
            paragraph.append(line)
        elif paragraph:
            if not records.add("\n".join(paragraph), "paragraph", line_start=start,
                               line_end=line_number - 1):
                break
            paragraph = []
    return "text/1"


def parse_document(data: bytes, filename: str, mime_type: str = "", *, limits=DEFAULT_LIMITS):
    """Extract in an already isolated process. This function itself is not OS isolation."""
    result = _result(data, filename, mime_type)
    records = _Records(limits)
    try:
        kind = _identify(data, filename, mime_type, limits)
        if kind == ".doc":
            raise _Failure("DOC_CONVERTER_REQUIRED", unavailable=True)
        result["engine"] = (_docx(data, records) if kind == ".docx" else
                            _pdf(data, records) if kind == ".pdf" else
                            _text(data, mime_type, records))
        text = "\n".join(row["text"] for row in records.rows)
        if not text.strip() and not records.issues:
            raise _Failure("EMPTY_TEXT")
        result.update(status="PARTIAL" if records.issues else "SUCCESS",
                      partial=bool(records.issues),
                      text=text, text_sha256=_sha(text.encode()), records=records.rows,
                      issues=records.issues)
    except ImportError:
        result.update(status="UNAVAILABLE", error_code="PARSER_DEPENDENCY_UNAVAILABLE")
    except _Failure as error:
        result.update(status="UNAVAILABLE" if error.unavailable else "ERROR",
                      error_code=error.code)
    except Exception:
        # Decoder libraries raise format-specific exceptions for hostile/corrupt input.
        # Keep that boundary fail-closed; resource containment belongs to the OS worker.
        result["error_code"] = "INVALID_DOCUMENT"
    return result


def _convert_doc(data, scratch, limits):
    executable = shutil.which("soffice")
    if not executable:
        raise _Failure("DOC_CONVERTER_UNAVAILABLE", unavailable=True)
    profile = scratch / "profile"
    (profile / "user").mkdir(parents=True)
    properties = ("DisableMacrosExecution", "DisableActiveContent", "BlockUntrustedRefererLinks")
    # LibreOffice's documented registry options disable scripts and active document content.
    entries = "".join(f'<prop oor:name="{name}" oor:op="fuse" oor:finalized="true">'
                      '<value>true</value></prop>' for name in properties)
    (profile / "user" / "registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        + entries + '</item></oor:items>', encoding="utf-8",
    )
    source, destination = scratch / "input.doc", scratch / "output"
    source.write_bytes(data)
    destination.mkdir()
    try:
        completed = subprocess.run([
            executable, "-env:UserInstallation=" + profile.as_uri(), "--headless", "--nologo",
            "--nodefault", "--norestore", "--convert-to", "docx:Office Open XML Text",
            "--outdir", str(destination), str(source),
        ], shell=False, cwd=scratch, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=limits.wall_seconds, check=False)
    except subprocess.TimeoutExpired:
        raise _Failure("DOC_CONVERSION_TIMEOUT") from None
    output = destination / "input.docx"
    if completed.returncode or not output.is_file() or output.is_symlink():
        raise _Failure("DOC_CONVERSION_FAILED")
    with output.open("rb") as stream:
        converted = stream.read(limits.max_file_bytes + 1)
    if len(converted) > limits.max_file_bytes:
        raise _Failure("DOC_CONVERSION_SIZE_LIMIT")
    return converted


def _worker(data, filename, mime_type, limits, scratch):
    try:
        kind = _identify(data, filename, mime_type, limits)
        if kind != ".doc":
            return parse_document(data, filename, mime_type, limits=limits)
        with tempfile.TemporaryDirectory(prefix="document-", dir=scratch) as directory:
            converted = _convert_doc(data, Path(directory), limits)
        result = parse_document(converted, "input.docx", DOCX_MIME, limits=limits)
        result.update(file_sha256=_sha(data), filename=filename, mime_type=mime_type)
        result["engine"] = "LibreOffice/doc-to-docx+" + (result["engine"] or VERSION)
        if result["status"] in {"SUCCESS", "PARTIAL"}:
            result.update(status="PARTIAL", partial=True)
            result["issues"].append("DOC_CONVERTED_TO_DOCX")
        return result
    except _Failure as error:
        result = _result(data, filename, mime_type)
        result.update(status="UNAVAILABLE" if error.unavailable else "ERROR",
                      error_code=error.code)
        return result
    except OSError:
        result = _result(data, filename, mime_type)
        result["error_code"] = "DOC_CONVERSION_FAILED"
        return result


def _validate_result(candidate, expected, limits):
    if not isinstance(candidate, dict) or set(candidate) != set(expected):
        raise ValueError("PARSER_PROTOCOL_INVALID")
    for key in ("schema_version", "file_sha256", "filename", "mime_type", "parser_version",
                "trust", "ocr", "isolation"):
        if type(candidate[key]) is not type(expected[key]) or candidate[key] != expected[key]:
            raise ValueError("PARSER_PROTOCOL_INVALID")
    status, rows, text = candidate["status"], candidate["records"], candidate["text"]
    issues, error = candidate["issues"], candidate["error_code"]
    if (status not in {"SUCCESS", "PARTIAL", "ERROR", "UNAVAILABLE"}
        or type(candidate["partial"]) is not bool or candidate["partial"] != (status == "PARTIAL")
        or not isinstance(issues, list) or len(issues) > 32
        or any(not isinstance(code, str) or not re.fullmatch(r"[A-Z_]{1,64}", code)
               for code in issues)
        or len(set(issues)) != len(issues)
        or not isinstance(rows, list) or len(rows) > limits.max_records
        or not isinstance(text, str) or len(text) > limits.max_text_chars
        or candidate["text_sha256"] != _sha(text.encode())
        or (candidate["engine"] is not None and (not isinstance(candidate["engine"], str)
                                                or len(candidate["engine"]) > 256))):
        raise ValueError("PARSER_PROTOCOL_INVALID")
    if status in {"SUCCESS", "PARTIAL"}:
        if error is not None or bool(issues) != (status == "PARTIAL"):
            raise ValueError("PARSER_PROTOCOL_INVALID")
        if status == "SUCCESS" and not text.strip():
            raise ValueError("PARSER_PROTOCOL_INVALID")
    elif (not isinstance(error, str) or not re.fullmatch(r"[A-Z_]{1,64}", error)
          or text or rows or issues):
        raise ValueError("PARSER_PROTOCOL_INVALID")
    for row in rows:
        if (not isinstance(row, dict)
            or set(row) != {"kind", "source", "text", "sha256", "truncated"}
            or row["kind"] not in {"paragraph", "table_cell", "page"}
            or not isinstance(row["text"], str) or type(row["truncated"]) is not bool
            or row["sha256"] != _sha(row["text"].encode())
            or not isinstance(row["source"], dict)):
            raise ValueError("PARSER_PROTOCOL_INVALID")
        source = row["source"]
        if row["kind"] == "page":
            keys = {"page"}
        elif "line_start" in source and row["kind"] == "paragraph":
            keys = {"line_start", "line_end"}
        else:
            keys = {"part", "block", "paragraph"}
            if row["kind"] == "table_cell":
                keys |= {"table_path", "table", "row", "cell"}
        if set(source) != keys:
            raise ValueError("PARSER_PROTOCOL_INVALID")
        for key, value in source.items():
            if key == "part":
                valid = value == "word/document.xml"
            elif key == "table_path":
                valid = (isinstance(value, list) and 3 <= len(value) <= limits.max_table_depth * 3
                         and len(value) % 3 == 0
                         and all(type(index) is int and index > 0 for index in value))
            else:
                valid = type(value) is int and value > 0
            if not valid:
                raise ValueError("PARSER_PROTOCOL_INVALID")
    if text != "\n".join(row["text"] for row in rows):
        raise ValueError("PARSER_PROTOCOL_INVALID")


def _container_argv(docker, image, name, filename, mime_type, limits, *, owner_label=None):
    if not re.fullmatch(r"(?:[a-z0-9][a-z0-9./:_-]*@)?sha256:[0-9a-f]{64}", image):
        raise ValueError("PINNED_PARSER_IMAGE_REQUIRED")
    if not re.fullmatch(r"companion-file-(?:fixtures-)?[0-9a-f]{32}", name):
        raise ValueError("INVALID_PARSER_CONTAINER_NAME")
    if owner_label is not None and not re.fullmatch(r"[0-9a-f]{32}", owner_label):
        raise ValueError("INVALID_PARSER_OWNER_LABEL")
    labels = [] if owner_label is None else ["--label=com.companion.parser-host=" + owner_label]
    return [docker, "run", "--rm", "--pull=never", "--name=" + name, "--network=none",
            "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--user=10003:10003", "--cpus=1", "--memory=" + str(limits.memory_bytes),
            "--memory-swap=" + str(limits.memory_bytes),
            "--pids-limit=" + str(limits.max_processes),
            f"--ulimit=cpu={limits.cpu_seconds}:{limits.cpu_seconds}",
            f"--ulimit=fsize={limits.scratch_bytes}:{limits.scratch_bytes}",
            "--tmpfs=/scratch:rw,nosuid,nodev,noexec,size=" + str(limits.scratch_bytes)
            + ",uid=10003,gid=10003,mode=0700", "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=33554432",
            "--workdir=/scratch", "--env=TMPDIR=/scratch", "--env=HOME=/scratch",
            "--log-driver=none", *labels, "--entrypoint=/usr/bin/timeout", "-i", image,
            "--signal=KILL", str(limits.wall_seconds) + "s", "python", "/app/file_parser.py",
            "--worker", "--filename=" + filename, "--mime-type=" + mime_type,
            "--limits=" + json.dumps(asdict(limits), separators=(",", ":"))]


def parse_isolated(data: bytes, filename: str, mime_type: str = "", *, image: str,
                   workspace: str | Path, limits=DEFAULT_LIMITS, docker="docker",
                   container_name=None, owner_label=None, docker_env=None):
    """Run once in a pinned, offline Linux image. Never substitute host-side parsing."""
    result = _result(data, filename, mime_type)
    try:
        _identify(data, filename, mime_type, limits)
        name = container_name or "companion-file-" + uuid.uuid4().hex
        command = _container_argv(docker, image, name,
                                  filename, mime_type, limits, owner_label=owner_label)
    except _Failure as error:
        result["error_code"] = error.code
        return result
    root = Path(workspace).resolve(strict=True)
    scratch = root / ".local" / "file-parser"
    if not scratch.resolve().is_relative_to(root):
        raise ValueError("PARSER_SCRATCH_OUTSIDE_WORKSPACE")
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="launch-", dir=scratch) as directory:
        try:
            process = subprocess.Popen(command, shell=False, cwd=directory, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       env=docker_env,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt"
                                       else 0)
        except OSError:
            result.update(status="UNAVAILABLE", error_code="PARSER_SANDBOX_UNAVAILABLE")
            return result
        output, failure = [], []

        def feed():
            try:
                process.stdin.write(data)
                process.stdin.close()
            except (OSError, ValueError):
                pass

        def receive():
            try:
                output.append(process.stdout.read(limits.max_result_bytes + 1))
                if len(output[0]) > limits.max_result_bytes:
                    failure.append("PARSER_OUTPUT_LIMIT")
                else:
                    process.wait()
            except (OSError, ValueError):
                failure.append("PARSER_PROCESS_FAILED")

        sender = threading.Thread(target=feed, daemon=True)
        receiver = threading.Thread(target=receive, daemon=True)
        sender.start()
        receiver.start()
        receiver.join(limits.wall_seconds)
        if receiver.is_alive() or failure:
            code = failure[0] if failure else "PARSER_TIMEOUT"
            try:
                removed = subprocess.run([docker, "rm", "--force", command[4].split("=", 1)[1]],
                    shell=False, cwd=directory, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, env=docker_env,
                    timeout=5, check=False)
                if removed.returncode:
                    code = "PARSER_CLEANUP_UNCONFIRMED"
            except (OSError, subprocess.TimeoutExpired):
                code = "PARSER_CLEANUP_UNCONFIRMED"
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                code = "PARSER_CLEANUP_UNCONFIRMED"
            receiver.join(5)
            result["error_code"] = code
        elif process.returncode:
            result.update(status="UNAVAILABLE", error_code="PARSER_SANDBOX_UNAVAILABLE")
        else:
            try:
                candidate = json.loads(output[0])
                _validate_result(candidate, result, limits)
                result = {**candidate, "isolation": "docker_network_none"}
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                result["error_code"] = "PARSER_PROTOCOL_INVALID"
        sender.join(5)
        if not sender.is_alive():
            process.stdin.close()
        if not receiver.is_alive():
            process.stdout.close()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="One-shot file parser for an isolated Linux image")
    parser.add_argument("--worker", action="store_true", required=True)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--mime-type", default="")
    parser.add_argument("--limits", default="{}")
    parser.add_argument("--scratch", type=Path, default=Path("/scratch"))
    args = parser.parse_args(argv)
    limits = Limits(**json.loads(args.limits))
    # Paths are launcher configuration, never derived from the supplied document filename.
    scratch = args.scratch.resolve(strict=True)
    logging.disable(logging.CRITICAL)
    data = sys.stdin.buffer.read(limits.max_file_bytes + 1)
    result = _worker(data, args.filename, args.mime_type, limits, scratch)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(encoded + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
