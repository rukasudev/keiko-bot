from app import mongo_client
from app.data.util import parse_insert_timestamp


def find_reminder_by_value(value: str) -> dict:
    return mongo_client.audit.reminders.find_one({"value": value})

def insert_reminder(reminder_id: str, title: str, value: str) -> None:
    data = {
        "reminder_id": reminder_id,
        "title": title,
        "value": value,
    }
    data = parse_insert_timestamp(data)
    return mongo_client.audit.reminders.insert_one(data)

def delete_reminder_by_id(reminder_id: str) -> None:
    return mongo_client.audit.reminders.delete_one({"reminder_id": str(reminder_id)})
