from unittest.mock import patch, MagicMock

from backend.integrations.delivery.backends.email import EmailBackend
from backend.integrations.delivery.backends.base import DeliveryResult


def test_email_backend_success():
    config = {
        "to_address": "net@example.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "user",
        "smtp_password": "pass",
        "smtp_use_tls": True,
        "smtp_from_address": "skynet@example.com",
    }
    with patch("backend.integrations.delivery.backends.email.smtplib.SMTP") as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

        backend = EmailBackend()
        result = backend.send("Test Subject", "Test Body", config)

    assert result.success is True
    assert result.error is None
    mock_server.send_message.assert_called_once()


def test_email_backend_failure():
    config = {
        "to_address": "net@example.com",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_use_tls": False,
        "smtp_from_address": "skynet@example.com",
    }
    with patch("backend.integrations.delivery.backends.email.smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__ = MagicMock(side_effect=ConnectionRefusedError("Connection refused"))

        backend = EmailBackend()
        result = backend.send("Test Subject", "Test Body", config)

    assert result.success is False
    assert "Connection refused" in result.error


def test_email_backend_no_host():
    config = {
        "to_address": "net@example.com",
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_username": "",
        "smtp_password": "",
        "smtp_use_tls": False,
        "smtp_from_address": "",
    }
    backend = EmailBackend()
    result = backend.send("Test Subject", "Test Body", config)

    assert result.success is False
    assert "not configured" in result.error.lower()
