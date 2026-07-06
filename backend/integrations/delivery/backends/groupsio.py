import html
import logging

import httpx
from httpx import HTTPStatusError

from backend.integrations.delivery.backends.base import DeliveryResult

BASE_URL = "https://groups.io/api/v1"

logger = logging.getLogger(__name__)


def _format_http_error(exc: HTTPStatusError) -> str:
    # raise_for_status() produces a message without the response body, so the
    # actual groups.io error (bad group_name, moderation required, etc.) is
    # invisible to operators. Include a snippet of the body in the stored error.
    body = (exc.response.text or "").strip()
    if len(body) > 500:
        body = body[:500] + "…"
    base = f"groups.io HTTP {exc.response.status_code} from {exc.request.url}"
    return f"{base}: {body}" if body else base


class GroupsIoBackend:
    """Post delivery content to a groups.io group via the API."""

    def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
        api_key = config.get("api_key", "")
        if not api_key:
            return DeliveryResult(success=False, error="Groups.io API key not configured")

        group_name = config.get("group_name", "")
        headers = {"Authorization": f"Bearer {api_key}"}

        logger.info("groups.io send: group=%r subject=%r", group_name, subject)

        try:
            # /newdraft only accepts group + draft_type; subject/body are
            # silently ignored here and must be set via /updatedraft before
            # /postdraft will accept the draft.
            draft_resp = httpx.post(
                f"{BASE_URL}/newdraft",
                headers=headers,
                data={
                    "group_name": group_name,
                    "draft_type": "draft_type_post",
                },
                timeout=30,
            )
            draft_resp.raise_for_status()
            draft_id = draft_resp.json()["id"]

            # groups.io renders `body` as HTML — plain-text newlines collapse
            # to whitespace, so wrap the roster body in <pre> to preserve
            # newlines and the fixed-width table alignment.
            body_html = f"<pre>{html.escape(body)}</pre>"
            update_resp = httpx.post(
                f"{BASE_URL}/updatedraft",
                headers=headers,
                data={"draft_id": draft_id, "subject": subject, "body": body_html},
                timeout=30,
            )
            update_resp.raise_for_status()

            post_resp = httpx.post(
                f"{BASE_URL}/postdraft",
                headers=headers,
                data={"draft_id": draft_id},
                timeout=30,
            )
            post_resp.raise_for_status()
        except HTTPStatusError as exc:
            error = _format_http_error(exc)
            logger.warning("groups.io send failed: %s", error)
            return DeliveryResult(success=False, error=error)
        except Exception as exc:
            logger.warning("groups.io send failed: %s: %s", type(exc).__name__, exc)
            return DeliveryResult(success=False, error=f"{type(exc).__name__}: {exc}")

        logger.info("groups.io send ok: draft_id=%s", draft_id)
        return DeliveryResult(success=True, error=None)
