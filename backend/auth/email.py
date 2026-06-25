import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy.orm import Session

from backend.auth.models import User
from backend.config import settings  # module singleton — only app_base_url is read
from backend.config_mgmt.smtp import SmtpConfig, get_smtp_config

logger = logging.getLogger(__name__)


async def send_email(
    db: Session,
    to: str,
    subject: str,
    body: str,
    *,
    smtp: SmtpConfig | None = None,
) -> None:
    """Send an email via SMTP. Fire-and-forget — never raises.

    Callers that send many messages in a single operation (admin
    notifications looping over multiple recipients) should fetch the
    SMTP config once and pass it via `smtp=` to avoid the per-send
    DB hit. The identity-map cache makes the repeated fetch cheap
    today, but it's fragile under future session-scoping changes.
    """
    if smtp is None:
        smtp = get_smtp_config(db)
    if smtp is None:
        logger.debug("SMTP not configured, skipping email to %s", to)
        return
    try:
        await asyncio.to_thread(_send_email_sync, smtp, to, subject, body)
    except Exception:
        logger.exception("Failed to send email to %s", to)


def _send_email_sync(smtp: SmtpConfig, to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp.from_address
    msg["To"] = to
    msg.set_content(body)

    with smtplib.SMTP(smtp.host, smtp.port) as server:
        if smtp.use_tls:
            server.starttls(context=ssl.create_default_context())
        if smtp.username:
            server.login(smtp.username, smtp.password)
        server.send_message(msg)


async def notify_admins_new_registration(db: Session, admins: list[User], new_user: User) -> None:
    smtp = get_smtp_config(db)
    if smtp is None:
        return
    base_url = settings.app_base_url
    for admin in admins:
        if admin.email:
            await send_email(
                db,
                admin.email,
                f"[SkyNetControl] New registration: {new_user.callsign}",
                f"{new_user.name} has registered as {new_user.callsign} and is awaiting approval. "
                f"Review pending users at {base_url}.",
                smtp=smtp,
            )


async def notify_admins_callsign_change(db: Session, admins: list[User], user: User, new_callsign: str) -> None:
    smtp = get_smtp_config(db)
    if smtp is None:
        return
    base_url = settings.app_base_url
    for admin in admins:
        if admin.email:
            await send_email(
                db,
                admin.email,
                f"[SkyNetControl] Callsign change request: {user.callsign} -> {new_callsign}",
                f"{user.name} ({user.callsign}) has requested a callsign change to {new_callsign}. "
                f"Review at {base_url}.",
                smtp=smtp,
            )


async def notify_user_approved(db: Session, user: User) -> None:
    if not user.email:
        return
    base_url = settings.app_base_url
    await send_email(
        db,
        user.email,
        "[SkyNetControl] Your account has been approved",
        f"Your account ({user.callsign}) has been approved. "
        f"You can now access SkyNetControl at {base_url}.",
    )


async def notify_user_callsign_approved(db: Session, user: User, old_callsign: str) -> None:
    if not user.email:
        return
    base_url = settings.app_base_url
    await send_email(
        db,
        user.email,
        "[SkyNetControl] Your callsign change has been approved",
        f"Your callsign has been changed from {old_callsign} to {user.callsign}. "
        f"Access SkyNetControl at {base_url}.",
    )
