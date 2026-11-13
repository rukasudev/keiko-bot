"""What Keiko set on the reminders API for a time, one document per thing scheduled."""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app import mongo_client
from app.data.util import parse_update_timestamp


def schedule_id(kind: str, *subject: Any) -> str:
    """The `_id` of the schedule of `kind` about `subject`, each part after the kind."""
    return ":".join([kind, *(str(part) for part in subject)])


def schedule_subject(schedule: Dict[str, Any]) -> Tuple[str, ...]:
    """What a schedule is about, read from its `_id` after the kind."""
    return tuple(str(schedule["_id"]).split(":")[1:])


def find_schedule(schedule_id: str) -> Optional[Dict[str, Any]]:
    return mongo_client.keiko.schedules.find_one({"_id": schedule_id})


def find_schedule_by_reminder(reminder_id: Any) -> Optional[Dict[str, Any]]:
    """The schedule a reminder of the reminders API belongs to, its id read as text."""
    return mongo_client.keiko.schedules.find_one({"reminder_id": str(reminder_id)})


def find_schedules_by_kind(kind: str) -> List[Dict[str, Any]]:
    return list(mongo_client.keiko.schedules.find({"kind": kind}))


def find_schedules_by_reminder(
    reminder_id: Any, kind: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Every schedule one reminder serves, only those of `kind` when given."""
    query: Dict[str, Any] = {"reminder_id": str(reminder_id)}
    if kind is not None:
        query["kind"] = kind
    return list(mongo_client.keiko.schedules.find(query))


def find_schedules_missing_reminder(kind: str, limit: int) -> List[Dict[str, Any]]:
    """Schedules of `kind` whose reminder could not be created yet, the soonest due first."""
    cursor = mongo_client.keiko.schedules.find({"reminder_id": None, "kind": kind})
    return list(cursor.sort("due_at", 1).limit(limit))


def upsert_schedule(
    schedule_id: str,
    kind: str,
    reminder_id: Any,
    due_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Save a schedule; a `due_at` or `expires_at` left out keeps the stored one."""
    fields: Dict[str, Any] = {
        "kind": kind,
        "reminder_id": None if reminder_id is None else str(reminder_id),
    }
    if due_at is not None:
        fields["due_at"] = due_at
    if expires_at is not None:
        fields["expires_at"] = expires_at
    update = parse_update_timestamp(fields)
    mongo_client.keiko.schedules.update_one(
        {"_id": schedule_id},
        {"$set": update, "$setOnInsert": {"created_at": update["updated_at"]}},
        upsert=True,
    )
    return {"_id": schedule_id, **update}


def set_schedule_due(schedule_id: str, due_at: datetime) -> int:
    """Record when the reminder of a saved schedule fires next."""
    result = mongo_client.keiko.schedules.update_one(
        {"_id": schedule_id},
        {"$set": parse_update_timestamp({"due_at": due_at})},
    )
    return result.modified_count


def extend_schedule_expiry(schedule_id: str, expires_at: datetime) -> int:
    """Move the expiry of a schedule that has one."""
    result = mongo_client.keiko.schedules.update_one(
        {"_id": schedule_id, "expires_at": {"$exists": True}},
        {"$set": {"expires_at": expires_at}},
    )
    return result.modified_count


def delete_schedule_without_reminder(schedule_id: str) -> int:
    """Delete a schedule while it still has no reminder."""
    result = mongo_client.keiko.schedules.delete_one(
        {"_id": schedule_id, "reminder_id": None}
    )
    return result.deleted_count


def delete_schedule(schedule_id: str) -> int:
    return mongo_client.keiko.schedules.delete_one({"_id": schedule_id}).deleted_count
