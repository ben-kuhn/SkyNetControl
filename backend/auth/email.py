import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from backend.auth.models import User
from backend.config import Settings

logger = logging.getLogger(__name__)


async def send_email(to: str, subject: str, body: str, settings: Settings) -> None:
    """Send an email via SMTP. Fire-and-forget — never raises."""
    if not settings.smtp.host:
        logger.debug("SMTP not configured, skipping email to %s", to)
        return

    try:
        await asyncio.to_thread(_send_email_sync, to, subject, body, settings)
    except Exception:
        logger.exception("Failed to send email to %s", to)


def _send_email_sync(to: str, subject: str, body: str, settings: Settings) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp.from_address
    msg["To"] = to
    msg.set_content(body)

    with smtplib.SMTP(settings.smtp.host, settings.smtp.port) as server:
        if settings.smtp.use_tls:
            server.starttls(context=ssl.create_default_context())
        if settings.smtp.username:
            server.login(settings.smtp.username, settings.smtp.password)
        server.send_message(msg)


async def notify_admins_new_registration(admins: list[User], new_user: User, settings: Settings) -> None:
    """Notify admins that a new user has registered."""
    for admin in admins:
        if admin.email:
            await send_email(
                admin.email,
                f"[SkyNetControl] New registration: {new_user.callsign}",
                f"{new_user.name} has registered as {new_user.callsign} and is awaiting approval. "
                f"Review pending users at {settings.app_base_url}.",
                settings,
            )


async def notify_admins_callsign_change(admins: list[User], user: User, new_callsign: str, settings: Settings) -> None:
    """Notify admins that a user has requested a callsign change."""
    for admin in admins:
        if admin.email:
            await send_email(
                admin.email,
                f"[SkyNetControl] Callsign change request: {user.callsign} -> {new_callsign}",
                f"{user.name} ({user.callsign}) has requested a callsign change to {new_callsign}. "
                f"Review at {settings.app_base_url}.",
                settings,
            )


async def notify_user_approved(user: User, settings: Settings) -> None:
    """Notify a user that their account has been approved."""
    if not user.email:
        return
    await send_email(
        user.email,
        "[SkyNetControl] Your account has been approved",
        f"Your account ({user.callsign}) has been approved as {user.role.value}. "
        f"You can now access SkyNetControl at {settings.app_base_url}.",
        settings,
    )


async def notify_user_callsign_approved(user: User, old_callsign: str, settings: Settings) -> None:
    """Notify a user that their callsign change has been approved."""
    if not user.email:
        return
    await send_email(
        user.email,
        "[SkyNetControl] Your callsign change has been approved",
        f"Your callsign has been changed from {old_callsign} to {user.callsign}. "
        f"Access SkyNetControl at {settings.app_base_url}.",
        settings,
    )
