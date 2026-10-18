from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app import mongo_client
from app.constants import Commands
from app.data.util import parse_insert_timestamp


def find_reminder_by_value(value: str) -> dict:
    return mongo_client.audit.reminders.find_one({"value": value})

def find_reminder_by_id(reminder_id: Any) -> Optional[dict]:
    """A renewal reminder Keiko created, whether its id was stored as a number or as text."""
    return mongo_client.audit.reminders.find_one(_same_reminder_id(reminder_id))

def insert_renewal_reminder(reminder_id: Any, youtuber: str) -> None:
    """The row of a youtuber's renewal reminder, kept until nothing has renewed it for a while."""
    data = parse_insert_timestamp({
        "reminder_id": reminder_id,
        "title": "youtube_notification",
        "value": youtuber,
    })

    data["expires_at"] = _kept_until(data["created_at"])
    return mongo_client.audit.reminders.insert_one(data)

def stamp_hub_confirmation(youtuber: str, moment: datetime) -> None:
    """Record when the hub last took the youtuber's subscription, clearing any lapse
    report, and move the expiry of the rows that have one."""
    renewals = {"title": "youtube_notification", "value": youtuber}

    mongo_client.audit.reminders.update_many(
        renewals, {"$set": {"hub_confirmed_at": moment, "lapse_reported_at": None}}
    )

    return mongo_client.audit.reminders.update_many(
        {**renewals, "expires_at": {"$exists": True}},
        {"$set": {"expires_at": _kept_until(moment)}},
    )

def mark_lapse_reported(youtuber: str, moment: datetime) -> None:
    """Record that the error channel heard the youtuber's subscription is about to lapse."""
    return mongo_client.audit.reminders.update_many(
        {"title": "youtube_notification", "value": youtuber},
        {"$set": {"lapse_reported_at": moment}},
    )

def delete_reminder_by_id(reminder_id: Any) -> None:
    return mongo_client.audit.reminders.delete_one(_same_reminder_id(reminder_id))

def _same_reminder_id(reminder_id: Any) -> Dict[str, Dict[str, List[Any]]]:
    text = str(reminder_id)
    forms: List[Any] = [text, int(text)] if text.isdecimal() else [text]
    return {"reminder_id": {"$in": forms}}

def _kept_until(renewed_at: datetime) -> datetime:
    return renewed_at + timedelta(seconds=Commands.YOUTUBE_RENEWAL_ROW_SECONDS)
