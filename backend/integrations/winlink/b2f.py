"""Bidirectional B2F/FBB codec: parse and build the text-and-attachment layout
of decoded .b2f files (as PAT and Winlink Express write them).

Layout:
    <Header: value>\n         (repeated)
    Body: <N>\n
    \n
    <N bytes of body>
    File: <M> <filename>\n     (repeated, each followed by M bytes)

NOT the compressed B2 binary transfer protocol — these are already-decoded
files on disk.
"""
import mimetypes
import re
from dataclasses import dataclass, field


class B2FParseError(Exception):
    """Malformed .b2f content."""


@dataclass
class B2FAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class B2FMessage:
    headers: dict
    body: str
    attachments: list = field(default_factory=list)


_EXTRA_TYPES = {".b2f": "application/octet-stream", ".xml": "application/xml"}


def guess_content_type(filename: str) -> str:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in _EXTRA_TYPES:
        return _EXTRA_TYPES[ext]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def strip_b2f_header_chars(value: str) -> str:
    """Header-injection guard: the .b2f format is line-oriented, so CR/LF in a
    header value would split into extra headers. Replace with space and trim."""
    return re.sub(r"\s+", " ", value.replace("\r", " ").replace("\n", " ")).strip()


def parse_b2f(raw: bytes) -> B2FMessage:
    """Parse decoded .b2f bytes into headers, body, and attachments."""
    # Split the header block from the payload at the first blank line.
    sep = raw.find(b"\n\n")
    if sep == -1:
        raise B2FParseError("no header/body separator")
    header_block = raw[:sep].decode("utf-8", errors="replace")
    payload = raw[sep + 2:]

    headers: dict = {}
    body_len: int | None = None
    for line in header_block.split("\n"):
        if not line.strip():
            continue
        if ":" not in line:
            raise B2FParseError(f"malformed header line: {line!r}")
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        headers[key] = val
        if key.lower() == "body":
            try:
                body_len = int(val)
            except ValueError:
                raise B2FParseError(f"non-numeric Body length: {val!r}")

    if body_len is None:
        raise B2FParseError("missing Body header")
    if body_len > len(payload):
        raise B2FParseError("Body length exceeds payload")

    body = payload[:body_len].decode("utf-8", errors="replace")
    rest = payload[body_len:]

    attachments: list = []
    while rest:
        # Skip a single separator newline between sections if present.
        if rest.startswith(b"\n"):
            rest = rest[1:]
        if not rest:
            break
        nl = rest.find(b"\n")
        if nl == -1:
            raise B2FParseError("attachment header not terminated")
        line = rest[:nl].decode("utf-8", errors="replace")
        if not line.startswith("File:"):
            raise B2FParseError(f"expected File: section, got {line!r}")
        spec = line[len("File:"):].strip()
        parts = spec.split(" ", 1)
        if len(parts) != 2 or not parts[1].strip():
            raise B2FParseError(f"malformed File: line: {line!r}")
        try:
            length = int(parts[0])
        except ValueError:
            raise B2FParseError(f"non-numeric File length: {parts[0]!r}")
        filename = parts[1].strip()
        data_start = nl + 1
        data_end = data_start + length
        if data_end > len(rest):
            raise B2FParseError("File length exceeds remaining bytes")
        data = rest[data_start:data_end]
        attachments.append(B2FAttachment(filename, guess_content_type(filename), data))
        rest = rest[data_end:]

    return B2FMessage(headers=headers, body=body, attachments=attachments)


def build_b2f(
    *,
    message_id: str,
    from_addr: str,
    to_addr: str,
    subject: str,
    mbo: str,
    date: str,
    body: str,
    attachments=(),
) -> bytes:
    """Build a decoded .b2f file. Body-only output (no attachments) is
    byte-identical to the legacy WinlinkBackend format."""
    from_addr = strip_b2f_header_chars(from_addr)
    to_addr = strip_b2f_header_chars(to_addr)
    subject = strip_b2f_header_chars(subject)
    mbo = strip_b2f_header_chars(mbo)
    date = strip_b2f_header_chars(date)
    message_id = strip_b2f_header_chars(message_id)

    body_bytes = body.encode("utf-8")
    header = (
        f"Mid: {message_id}\n"
        f"From: {from_addr}\n"
        f"To: {to_addr}\n"
        f"Subject: {subject}\n"
        f"Mbo: {mbo}\n"
        f"Date: {date}\n"
        f"Body: {len(body_bytes)}\n"
        f"\n"
    )
    out = header.encode("utf-8") + body_bytes
    for att in attachments:
        out += f"\nFile: {len(att.data)} {att.filename}\n".encode("utf-8") + att.data
    return out
