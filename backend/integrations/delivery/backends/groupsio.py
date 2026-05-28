import httpx

from backend.integrations.delivery.backends.base import DeliveryResult

BASE_URL = "https://groups.io/api/v1"


class GroupsIoBackend:
    """Post delivery content to a groups.io group via the API."""

    def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
        api_key = config.get("api_key", "")
        if not api_key:
            return DeliveryResult(success=False, error="Groups.io API key not configured")

        group_name = config.get("group_name", "")
        headers = {"Authorization": f"Bearer {api_key}"}

        try:
            draft_resp = httpx.post(
                f"{BASE_URL}/newdraft",
                headers=headers,
                data={"group_name": group_name, "subject": subject, "body": body},
                timeout=30,
            )
            draft_resp.raise_for_status()
            draft_data = draft_resp.json()
            draft_id = draft_data["draft_id"]
            group_id = draft_data["group_id"]

            post_resp = httpx.post(
                f"{BASE_URL}/postdraft",
                headers=headers,
                data={"draft_id": draft_id, "group_id": group_id},
                timeout=30,
            )
            post_resp.raise_for_status()

            return DeliveryResult(success=True, error=None)
        except Exception as exc:
            return DeliveryResult(success=False, error=str(exc))
