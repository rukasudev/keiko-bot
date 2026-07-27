from datetime import datetime, time
from typing import Callable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app import bot, logger
from app.constants import LogTypes as logconstants
from app.services.dates import next_mm_dd_occurrence, parse_mm_dd


def mm_dd_yearly_rrule(mm_dd: str) -> str:
    month, day = parse_mm_dd(mm_dd)
    if month == 2 and day == 29:
        return "FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=28,29;BYSETPOS=-1"
    return f"FREQ=YEARLY;BYMONTH={month};BYMONTHDAY={day}"


def _parse_notification_time(notification_time: Optional[str]) -> time:
    if not notification_time:
        return time(12, 0)
    hour, minute = [int(part) for part in str(notification_time).split(":", 1)]
    return time(hour, minute)


def next_mm_dd_occurrence_for_timezone(
    mm_dd: str,
    timezone_name: str = None,
    notification_time: str = None,
) -> datetime:
    if not timezone_name:
        return next_mm_dd_occurrence(mm_dd)
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warn(
            f"Invalid reminder timezone: {timezone_name}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return next_mm_dd_occurrence(mm_dd)

    today = datetime.now(tz).date()
    occurrence = next_mm_dd_occurrence(mm_dd, today).date()
    return datetime.combine(occurrence, _parse_notification_time(notification_time), tzinfo=tz)


def create_reminder(
    title: str,
    mm_dd: str,
    notes: str = "",
    timezone_name: str = None,
    notification_time: str = None,
) -> Optional[str]:
    if bot.config.is_dev():
        return None
    occurrence = next_mm_dd_occurrence_for_timezone(mm_dd, timezone_name, notification_time)
    response = bot.reminder.create_reminder({
        "title": title,
        "date_tz": occurrence if timezone_name else occurrence.date(),
        "rrule": mm_dd_yearly_rrule(mm_dd),
        "timezone": timezone_name or "UTC",
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


def update_reminder(
    reminder_id: Optional[str],
    mm_dd: str,
    timezone_name: str = None,
    notification_time: str = None,
) -> None:
    if not reminder_id or bot.config.is_dev():
        return
    try:
        occurrence = next_mm_dd_occurrence_for_timezone(mm_dd, timezone_name, notification_time)
        bot.reminder.update_reminder(
            reminder_id,
            occurrence if timezone_name else occurrence.date(),
            rrule=mm_dd_yearly_rrule(mm_dd),
            timezone=timezone_name or "UTC",
        )
    except Exception as e:
        logger.warn(
            f"Failed to update reminder {reminder_id}: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


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
