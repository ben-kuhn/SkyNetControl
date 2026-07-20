# backend/integrations/winlink/pat_inbound.py
"""Fetch inbound Winlink messages from PAT over HTTP and adapt them into the
dict shape read_message_file produces, so the existing import path is unchanged."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.integrations.winlink.pat_client import PatClient, PatUnavailable


def _parse_date(raw: str) -> datetime:
    if not raw:
        return datetime.now(tz=timezone.utc)
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(tz=timezone.utc)


def _adapt(client: PatClient, summary: dict) -> dict | None:
    mid = str(summary.get("MID") or summary.get("mid") or "")
    if not mid:
        return None
    msg = client.get_message("in", mid)
    from_addr = (msg.get("From") or "").strip()
    if not from_addr:
        return None
    attachments = []
    for f in msg.get("Files") or []:
        name = f.get("Name") or f.get("filename")
        if not name:
            continue
        data = client.get_attachment("in", mid, name)
        attachments.append({
            "filename": name,
            "content_type": f.get("ContentType") or "application/octet-stream",
            "data": data,
        })
    return {
        "path": f"pat-http://in/{mid}",
        "message_id": mid,
        "from_address": from_addr,
        "to_address": (msg.get("To") or "").strip(),
        "subject": (msg.get("Subject") or "").strip(),
        "received_at": _parse_date(msg.get("Date") or ""),
        "body": msg.get("Body") or "",
        "attachments": attachments,
    }


def fetch_inbound_messages(client: PatClient) -> list[dict]:
    """Return inbound messages in read_message_file shape. Best-effort: a message
    that can't be fetched/parsed is skipped, never raised."""
    try:
        summaries = client.list_mailbox("in")
    except PatUnavailable:
        raise
    out: list[dict] = []
    for summary in summaries:
        try:
            adapted = _adapt(client, summary)
        except PatUnavailable:
            raise
        except Exception:
            adapted = None
        if adapted is not None:
            out.append(adapted)
    return out
