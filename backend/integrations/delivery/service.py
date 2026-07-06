import json
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.config_mgmt.service import get_config_value
from backend.config_mgmt.smtp import get_smtp_config
from backend.integrations.delivery.backends import get_backend
from backend.integrations.delivery.models import DeliveryLog, DeliveryStatus
from backend.modules.nets.config_service import get_net_config

logger = logging.getLogger(__name__)


def _build_config(db: Session, backend_name: str, net_id: int) -> dict:
    """Build config dict for a backend from per-net NetConfig + global AppConfig + Settings."""
    config: dict = {}

    if backend_name == "email":
        config["to_address"] = get_net_config(db, net_id, "delivery.email.to_address", "")
        smtp = get_smtp_config(db)
        if smtp is not None:
            config["smtp_host"] = smtp.host
            config["smtp_port"] = smtp.port
            config["smtp_username"] = smtp.username
            config["smtp_password"] = smtp.password
            config["smtp_use_tls"] = smtp.use_tls
            config["smtp_from_address"] = smtp.from_address
        # If SMTP isn't configured, leave the smtp_* keys absent — the
        # EmailBackend.send already short-circuits on a missing host.

    elif backend_name == "groupsio":
        config["api_key"] = get_config_value(db, "delivery.groupsio.api_key", "")
        config["group_name"] = get_net_config(db, net_id, "delivery.groupsio.group_name", "")

    elif backend_name == "winlink":
        config["target_address"] = get_net_config(db, net_id, "delivery.winlink.target_address", "")
        config["mailbox_path"] = get_net_config(db, net_id, "pat_mailbox_path", "")
        net_address = get_net_config(db, net_id, "net_address", "")
        config["callsign"] = net_address.split("@")[0].upper() if "@" in net_address else net_address.upper()

    return config


def dispatch_delivery(
    db: Session,
    content_type: str,
    content_id: int,
    subject: str,
    body: str,
    net_id: int,
) -> bool:
    """Dispatch content to all enabled delivery backends.

    Returns True if at least one backend succeeds.
    """
    backends_json = get_net_config(db, net_id, "delivery.backends", "[]")
    backend_names = json.loads(backends_json)

    if not backend_names:
        logger.info("No delivery backends configured")
        return False

    any_success = False

    for name in backend_names:
        config = _build_config(db, name, net_id)
        # UNIQUE(content_type, content_id, backend) constrains delivery_logs
        # to one row per backend per piece of content — that row represents
        # the most recent attempt's state. On re-dispatch (e.g. resend_roster)
        # the row already exists, so reset it in place instead of inserting.
        log = (
            db.query(DeliveryLog)
            .filter_by(content_type=content_type, content_id=content_id, backend=name)
            .one_or_none()
        )
        if log is None:
            log = DeliveryLog(
                content_type=content_type,
                content_id=content_id,
                backend=name,
                status=DeliveryStatus.PENDING,
                created_at=datetime.now(tz=timezone.utc),
            )
            db.add(log)
        else:
            log.status = DeliveryStatus.PENDING
            log.error_message = None
            log.sent_at = None
            log.created_at = datetime.now(tz=timezone.utc)
        db.flush()

        try:
            backend = get_backend(name)
            result = backend.send(subject, body, config)
        except KeyError:
            log.status = DeliveryStatus.FAILED
            log.error_message = f"Unknown backend: {name}"
            db.commit()
            continue

        if result.success:
            log.status = DeliveryStatus.SENT
            log.sent_at = datetime.now(tz=timezone.utc)
            any_success = True
        else:
            log.status = DeliveryStatus.FAILED
            log.error_message = result.error
            logger.warning(
                "Delivery failed (%s %s/%s): %s",
                name, content_type, content_id, result.error,
            )

        db.commit()

    return any_success


def get_last_attempt_errors(db: Session, content_type: str, content_id: int) -> list[str]:
    """Return error messages from the most recent failed attempts per backend.

    Used so 'send failed' API responses can surface the actual backend error
    (e.g. groups.io's response body) instead of a generic message.
    """
    logs = (
        db.query(DeliveryLog)
        .filter_by(content_type=content_type, content_id=content_id, status=DeliveryStatus.FAILED)
        .order_by(DeliveryLog.created_at.desc())
        .all()
    )
    seen: set[str] = set()
    errors: list[str] = []
    for log in logs:
        if log.backend in seen:
            continue
        seen.add(log.backend)
        if log.error_message:
            errors.append(f"{log.backend}: {log.error_message}")
    return errors


def _lookup_content(db: Session, content_type: str, content_id: int) -> tuple[str, str]:
    """Look up the original subject and body from the source log."""
    if content_type == "reminder":
        from backend.modules.reminders.models import ReminderLog

        log = db.get(ReminderLog, content_id)
        if log:
            return log.content_subject, log.content_body
    elif content_type == "roster":
        from backend.modules.roster.models import RosterLog
        from backend.modules.roster.service import assemble_roster

        log = db.get(RosterLog, content_id)
        if log:
            body = assemble_roster(db, content_id) or ""
            return log.content_subject, body
    return "", ""


def retry_failed(db: Session, content_type: str, content_id: int, net_id: int) -> bool:
    """Retry only failed delivery attempts for a piece of content."""
    failed_logs = (
        db.query(DeliveryLog)
        .filter_by(content_type=content_type, content_id=content_id, status=DeliveryStatus.FAILED)
        .all()
    )

    if not failed_logs:
        return False

    subject, body = _lookup_content(db, content_type, content_id)

    any_success = False
    for log in failed_logs:
        config = _build_config(db, log.backend, net_id)
        try:
            backend = get_backend(log.backend)
            result = backend.send(subject, body, config)
        except KeyError:
            continue

        if result.success:
            log.status = DeliveryStatus.SENT
            log.sent_at = datetime.now(tz=timezone.utc)
            log.error_message = None
            any_success = True
        else:
            log.error_message = result.error

        db.commit()

    return any_success


def get_delivery_status(db: Session, content_type: str, content_id: int) -> list[DeliveryLog]:
    """Get all delivery log entries for a piece of content."""
    return (
        db.query(DeliveryLog)
        .filter_by(content_type=content_type, content_id=content_id)
        .order_by(DeliveryLog.created_at)
        .all()
    )
