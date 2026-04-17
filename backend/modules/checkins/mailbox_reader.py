import os
from datetime import datetime, timezone
from email import message_from_string, policy
from email.utils import parsedate_to_datetime
from pathlib import Path


def read_message_file(file_path: Path | str) -> dict | None:
    """Read a single MIME-format message file and return parsed headers + body.

    Returns None if the file cannot be parsed.
    """
    file_path = Path(file_path)
    try:
        text = file_path.read_text(errors="replace")
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

    net_addr_lower = net_address.lower()
    extensions = {".mime", ".b2f", ".eml"}
    messages = []

    for filename in os.listdir(mailbox_path):
        file_path = Path(mailbox_path) / filename
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in extensions:
            continue

        parsed = read_message_file(file_path)
        if parsed is None:
            continue

        to_addr = parsed.get("to_address", "").lower()
        if net_addr_lower not in to_addr:
            continue

        messages.append(parsed)

    return messages
