from unittest.mock import patch, MagicMock

from backend.integrations.delivery.backends.groupsio import GroupsIoBackend
from backend.integrations.delivery.backends.base import DeliveryResult


def test_groupsio_backend_success():
    config = {
        "api_key": "test-key-123",
        "group_name": "w0ne-net",
    }

    mock_response_draft = MagicMock()
    mock_response_draft.status_code = 200
    mock_response_draft.json.return_value = {"id": 42, "group_id": 7}
    mock_response_draft.raise_for_status = MagicMock()

    mock_response_post = MagicMock()
    mock_response_post.status_code = 200
    mock_response_post.raise_for_status = MagicMock()

    with patch("backend.integrations.delivery.backends.groupsio.httpx") as mock_httpx:
        mock_httpx.post.side_effect = [mock_response_draft, mock_response_post]

        backend = GroupsIoBackend()
        result = backend.send("Test Subject", "Test Body", config)

    assert result.success is True
    assert result.error is None

    calls = mock_httpx.post.call_args_list
    assert len(calls) == 2
    assert "/newdraft" in calls[0].args[0]
    assert calls[0].kwargs["headers"]["Authorization"] == "Bearer test-key-123"
    assert calls[0].kwargs["data"]["draft_type"] == "draft_type_post"
    assert "/postdraft" in calls[1].args[0]
    assert calls[1].kwargs["data"] == {"draft_id": 42}


def test_groupsio_backend_draft_failure():
    config = {
        "api_key": "test-key-123",
        "group_name": "w0ne-net",
    }

    with patch("backend.integrations.delivery.backends.groupsio.httpx") as mock_httpx:
        mock_httpx.post.side_effect = Exception("API error")

        backend = GroupsIoBackend()
        result = backend.send("Test Subject", "Test Body", config)

    assert result.success is False
    assert "API error" in result.error


def test_groupsio_backend_no_api_key():
    config = {
        "api_key": "",
        "group_name": "w0ne-net",
    }

    backend = GroupsIoBackend()
    result = backend.send("Test Subject", "Test Body", config)

    assert result.success is False
    assert "not configured" in result.error.lower()
