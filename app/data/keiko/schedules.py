"""keiko.schedules: what Keiko set on the reminders API for a time, one document per
thing scheduled."""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.data.collection import Document, Repository
from app.data.util import parse_update_timestamp


def schedule_id(kind: str, *subject: Any) -> str:
    """The `_id` of the schedule of `kind` about `subject`, each part after the kind."""
    return ":".join([kind, *(str(part) for part in subject)])


def schedule_subject(schedule: Dict[str, Any]) -> Tuple[str, ...]:
    """What a schedule is about, read from its `_id` after the kind."""
    return tuple(str(schedule["_id"]).split(":")[1:])


class Schedules(Repository):
    """Everything Keiko scheduled on the reminders API."""

    async def find_schedule(self, schedule_id: str) -> Optional[Document]:
        """The schedule saved under `schedule_id`."""
        return await self._collection.find_one({"_id": schedule_id})

    async def find_schedule_by_reminder(self, reminder_id: Any) -> Optional[Document]:
        """The schedule a reminder of the reminders API belongs to, its id read as
        text."""
        return await self._collection.find_one({"reminder_id": str(reminder_id)})

    async def find_schedules_by_kind(self, kind: str) -> List[Document]:
        """Every schedule of `kind`."""
        return await self._collection.find({"kind": kind}).to_list()

    async def find_schedules_by_reminder(
        self, reminder_id: Any, kind: Optional[str] = None
    ) -> List[Document]:
        """Every schedule one reminder serves, only those of `kind` when given."""
        query: Dict[str, Any] = {"reminder_id": str(reminder_id)}
        if kind is not None:
            query["kind"] = kind
        return await self._collection.find(query).to_list()

    async def find_schedules_missing_reminder(
        self, kind: str, limit: int
    ) -> List[Document]:
        """Schedules of `kind` whose reminder could not be created yet, the soonest due
        first."""
        cursor = self._collection.find({"reminder_id": None, "kind": kind})
        return await cursor.sort("due_at", 1).limit(limit).to_list()

    async def upsert_schedule(
        self,
        schedule_id: str,
        kind: str,
        reminder_id: Any,
        due_at: Optional[datetime] = None,
        expires_at: Optional[datetime] = None,
    ) -> Document:
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
        await self._collection.update_one(
            {"_id": schedule_id},
            {"$set": update, "$setOnInsert": {"created_at": update["updated_at"]}},
            upsert=True,
        )
        return {"_id": schedule_id, **update}

    async def set_schedule_due(self, schedule_id: str, due_at: datetime) -> int:
        """Record when the reminder of a saved schedule fires next."""
        result = await self._collection.update_one(
            {"_id": schedule_id},
            {"$set": parse_update_timestamp({"due_at": due_at})},
        )
        return result.modified_count

    async def extend_schedule_expiry(
        self, schedule_id: str, expires_at: datetime
    ) -> int:
        """Move the expiry of a schedule that has one."""
        result = await self._collection.update_one(
            {"_id": schedule_id, "expires_at": {"$exists": True}},
            {"$set": {"expires_at": expires_at}},
        )
        return result.modified_count

    async def delete_schedule_without_reminder(self, schedule_id: str) -> int:
        """Delete a schedule while it still has no reminder."""
        result = await self._collection.delete_one(
            {"_id": schedule_id, "reminder_id": None}
        )
        return result.deleted_count

    async def delete_schedule(self, schedule_id: str) -> int:
        """Delete the schedule saved under `schedule_id`."""
        result = await self._collection.delete_one({"_id": schedule_id})
        return result.deleted_count
