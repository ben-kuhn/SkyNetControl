import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from unittest.mock import patch, MagicMock

from backend.auth.email import (
    notify_admins_callsign_change,
    notify_admins_new_registration,
    notify_user_approved,
    notify_user_callsign_approved,
    send_email,
)
from backend.auth.models import User
from backend.config_mgmt.smtp import SmtpConfig, upsert_smtp_config
from backend.db.base import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture
def smtp_db(db):
    """A db session pre-populated with valid SMTP config."""
    upsert_smtp_config(db, SmtpConfig(
        host="smtp.example.com",
        port=587,
        username="test@example.com",
        password="password",
        use_tls=True,
        from_address="skynet@example.com",
    ))
    return db


@pytest.mark.asyncio
async def test_send_email_success(smtp_db):
    with patch("backend.auth.email.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        await send_email(smtp_db, "recipient@example.com", "Test Subject", "Test body")

        mock_smtp.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_send_email_no_smtp_configured(db):
    # Empty DB — get_smtp_config returns None, should be a no-op
    with patch("backend.auth.email.smtplib.SMTP") as mock_smtp_cls:
        await send_email(db, "recipient@example.com", "Test Subject", "Test body")
        mock_smtp_cls.assert_not_called()


@pytest.mark.asyncio
async def test_send_email_failure_does_not_raise(smtp_db):
    with patch("backend.auth.email.smtplib.SMTP") as mock_smtp_cls:
        mock_smtp_cls.side_effect = Exception("Connection refused")

        # Should not raise
        await send_email(smtp_db, "recipient@example.com", "Test Subject", "Test body")


@pytest.mark.asyncio
async def test_notify_admins_new_registration(smtp_db):
    admin = User(callsign="W0NE", oidc_subject="g:1", name="Admin", is_admin=True, email="admin@example.com")
    new_user = User(callsign="W0ABC", oidc_subject="g:2", name="New User", is_pending=True)

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_admins_new_registration(smtp_db, [admin], new_user)
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        assert "W0ABC" in call_args[2]  # subject contains callsign
        assert "admin@example.com" == call_args[1]


@pytest.mark.asyncio
async def test_notify_admins_skips_admins_without_email(smtp_db):
    admin_no_email = User(callsign="W0NE", oidc_subject="g:1", name="Admin", is_admin=True, email=None)
    new_user = User(callsign="W0ABC", oidc_subject="g:2", name="New User", is_pending=True)

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_admins_new_registration(smtp_db, [admin_no_email], new_user)
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_notify_user_approved(smtp_db):
    user = User(callsign="W0ABC", oidc_subject="g:1", name="User",  email="user@example.com")

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_user_approved(smtp_db, user)
        mock_send.assert_called_once()
        assert "approved" in mock_send.call_args[0][2].lower()


@pytest.mark.asyncio
async def test_notify_user_approved_no_email(smtp_db):
    user = User(callsign="W0ABC", oidc_subject="g:1", name="User",  email=None)

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_user_approved(smtp_db, user)
        mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_notify_admins_callsign_change(smtp_db):
    admin = User(callsign="W0NE", oidc_subject="g:1", name="Admin", is_admin=True, email="admin@example.com")
    user = User(callsign="W0OLD", oidc_subject="g:2", name="User", )

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_admins_callsign_change(smtp_db, [admin], user, "W0NEW")
        mock_send.assert_called_once()
        subject = mock_send.call_args[0][2]
        assert "W0OLD" in subject
        assert "W0NEW" in subject


@pytest.mark.asyncio
async def test_notify_user_callsign_approved(smtp_db):
    user = User(callsign="W0NEW", oidc_subject="g:1", name="User",  email="user@example.com")

    with patch("backend.auth.email.send_email") as mock_send:
        await notify_user_callsign_approved(smtp_db, user, "W0OLD")
        mock_send.assert_called_once()
        body = mock_send.call_args[0][3]
        assert "W0NEW" in body
