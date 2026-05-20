import smtplib
import ssl
from email.message import EmailMessage

from backend.integrations.delivery.backends.base import DeliveryBackend, DeliveryResult


class EmailBackend:
    """Send delivery content via SMTP email."""

    def send(self, subject: str, body: str, config: dict) -> DeliveryResult:
        host = config.get("smtp_host", "")
        if not host:
            return DeliveryResult(success=False, error="SMTP not configured")

        try:
            msg = EmailMessage()
            msg["Subject"] = subject
            msg["From"] = config.get("smtp_from_address", "")
            msg["To"] = config.get("to_address", "")
            msg.set_content(body)

            with smtplib.SMTP(host, config.get("smtp_port", 587)) as server:
                if config.get("smtp_use_tls", True):
                    server.starttls(context=ssl.create_default_context())
                username = config.get("smtp_username", "")
                if username:
                    server.login(username, config.get("smtp_password", ""))
                server.send_message(msg)

            return DeliveryResult(success=True, error=None)
        except Exception as exc:
            return DeliveryResult(success=False, error=str(exc))
