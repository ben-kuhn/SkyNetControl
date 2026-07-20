from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth.dependencies import NetContext, get_db_session, require_net_role
from backend.integrations.delivery.service import get_delivery_status, retry_failed
from backend.modules.nets.models import NetRole

delivery_router = APIRouter()


def _verify_content_belongs_to_net(
    db: Session, content_type: str, content_id: int, net_id: int
) -> None:
    """Resolve a delivery log target (`roster`/`reminder`) to its owning net and
    enforce isolation. 404 conflates "not found" with "cross-net" so net
    existence isn't probeable from outside the net.
    """
    from backend.modules.checkins.service import get_net_id_for_session
    from backend.modules.reminders.models import ReminderLog
    from backend.modules.roster.models import RosterLog
    from backend.modules.schedule.models import NetSession

    if content_type == "event_message":
        from backend.modules.events.models import Event, EventMessage

        msg = db.get(EventMessage, content_id)
        if msg is None:
            raise HTTPException(status_code=404, detail="Not found")
        event = db.get(Event, msg.event_id)
        if event is None or event.net_id != net_id:
            raise HTTPException(status_code=404, detail="Not found")
        return

    if content_type == "roster":
        log = db.get(RosterLog, content_id)
    elif content_type == "reminder":
        log = db.get(ReminderLog, content_id)
    else:
        raise HTTPException(status_code=404, detail="Unknown content type")

    if log is None:
        raise HTTPException(status_code=404, detail="Not found")
    sess = db.get(NetSession, log.session_id)
    if sess is None or get_net_id_for_session(db, sess) != net_id:
        raise HTTPException(status_code=404, detail="Not found")


@delivery_router.get("/{content_type}/{content_id}")
def list_delivery_attempts(
    content_type: str,
    content_id: int,
    db: Session = Depends(get_db_session),
    ctx: NetContext = Depends(require_net_role(NetRole.VIEWER)),
):
    _verify_content_belongs_to_net(db, content_type, content_id, ctx.net.id)
    logs = get_delivery_status(db, content_type, content_id)
    return [
        {
            "id": log.id,
            "backend": log.backend,
            "status": log.status.value,
            "error_message": log.error_message,
            "sent_at": log.sent_at.isoformat() if log.sent_at else None,
            "created_at": log.created_at.isoformat(),
        }
        for log in logs
    ]


@delivery_router.post("/{content_type}/{content_id}/retry")
def retry_delivery(
    content_type: str,
    content_id: int,
    db: Session = Depends(get_db_session),
    ctx: NetContext = Depends(require_net_role(NetRole.NET_CONTROL)),
):
    _verify_content_belongs_to_net(db, content_type, content_id, ctx.net.id)

    if content_type == "event_message":
        # Event messages must retry winlink-only, addressed to the original
        # composed recipient (to_address on the EventMessage row).
        # For form messages, rebuild the attachment so the retry is byte-identical
        # to the original send — the whole point of persisting EventMessageForm.
        from backend.modules.events.models import EventMessage
        from backend.modules.events.message_service import _build_context

        msg = db.get(EventMessage, content_id)
        if msg is None:
            raise HTTPException(status_code=404, detail="Not found")

        config_overrides: dict = {"target_address": msg.to_address}
        subject = msg.subject
        body = msg.body

        net_id = ctx.net.id
        rec = msg.form_record  # EventMessageForm; None for plain messages
        if rec is not None:
            from backend.modules.forms.builder import build_form_message, FormBuildError
            try:
                build_ctx = _build_context(db, net_id, rec.datetime_stamp)
                composed = build_form_message(rec.template_path, rec.variables, build_ctx)
            except (FormBuildError, ValueError) as exc:
                # A form message must never silently degrade to a plain-text retry —
                # that reintroduces the exact failure (form sent as text, reported
                # delivered) this rebuild exists to prevent. Fail the retry loudly so
                # the operator can fix the template/library and try again.
                raise HTTPException(
                    status_code=422,
                    detail=f"Cannot rebuild form attachment for retry: {exc}",
                )
            config_overrides["attachments"] = [composed.attachment]
            subject = composed.subject
            body = composed.body

        success = retry_failed(
            db, content_type, content_id, net_id,
            backends=["winlink"],
            config_overrides=config_overrides,
            subject_override=subject,
            body_override=body,
        )
    else:
        success = retry_failed(db, content_type, content_id, ctx.net.id)

    return {"retried": success}
