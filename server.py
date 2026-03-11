#!/usr/bin/env python3
"""AchoDetect backend server with forensic-style analysis model.

Stdlib-only implementation compatible with Python versions where `cgi` was
removed. Multipart parsing is handled manually for the single expected upload
field: `audio`.
"""

from __future__ import annotations

import os
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from forensic_model import ForensicVoiceModel

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "index.html"
RESULT = ROOT / "result.html"
UPLOADS = ROOT / "uploads"
UPLOADS.mkdir(exist_ok=True)
MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MODEL = ForensicVoiceModel()


def _json_bytes(payload: dict[str, Any]) -> bytes:
    import json

    return json.dumps(payload, indent=2).encode("utf-8")


def _get_boundary(content_type: str) -> bytes | None:
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part.split("=", 1)[1].strip('"')
            if boundary:
                return boundary.encode("utf-8")
    return None


def _infer_extension(filename: str, part_content_type: str, file_bytes: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix:
        return suffix

    ct = (part_content_type or "").lower()
    if "mpeg" in ct or "mp3" in ct:
        return ".mp3"
    if "wav" in ct or "wave" in ct:
        return ".wav"
    if "m4a" in ct or "mp4" in ct or "aac" in ct:
        return ".m4a"
    if "ogg" in ct:
        return ".ogg"

    head = file_bytes[:16]
    if head.startswith(b"ID3"):
        return ".mp3"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return ".mp3"
    if file_bytes.startswith(b"RIFF") and file_bytes[8:12] == b"WAVE":
        return ".wav"
    if file_bytes.startswith(b"OggS"):
        return ".ogg"
    if b"ftyp" in file_bytes[:64]:
        return ".m4a"

    return ".bin"


def _parse_multipart_audio(body: bytes, boundary: bytes) -> tuple[str, bytes, str] | None:
    """Extract (filename, content, part_content_type) for multipart field name="audio"."""
    delimiter = b"--" + boundary
    parts = body.split(delimiter)

    for part in parts:
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue

        header_blob, sep, content = part.partition(b"\r\n\r\n")
        if not sep:
            continue

        headers_text = header_blob.decode("utf-8", errors="ignore")
        if "name=\"audio\"" not in headers_text:
            continue

        filename = "upload"
        part_content_type = ""
        for header_line in headers_text.split("\r\n"):
            lower = header_line.lower()
            if lower.startswith("content-disposition:") and "filename=" in lower:
                raw = header_line.split("filename=", 1)[1].strip().strip('"')
                filename = Path(raw).name or filename
            if lower.startswith("content-type:"):
                part_content_type = header_line.split(":", 1)[1].strip()

        file_bytes = content.rstrip(b"\r\n")
        return filename, file_bytes, part_content_type

    return None


class AchoDetectHandler(BaseHTTPRequestHandler):
    server_version = "AchoDetectHTTP/2.1"

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, file_path: Path, content_type: str = "text/html; charset=utf-8") -> None:
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send_file(INDEX)
            return
        if path == "/result.html":
            if RESULT.exists():
                self._send_file(RESULT)
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "result.html not found"})
            return
        if path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {"status": "ok", "service": "AchoDetect backend", "engine": MODEL.engine_name},
            )
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/detect":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
            return

        content_length = int(self.headers.get("content-length", "0") or "0")
        if content_length <= 0:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Empty request body"})
            return
        if content_length > MAX_UPLOAD_BYTES:
            self._send_json(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                {"error": f"File too large. Max size is {MAX_UPLOAD_BYTES // (1024 * 1024)}MB."},
            )
            return

        content_type = self.headers.get("content-type", "")
        if "multipart/form-data" not in content_type:
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"error": "Use multipart/form-data with field 'audio'"},
            )
            return

        boundary = _get_boundary(content_type)
        if not boundary:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing multipart boundary"})
            return

        body = self.rfile.read(content_length)
        parsed = _parse_multipart_audio(body, boundary)
        if not parsed:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "Missing 'audio' file field"})
            return

        filename, file_bytes, part_content_type = parsed
        if not file_bytes:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "No file selected"})
            return

        inferred_ext = _infer_extension(filename, part_content_type, file_bytes)
        safe_name = Path(filename).name or "upload"
        if not Path(safe_name).suffix:
            safe_name = f"{safe_name}{inferred_ext}"

        unique_name = f"{uuid.uuid4().hex[:10]}_{safe_name}"
        output_path = UPLOADS / unique_name
        output_path.write_bytes(file_bytes)

        analysis = MODEL.analyze(output_path)
        self._send_json(HTTPStatus.OK, analysis)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"AchoDetect backend running at http://{host}:{port}")
    httpd = ThreadingHTTPServer((host, port), AchoDetectHandler)
    httpd.serve_forever()
