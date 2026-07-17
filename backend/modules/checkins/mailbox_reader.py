import logging
import os
import re
from datetime import datetime, timezone
from email import message_from_string, policy
from email.utils import parsedate_to_datetime
from pathlib import Path

from backend.integrations.winlink.b2f import B2FParseError, guess_content_type, parse_b2f

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 20 * 1024 * 1024

# Address-like tokens in a `To:` header: callsigns, local-parts, full emails.
_ADDRESS_TOKEN_RE = re.compile(r"[a-z0-9._%+\-]+(?:@[a-z0-9.\-]+)?")


def _to_matches_net(net_address: str, to_address: str) -> bool:
    """Return True if `to_address` is addressed to our net.

    PAT delivers inbound Winlink mail with `To: <CALLSIGN>` (bare local-part,
    no @domain). Outbound or relayed copies may use the full `user@winlink.org`
    form or RFC-2822 angle-bracketed forms. Accept any of:
      - net_address appears as a substring (handles full and angle-bracketed)
      - local-part of net_address equals any address-like token in to_address
    """
    net = net_address.strip().lower()
    to = to_address.strip().lower()
    if not net or not to:
        return False
    if net in to:
        return True
    local = net.split("@", 1)[0]
    if not local:
        return False
    return any(token == local for token in _ADDRESS_TOKEN_RE.findall(to))


def _parse_date(date_str: str) -> datetime:
    """Parse a date string (RFC 2822 or PAT B2F YYYY/MM/DD HH:MM) into a datetime."""
    received_at: datetime | None = None
    try:
        received_at = parsedate_to_datetime(date_str)
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        received_at = None
    if received_at is None:
        try:
            received_at = datetime.strptime(date_str.strip(), "%Y/%m/%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            received_at = datetime.now(timezone.utc)
    return received_at


def _cap_attachments(attachments: list[dict]) -> list[dict]:
    kept: list[dict] = []
    total = 0
    for att in attachments:
        size = len(att["data"])
        if size > MAX_ATTACHMENT_BYTES:
            logger.warning("Skipping oversized attachment %s (%d bytes)", att["filename"], size)
            continue
        if total + size > MAX_TOTAL_ATTACHMENT_BYTES:
            logger.warning("Attachment total cap reached; skipping %s", att["filename"])
            continue
        total += size
        kept.append(att)
    return kept


def _email_attachments(msg) -> list[dict]:
    out = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = part.get_content_disposition()
        filename = part.get_filename()
        if disp == "attachment" or (filename and part.get_content_maintype() != "text"):
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            out.append({
                "filename": filename or "attachment",
                "content_type": part.get_content_type() or guess_content_type(filename or ""),
                "data": payload,
            })
    return out


def _read_b2f(file_path: Path) -> dict | None:
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        logger.warning("Cannot read mailbox message %s: %s", file_path, exc)
        return None
    try:
        msg = parse_b2f(raw)
    except B2FParseError:
        logger.debug("Malformed .b2f, falling back to email parser: %s", file_path)
        return None

    headers = msg.headers
    message_id = (headers.get("Message-Id") or headers.get("Mid") or "").strip()
    from_address = (headers.get("From") or "").strip()
    if not message_id or not from_address:
        return None
    received_at = _parse_date(headers.get("Date", ""))
    attachments = _cap_attachments(
        [{"filename": a.filename, "content_type": a.content_type, "data": a.data} for a in msg.attachments]
    )
    return {
        "path": str(file_path),
        "message_id": message_id,
        "from_address": from_address,
        "to_address": (headers.get("To") or "").strip(),
        "subject": (headers.get("Subject") or "").strip(),
        "received_at": received_at,
        "body": msg.body.strip(),
        "attachments": attachments,
    }


def _read_email(file_path: Path) -> dict | None:
    try:
        text = file_path.read_text(errors="replace")
    except OSError as exc:
        # Most often PermissionError when the service user can't traverse
        # into the PAT mailbox tree — log loudly so it's not silently a 0-import.
        logger.warning("Cannot read mailbox message %s: %s", file_path, exc)
        return None

    try:
        msg = message_from_string(text, policy=policy.default)

        # PAT writes B2F files with `Mid:` instead of `Message-Id:` and a
        # `YYYY/MM/DD HH:MM` Date (no RFC 2822 framing). Fall back to those
        # so we don't silently drop every PAT-delivered message.
        message_id = msg.get("Message-Id", "").strip() or msg.get("Mid", "").strip()
        from_address = msg.get("From", "").strip()
        to_address = msg.get("To", "").strip()
        subject = msg.get("Subject", "").strip()
        date_str = msg.get("Date", "")

        if not message_id or not from_address:
            return None

        received_at = _parse_date(date_str)

        body = msg.get_body(preferencelist=("plain",))
        body_text = body.get_content().strip() if body else ""

        attachments = _cap_attachments(_email_attachments(msg))

        return {
            "path": str(file_path),
            "message_id": message_id,
            "from_address": from_address,
            "to_address": to_address,
            "subject": subject,
            "received_at": received_at,
            "body": body_text,
            "attachments": attachments,
        }
    except Exception:
        logger.exception("Failed to parse mailbox message %s", file_path)
        return None


def read_message_file(file_path: Path | str) -> dict | None:
    """Read a single MIME-format message file and return parsed headers + body.

    Returns None if the file cannot be parsed.
    """
    file_path = Path(file_path)
    if file_path.suffix.lower() == ".b2f":
        parsed = _read_b2f(file_path)
        if parsed is not None:
            return parsed
        # Fall through to the generic email parser on codec failure.
    return _read_email(file_path)


def read_mailbox(
    mailbox_path: str,
    net_address: str,
) -> list[dict]:
    """Read all message files from a mailbox directory, filtered by net address.

    Reads all files with common message extensions (.mime, .b2f, .eml).
    Filters to only messages addressed to net_address (case-insensitive).
    """
    if not os.path.isdir(mailbox_path):
        return []

    extensions = {".mime", ".b2f", ".eml"}
    messages = []

    try:
        filenames = os.listdir(mailbox_path)
    except OSError as exc:
        # PermissionError here means the service can see the directory exists
        # but can't enumerate it (perms on the dir or a parent in the chain).
        logger.warning("Cannot list mailbox %s: %s", mailbox_path, exc)
        return []

    for filename in filenames:
        file_path = Path(mailbox_path) / filename
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in extensions:
            continue

        parsed = read_message_file(file_path)
        if parsed is None:
            continue

        if not _to_matches_net(net_address, parsed.get("to_address", "")):
            continue

        messages.append(parsed)

    return messages
