import uuid
from datetime import datetime, timezone
from pathlib import Path

from backend.integrations.delivery.backends.base import DeliveryResult
from backend.integrations.winlink.b2f import build_b2f
from backend.integrations.winlink.pat_client import PatUnavailable
from backend.integrations.winlink.pat_config import build_pat_client


class WinlinkBackend:
    """Post via PAT HTTP when transport is enabled; fall back to writing a .b2f file."""

    def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
        pat_http = config.get("pat_http")
        if pat_http is not None and getattr(pat_http, "enabled", False):
            client = config.get("pat_client") or build_pat_client(pat_http)
            attachments = config.get("attachments") or []
            atts = [
                {"filename": a.filename, "content_type": a.content_type, "data": a.data}
                if hasattr(a, "filename") else a
                for a in attachments
            ]
            try:
                mid = client.post_outbound(
                    to=config["target_address"], subject=subject, body=body,
                    cc=[], attachments=atts,
                )
            except PatUnavailable as exc:
                return DeliveryResult(success=False, error=str(exc))
            return DeliveryResult(success=True, error=None, queued=True, pat_mid=mid or None)

        # --- existing file-based handoff below (unchanged) ---
        mailbox_path = config.get("mailbox_path", "")
        if not mailbox_path:
            return DeliveryResult(success=False, error="Winlink mailbox path not configured")

        target_address = config.get("target_address", "")
        callsign = config.get("callsign", "")
        # Optional attachments (used by forms composition in SP4b); a list of
        # B2FAttachment. Absent/empty for plain messages → byte-identical output.
        attachments = config.get("attachments") or ()

        try:
            out_dir = Path(mailbox_path) / "out"
            out_dir.mkdir(parents=True, exist_ok=True)

            message_id = uuid.uuid4().hex[:12].upper()
            now = datetime.now(tz=timezone.utc)
            date_str = now.strftime("%Y/%m/%d %H:%M")

            content = build_b2f(
                message_id=message_id,
                from_addr=callsign,
                to_addr=target_address,
                subject=subject,
                mbo=callsign,
                date=date_str,
                body=body,
                attachments=attachments,
            )

            filename = f"{message_id}.b2f"
            (out_dir / filename).write_bytes(content)

            return DeliveryResult(success=True, error=None)
        except Exception as exc:
            return DeliveryResult(success=False, error=str(exc))
