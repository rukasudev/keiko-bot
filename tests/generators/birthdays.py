"""A member's birthday saved the way the bot saves it: the member and the date's schedule."""

from typing import Any, Dict, Optional

from app.data import data, wait
from app.services import reminders_birthdays as birthdays_service


def saved_birthday(
    guild_id: str,
    user_id: str,
    mm_dd: str,
    reminder_id: Optional[str] = None,
    **fields: Any,
) -> Dict[str, Any]:
    """Save the birthday, and the date's schedule with `reminder_id` unless it has one."""
    schedule_id = birthdays_service.birthday_schedule_id(guild_id, mm_dd)
    schedule = wait(data().keiko_schedules.find_schedule(schedule_id))
    if reminder_id is not None or not schedule:
        wait(data().keiko_schedules.upsert_schedule(schedule_id, "birthday", reminder_id))
    return wait(birthdays_service.save_birthday(guild_id, user_id, mm_dd, **fields))


def last_celebrated_year(guild_id: str, user_id: str) -> Any:
    """The year Keiko last celebrated the member, as stored."""
    return birthdays_service.last_celebrated_year(
        wait(birthdays_service.find_birthday(guild_id, user_id))
    )
