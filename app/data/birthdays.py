from typing import Any, Dict, List, Optional

from app import mongo_client
from app.constants import Commands as constants
from app.data.util import parse_insert_timestamp, parse_update_timestamp


def is_birthday_enabled(guild_id: str) -> bool:
    moderations = mongo_client.guild.moderations.find_one({"guild_id": str(guild_id)})
    return bool(moderations and moderations.get(constants.REMINDERS_BIRTHDAY_KEY))


def find_birthday_config(guild_id: str) -> Optional[Dict[str, Any]]:
    return mongo_client.guild.reminders_birthday.find_one({"guild_id": str(guild_id)})


def upsert_birthday_config(guild_id: str, channel_id: str, mention_everyone: bool = False) -> Dict[str, Any]:
    existing = find_birthday_config(guild_id) or {}
    data = {
        "guild_id": str(guild_id),
        "channel_id": str(channel_id),
        "mention_everyone": bool(mention_everyone),
    }
    update = parse_update_timestamp(data) if existing else parse_insert_timestamp(data)
    mongo_client.guild.reminders_birthday.update_one(
        {"guild_id": str(guild_id)},
        {"$set": update},
        upsert=True,
    )
    return {**existing, **update}


def find_birthday_item(guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    return mongo_client.reminders.birthdays.find_one({
        "guild_id": str(guild_id),
        "user_id": str(user_id),
    })


def find_birthday_items_by_date(date: str) -> List[Dict[str, Any]]:
    return list(mongo_client.reminders.birthdays.find({"date": str(date)}))


def find_birthday_items_by_guild(guild_id: str) -> List[Dict[str, Any]]:
    return list(mongo_client.reminders.birthdays.find({"guild_id": str(guild_id)}))


def find_birthday_items_by_reminder_id(reminder_id: str) -> List[Dict[str, Any]]:
    return list(mongo_client.reminders.birthdays.find({"reminder_id": str(reminder_id)}))


def find_reminder_id_by_date(date: str) -> Optional[str]:
    item = mongo_client.reminders.birthdays.find_one({"date": str(date)})
    return item.get("reminder_id") if item else None


def count_birthday_items_by_date(date: str) -> int:
    return mongo_client.reminders.birthdays.count_documents({"date": str(date)})


def upsert_birthday_item(
    guild_id: str,
    user_id: str,
    date: str,
    reminder_id: Optional[str] = None,
    self_edit_count: Optional[int] = None,
    message: Optional[Dict[str, Any]] = None,
    image: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    existing = find_birthday_item(guild_id, user_id)
    item = build_default_item(user_id, date, reminder_id, self_edit_count)

    if existing:
        item["reminder_id"] = reminder_id if reminder_id is not None else existing.get("reminder_id")
        item["self_edit_count"] = (
            self_edit_count if self_edit_count is not None else existing.get("self_edit_count", 0)
        )
        item["message"] = message or existing.get("message") or item["message"]
        item["image"] = image or existing.get("image") or item["image"]
    else:
        item["message"] = message or item["message"]
        item["image"] = image or item["image"]

    item["guild_id"] = str(guild_id)
    update = parse_update_timestamp(item) if existing else parse_insert_timestamp(item)
    mongo_client.reminders.birthdays.update_one(
        {"guild_id": str(guild_id), "user_id": str(user_id)},
        {"$set": update},
        upsert=True,
    )
    return {**(existing or {}), **update}


def remove_birthday_item(guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    item = find_birthday_item(guild_id, user_id)
    if not item:
        return None
    mongo_client.reminders.birthdays.delete_one({
        "guild_id": str(guild_id),
        "user_id": str(user_id),
    })
    return item


def build_default_item(
    user_id: str,
    date: str,
    reminder_id: Optional[str] = None,
    self_edit_count: Optional[int] = None,
) -> Dict[str, Any]:
    month, day = [int(part) for part in str(date).split("-")]
    return {
        "user_id": str(user_id),
        "date": str(date),
        "month": month,
        "day": day,
        "reminder_id": reminder_id,
        "self_edit_count": self_edit_count or 0,
        "message": {
            "mode": "default",
            "title": None,
            "content": None,
        },
        "image": {
            "mode": "default",
            "url": None,
        },
    }


def to_summary_composition(item: Dict[str, Any]) -> Dict[str, Any]:
    message = item.get("message") or {}
    image = item.get("image") or {}
    return {
        "type": {"value": constants.REMINDER_TYPE_BIRTHDAY, "title": "Type"},
        "user": {"value": str(item.get("user_id")), "title": "Member", "style": "user"},
        "date": {"value": item.get("date"), "title": "Birthday", "style": "birthday_date"},
        "use_custom_message": {"value": message.get("mode", "default"), "title": "Message"},
        "custom_message_title": {"value": message.get("title"), "title": "Title"},
        "custom_message_content": {"value": message.get("content"), "title": "Content"},
        "use_custom_image": {"value": image.get("mode", "default"), "title": "Custom Birthday Image"},
        "custom_image": {"value": image.get("url"), "title": "Custom Birthday Image"},
        "reminder_id": item.get("reminder_id"),
        "self_edit_count": item.get("self_edit_count", 0),
    }
