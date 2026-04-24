import pytest
from unittest.mock import patch, MagicMock

from backend.auth.email import (
    notify_admins_callsign_change,
    notify_admins_new_registration,
    notify_user_approved,
    notify_user_callsign_approved,
    send_email,
)
from backend.auth.models import User, UserRole
from backend.config import Settings, ProviderSettings, SmtpSettings


@pytest.fixture
def smtp_settings():
    return Settings(
        database_url="sqlite:///",
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
        app_base_url="http://localhost:8000",
        smtp=SmtpSettings(
            host="smtp.example.com",
            port=587,
            username="test@example.com",
            password="password",
            use_tls=True,
            from_address="skynet@example.com",
        ),
    )


@pytest.fixture
def no_smtp_settings():
    return Settings(
        database_url="sqlite:///",
        auth_google=ProviderSettings(enabled=True, client_id="gid", client_secret="gsec"),
    )


@pytest.mark.asyncio
async def test_send_email_success(smtp_settings):
    with patch("backend.auth.email.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        await send_email("recipient@example.com", "Test Subject", "Test body", smtp_settings)

        mock_smtp.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_send_email_no_smtp_configured(no_smtp_settings):
    # Should not raise — just silently skip
    await send_email("recipient@example.com", "Test Subject", "Test body", no_smtp_settings)


@pytest.mark.asyncio
async def test_send_email_failure_does_not_raise(smtp_settings):
    with patch("backend.auth.email.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.side_effect = Exception("Connection refused")

        # Should not raise
        await send_email("recipient@example.com", "Test Subject", "Test body", smtp_settings)


@pytest.mark.asyncio
async def test_notify_admins_new_registration(smtp_settings):
    admin = User(callsign="W0NE", oidc_subject="g:1", name="Admin", role=UserRole.ADMIN, email="admin@example.com")
    new_user = User(callsign="W0ABC", oidc_subject="g:2", name="New User", role=UserRole.PENDING)

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_admins_new_registration([admin], new_user, smtp_settings)
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert "W0ABC" in call_args[0][1]  # subject contains callsign
        assert "admin@example.com" == call_args[0][0]


@pytest.mark.asyncio
async def test_notify_admins_skips_admins_without_email(smtp_settings):
    admin_no_email = User(callsign="W0NE", oidc_subject="g:1", name="Admin", role=UserRole.ADMIN, email=None)
    new_user = User(callsign="W0ABC", oidc_subject="g:2", name="New User", role=UserRole.PENDING)

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_admins_new_registration([admin_no_email], new_user, smtp_settings)
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_notify_user_approved(smtp_settings):
    user = User(callsign="W0ABC", oidc_subject="g:1", name="User", role=UserRole.VIEWER, email="user@example.com")

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_user_approved(user, smtp_settings)
        mock_send.assert_called_once()
        assert "approved" in mock_send.call_args[0][1].lower()


@pytest.mark.asyncio
async def test_notify_user_approved_no_email(smtp_settings):
    user = User(callsign="W0ABC", oidc_subject="g:1", name="User", role=UserRole.VIEWER, email=None)

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_user_approved(user, smtp_settings)
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_notify_admins_callsign_change(smtp_settings):
    admin = User(callsign="W0NE", oidc_subject="g:1", name="Admin", role=UserRole.ADMIN, email="admin@example.com")
    user = User(callsign="W0OLD", oidc_subject="g:2", name="User", role=UserRole.VIEWER)

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_admins_callsign_change([admin], user, "W0NEW", smtp_settings)
        mock_send.assert_called_once()
        subject = mock_send.call_args[0][1]
        assert "W0OLD" in subject
        assert "W0NEW" in subject


@pytest.mark.asyncio
async def test_notify_user_callsign_approved(smtp_settings):
    user = User(callsign="W0NEW", oidc_subject="g:1", name="User", role=UserRole.VIEWER, email="user@example.com")

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_user_callsign_approved(user, "W0OLD", smtp_settings)
        mock_send.assert_called_once()
        body = mock_send.call_args[0][2]
        assert "W0NEW" in body
