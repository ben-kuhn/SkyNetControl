import logging
import os
import re
from datetime import datetime, timezone
from email import message_from_string, policy
from email.utils import parsedate_to_datetime
from pathlib import Path

logger = logging.getLogger(__name__)

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


def read_message_file(file_path: Path | str) -> dict | None:
    """Read a single MIME-format message file and return parsed headers + body.

    Returns None if the file cannot be parsed.
    """
    file_path = Path(file_path)
    try:
        text = file_path.read_text(errors="replace")
    except OSError as exc:
        # Most often PermissionError when the service user can't traverse
        # into the PAT mailbox tree — log loudly so it's not silently a 0-import.
        logger.warning("Cannot read mailbox message %s: %s", file_path, exc)
        return None

    try:
        msg = message_from_string(text, policy=policy.default)

        message_id = msg.get("Message-Id", "").strip()
        from_address = msg.get("From", "").strip()
        to_address = msg.get("To", "").strip()
        subject = msg.get("Subject", "").strip()
        date_str = msg.get("Date", "")

        if not message_id or not from_address:
            return None

        try:
            received_at = parsedate_to_datetime(date_str)
            if received_at.tzinfo is None:
                received_at = received_at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            received_at = datetime.now(timezone.utc)

        body = msg.get_body(preferencelist=("plain",))
        body_text = body.get_content().strip() if body else ""

        return {
            "message_id": message_id,
            "from_address": from_address,
            "to_address": to_address,
            "subject": subject,
            "received_at": received_at,
            "body": body_text,
        }
    except Exception:
        logger.exception("Failed to parse mailbox message %s", file_path)
        return None


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
