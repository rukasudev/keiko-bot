from typing import Callable, Optional

from app import bot, logger
from app.constants import LogTypes as logconstants
from app.services.dates import next_mm_dd_occurrence, parse_mm_dd


def mm_dd_yearly_rrule(mm_dd: str) -> str:
    month, day = parse_mm_dd(mm_dd)
    if month == 2 and day == 29:
        return "FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=28,29;BYSETPOS=-1"
    return f"FREQ=YEARLY;BYMONTH={month};BYMONTHDAY={day}"


def create_reminder(title: str, mm_dd: str, notes: str = "") -> Optional[str]:
    if bot.config.is_dev():
        return None
    occurrence = next_mm_dd_occurrence(mm_dd)
    response = bot.reminder.create_reminder({
        "title": title,
        "date_tz": occurrence.date(),
        "rrule": mm_dd_yearly_rrule(mm_dd),
        "timezone": "UTC",
        "notes": notes or mm_dd,
    })
    reminder_id = response.get("id") if isinstance(response, dict) else None
    if not reminder_id:
        logger.error(
            f"Failed to create reminder title={title} date={mm_dd}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
        )
        return None
    return str(reminder_id)


def delete_reminder(reminder_id: Optional[str]) -> None:
    if not reminder_id or bot.config.is_dev():
        return
    try:
        bot.reminder.delete_reminder(reminder_id)
    except Exception as e:
        logger.warn(
            f"Failed to delete reminder {reminder_id}: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


def cleanup_reminder_if_unused(
    reminder_id: Optional[str],
    count_remaining: Callable[[], int],
) -> None:
    if not reminder_id or count_remaining() > 0:
        return
    delete_reminder(reminder_id)
