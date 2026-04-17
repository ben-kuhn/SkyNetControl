from sqlalchemy.orm import Session

from backend.modules.activities.models import Activity, ActivityTag


def get_or_create_tags(db: Session, tag_names: list[str]) -> list[ActivityTag]:
    if not tag_names:
        return []
    tags = []
    for name in tag_names:
        tag = db.query(ActivityTag).filter(ActivityTag.name == name).first()
        if tag is None:
            tag = ActivityTag(name=name)
            db.add(tag)
        tags.append(tag)
    db.flush()
    return tags


def create_activity(
    db: Session,
    title: str,
    description: str,
    instructions: str,
    tag_names: list[str] | None = None,
    is_default: bool = False,
) -> Activity:
    activity = Activity(
        title=title,
        description=description,
        instructions=instructions,
        is_default=is_default,
    )
    if tag_names:
        activity.tags = get_or_create_tags(db, tag_names)
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def get_activity(db: Session, activity_id: int) -> Activity | None:
    return db.get(Activity, activity_id)


def list_activities(db: Session) -> list[Activity]:
    return db.query(Activity).order_by(Activity.title).all()


def update_activity(
    db: Session,
    activity_id: int,
    title: str | None = None,
    description: str | None = None,
    instructions: str | None = None,
    tag_names: list[str] | None = None,
) -> Activity | None:
    activity = db.get(Activity, activity_id)
    if activity is None:
        return None

    if title is not None:
        activity.title = title
    if description is not None:
        activity.description = description
    if instructions is not None:
        activity.instructions = instructions
    if tag_names is not None:
        activity.tags = get_or_create_tags(db, tag_names)

    db.commit()
    db.refresh(activity)
    return activity


def delete_activity(db: Session, activity_id: int) -> bool:
    activity = db.get(Activity, activity_id)
    if activity is None:
        return False
    if activity.is_default:
        return False
    db.delete(activity)
    db.commit()
    return True
