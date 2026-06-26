from sqlalchemy.orm import Session

from backend.modules.activities.models import Activity, ActivityTag


def _clear_default(db: Session, net_id: int) -> None:
    """Clear is_default on all existing activities of *net_id*."""
    db.query(Activity).filter(
        Activity.net_id == net_id,
        Activity.is_default.is_(True),
    ).update({"is_default": False})


def get_or_create_tags(db: Session, net_id: int, tag_names: list[str]) -> list[ActivityTag]:
    if not tag_names:
        return []
    tags = []
    for name in tag_names:
        tag = db.query(ActivityTag).filter(ActivityTag.net_id == net_id, ActivityTag.name == name).first()
        if tag is None:
            tag = ActivityTag(net_id=net_id, name=name)
            db.add(tag)
        tags.append(tag)
    db.flush()
    return tags


def create_activity(
    db: Session,
    net_id: int,
    title: str,
    description: str,
    instructions: str,
    tag_names: list[str] | None = None,
    is_default: bool = False,
) -> Activity:
    if is_default:
        _clear_default(db, net_id)
    activity = Activity(
        net_id=net_id,
        title=title,
        description=description,
        instructions=instructions,
        is_default=is_default,
    )
    if tag_names:
        activity.tags = get_or_create_tags(db, net_id, tag_names)
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def get_activity(db: Session, activity_id: int, net_id: int | None = None) -> Activity | None:
    """Return an Activity by id, optionally verifying it belongs to *net_id*."""
    activity = db.get(Activity, activity_id)
    if activity is None:
        return None
    if net_id is not None and activity.net_id != net_id:
        return None
    return activity


def list_activities(db: Session, net_id: int) -> list[Activity]:
    return db.query(Activity).filter(Activity.net_id == net_id).order_by(Activity.title).all()


def list_tags(db: Session, net_id: int) -> list[ActivityTag]:
    return db.query(ActivityTag).filter(ActivityTag.net_id == net_id).order_by(ActivityTag.name).all()


def update_activity(
    db: Session,
    activity_id: int,
    net_id: int | None = None,
    title: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    tag_names: list[str] | None = None,
    is_default: bool | None = None,
) -> Activity | None:
    activity = get_activity(db, activity_id, net_id=net_id)
    if activity is None:
        return None

    if title is not None:
        activity.title = title
    if description is not None:
        activity.description = description
    if instructions is not None:
        activity.instructions = instructions
    if tag_names is not None:
        activity.tags = get_or_create_tags(db, activity.net_id, tag_names)
    if is_default is not None:
        if is_default and not activity.is_default:
            _clear_default(db, activity.net_id)
        activity.is_default = is_default

    db.commit()
    db.refresh(activity)
    return activity


def delete_activity(db: Session, activity_id: int, net_id: int | None = None) -> bool:
    activity = get_activity(db, activity_id, net_id=net_id)
    if activity is None:
        return False
    if activity.is_default:
        return False
    db.delete(activity)
    db.commit()
    return True
