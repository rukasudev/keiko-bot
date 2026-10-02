from datetime import datetime
from typing import Any, Dict, List, Optional

from app import mongo_client
from app.data.util import parse_insert_timestamp


def find_reminder_by_value(value: str) -> dict:
    return mongo_client.audit.reminders.find_one({"value": value})

def find_reminder_by_id(reminder_id: Any) -> Optional[dict]:
    """A renewal reminder Keiko created, whether its id was stored as a number or as text."""
    return mongo_client.audit.reminders.find_one(_same_reminder_id(reminder_id))

def insert_reminder(reminder_id: str, title: str, value: str) -> None:
    data = {
        "reminder_id": reminder_id,
        "title": title,
        "value": value,
    }
    data = parse_insert_timestamp(data)
    return mongo_client.audit.reminders.insert_one(data)

def stamp_hub_confirmation(youtuber: str, moment: datetime) -> None:
    """Record when the hub last took the youtuber's subscription, clearing any lapse report."""
    return mongo_client.audit.reminders.update_many(
        {"title": "youtube_notification", "value": youtuber},
        {"$set": {"hub_confirmed_at": moment, "lapse_reported_at": None}},
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
