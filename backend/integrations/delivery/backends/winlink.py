import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.integrations.delivery.backends.base import DeliveryResult


def _strip_b2f_header_chars(value: str) -> str:
    """Strip CR/LF (and surrounding whitespace) from a B2F header value.

    Header injection guard: the .b2f format is line-oriented, so a value
    containing \\r or \\n would split into additional headers. `subject` is
    rendered from Jinja2 reminder/roster templates that may interpolate
    user-supplied comment text from check-ins; without this strip, a
    crafted comment could inject To:/Cc:/etc. and redirect the message.
    """
    return value.replace("\r", " ").replace("\n", " ").strip()


class WinlinkBackend:
    """Write a .b2f file to PAT's out/ directory for delivery on next sync."""

    def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
        mailbox_path = config.get("mailbox_path", "")
        if not mailbox_path:
            return DeliveryResult(success=False, error="Winlink mailbox path not configured")

        target_address = _strip_b2f_header_chars(config.get("target_address", ""))
        callsign = _strip_b2f_header_chars(config.get("callsign", ""))
        subject = _strip_b2f_header_chars(subject)

        try:
            out_dir = Path(mailbox_path) / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            message_id = uuid.uuid4().hex[:12].upper()
            now = datetime.now(tz=timezone.utc)
            date_str = now.strftime("%Y/%m/%d %H:%M")
            body_bytes = len(body.encode("utf-8"))

            b2f_content = (
                f"Mid: {message_id}\n"
                f"From: {callsign}\n"
                f"To: {target_address}\n"
                f"Subject: {subject}\n"
                f"Mbo: {callsign}\n"
                f"Date: {date_str}\n"
                f"Body: {body_bytes}\n"
                f"\n"
                f"{body}"
            )

            filename = f"{message_id}.b2f"
            (out_dir / filename).write_text(b2f_content)

            return DeliveryResult(success=True, error=None)
        except Exception as exc:
            return DeliveryResult(success=False, error=str(exc))
