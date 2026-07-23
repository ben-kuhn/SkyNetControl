"""Event-level authorization. CONTROL = owner / co-operator / admin. READ = CONTROL,
or a public event for any authenticated user, or anonymous with a valid public_token."""
from __future__ import annotations

import enum
import secrets
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from backend.auth.dependencies import get_db_session, get_optional_user
from backend.auth.models import User
from backend.modules.events.models import Event, EventOperator


class EventRole(str, enum.Enum):
    READ = "read"
    CONTROL = "control"


@dataclass
class EventContext:
    user: User | None
    event: Event
    is_control: bool


def event_has_control(db: Session, user: User | None, event: Event) -> bool:
    if user is None:
        return False
    if user.is_admin or event.created_by == user.callsign:
        return True
    # EventOperator has an integer PK + a (event_id, callsign) unique constraint — query it.
    return (
        db.query(EventOperator)
        .filter(EventOperator.event_id == event.id, EventOperator.callsign == user.callsign)
        .first()
        is not None
    )


def require_event_role(min_role: EventRole) -> Callable:
    def dep(
        event_id: int = Path(...),
        token: str | None = Query(default=None),
        user: User | None = Depends(get_optional_user),
        db: Session = Depends(get_db_session),
    ) -> EventContext:
        event = db.get(Event, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail="Event not found")
        control = event_has_control(db, user, event)

        if min_role == EventRole.CONTROL:
            if user is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if not control:
                raise HTTPException(status_code=403, detail="Not authorized for this event")
            return EventContext(user=user, event=event, is_control=True)

        # READ
        if control:
            return EventContext(user=user, event=event, is_control=True)
        if event.visibility == "public":
            if user is not None:
                return EventContext(user=user, event=event, is_control=False)
            # Compare UTF-8 bytes: compare_digest raises TypeError on non-ASCII str,
            # which would 500 (and leak a public-event existence oracle) on a bad token.
            if token and secrets.compare_digest(token.encode("utf-8"), event.public_token.encode("utf-8")):
                return EventContext(user=None, event=event, is_control=False)
            # Anonymous with wrong/absent token: 404 (no existence signal)
            raise HTTPException(status_code=404, detail="Event not found")
        # Private event: authenticated users get 403; anonymous get 404 (no existence signal)
        if user is not None:
            raise HTTPException(status_code=403, detail="Not authorized for this event")
        raise HTTPException(status_code=404, detail="Event not found")

    return dep
