import asyncio
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from app import logger
from app.components.embed import default_welcome_embed
from app.constants import Commands as commands_constants
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.data.guild import cogs as guild_cogs
from app.data.guild import members as members_data
from app.data.keiko import schedules as schedules_data
from app.core.dates import (
    format_mm_dd_count,
    format_mm_dd_label,
    format_month_count,
    is_valid_mm_dd,
    next_mm_dd_occurrence,
    parse_mm_dd,
    utc_today,
)
from app.settings import open_feature
from app.settings.form.responses.styles import format_value
from app.core import members
from app.core import reminders as reminders_service
from app.core.cache import remove_cog_cache_by_guild
from app.core.cogs import is_feature_on
from app.exceptions import ServerSettingsMissing
from app.core.servers import save_server_setting, server_setting
from app.core.utils import (
    fill_placeholders,
    ml,
    parse_locale,
    values_of,
)


async def manager(interaction: discord.Interaction, guild_id: str) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, commands_constants.REMINDERS_BIRTHDAY_KEY)


def _mb(key: str, locale: str) -> str:
    return ml(f"commands.commands.commons.reminders-birthdays-manager.{key}", locale=locale)


def birthday_config(guild_id: str) -> Optional[Dict[str, Any]]:
    """The saved birthday configuration of a guild, or nothing."""
    return guild_cogs.find_cog_by_guild_id(guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY)


def save_birthday_config(
    guild_id: str,
    channel_id: str,
    mention_everyone: bool = False,
    notification_time: Optional[str] = None,
    default_message: Optional[Dict[str, Any]] = None,
    updated_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Save the birthday configuration over the saved one; the configuration as saved."""
    options: Dict[str, Any] = {
        commands_constants.BIRTHDAY_CONFIG_CHANNEL: str(channel_id),
        commands_constants.BIRTHDAY_CONFIG_MENTION_EVERYONE: bool(mention_everyone),
    }
    if notification_time is not None:
        options[commands_constants.BIRTHDAY_CONFIG_NOTIFICATION_TIME] = str(notification_time)
    if default_message is not None:
        options["default_message"] = default_message

    key = commands_constants.REMINDERS_BIRTHDAY_KEY
    guild_cogs.insert_cog_by_guild_id(
        key, {"guild_id": str(guild_id), **options}, updated_by
    )
    remove_cog_cache_by_guild(str(guild_id), key)
    return birthday_config(guild_id) or {}


def accepts_birthdays(guild_id: str) -> bool:
    """Whether a guild takes birthdays: it is configured and its feature is on."""
    return is_feature_on(birthday_config(guild_id))


def default_birthday(date: str) -> Dict[str, Any]:
    """A birthday on `date` with Keiko's message and image."""
    month, day = [int(part) for part in str(date).split("-")]
    return {
        "date": str(date),
        "month": month,
        "day": day,
        "self_edit_count": 0,
        "message": {"mode": "default", "title": None, "content": None},
        "image": {"mode": "default", "url": None},
    }


def find_birthday(guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """The member's birthday, with what Keiko keeps about them under `state`."""
    return members_data.find_member_option(
        guild_id, user_id, commands_constants.REMINDERS_BIRTHDAY_KEY
    )


def find_birthdays_by_guild(guild_id: str) -> List[Dict[str, Any]]:
    """Every birthday saved in a server."""
    return members_data.find_members_by_option(
        guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY
    )


def find_birthdays_on(guild_id: str, mm_dd: str) -> List[Dict[str, Any]]:
    """The server's birthdays on one date."""
    return members_data.find_members_by_option(
        guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY, {"date": str(mm_dd)}
    )


def count_birthdays_on(guild_id: str, mm_dd: str) -> int:
    """How many of the server's birthdays fall on one date."""
    return members_data.count_members_by_option(
        guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY, {"date": str(mm_dd)}
    )


def save_birthday(
    guild_id: str,
    user_id: str,
    date: str,
    self_edit_count: Optional[int] = None,
    message: Optional[Dict[str, Any]] = None,
    image: Optional[Dict[str, Any]] = None,
    display_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Save the member's birthday, keeping what was saved before where nothing new is given."""
    existing = find_birthday(guild_id, user_id) or {}
    birthday = default_birthday(date)
    birthday["self_edit_count"] = (
        self_edit_count if self_edit_count is not None else existing.get("self_edit_count", 0)
    )
    birthday["message"] = message or existing.get("message") or birthday["message"]
    birthday["image"] = image or existing.get("image") or birthday["image"]

    return members_data.set_member_option(
        guild_id,
        user_id,
        commands_constants.REMINDERS_BIRTHDAY_KEY,
        birthday,
        display_name,
    )


def last_celebrated_year(item: Dict[str, Any]) -> Any:
    """The year Keiko last celebrated the member of a saved birthday."""
    return item["state"].get("last_celebrated_year")


def claim_birthday_year(
    guild_id: str, user_id: str, year: int, display_name: Optional[str] = None
) -> bool:
    """Record this year's celebration of a member; False when it was already recorded."""
    return members_data.claim_member_state(
        guild_id,
        user_id,
        commands_constants.REMINDERS_BIRTHDAY_KEY,
        "last_celebrated_year",
        int(year),
        display_name,
    )


def release_birthday_year(guild_id: str, user_id: str, year: int, previous: Any) -> bool:
    """Give back this year's claim of a member whose message was not posted."""
    return members_data.restore_member_state(
        guild_id,
        user_id,
        commands_constants.REMINDERS_BIRTHDAY_KEY,
        "last_celebrated_year",
        int(year),
        previous,
    )


def get_self_edit_count(item: Optional[Dict[str, Any]]) -> int:
    if not item:
        return 0
    try:
        return int(item.get("self_edit_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def can_self_edit_birthday(item: Optional[Dict[str, Any]]) -> bool:
    return get_self_edit_count(item) < commands_constants.SELF_BIRTHDAY_EDIT_LIMIT


def upsert_birthday(
    guild_id: str,
    user_id: str,
    mm_dd: str,
    increment_self_edit: bool = False,
    message: Optional[Dict[str, Any]] = None,
    image: Optional[Dict[str, Any]] = None,
    display_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    existing = find_birthday(guild_id, user_id)
    old_date = existing.get("date") if existing else None
    self_edit_count = get_self_edit_count(existing) + (1 if existing and increment_self_edit else 0)

    schedule_birthday_date(guild_id, mm_dd)
    try:
        item = save_birthday(
            guild_id,
            user_id,
            mm_dd,
            self_edit_count=self_edit_count,
            message=message,
            image=image,
            display_name=display_name,
        )
    except Exception:
        release_birthday_date(guild_id, mm_dd)
        raise

    if old_date and old_date != mm_dd:
        release_birthday_date(guild_id, old_date)

    return item


def birthday_schedule_id(guild_id: str, mm_dd: str) -> str:
    """The schedule of one server's birthdays on one date."""
    return schedules_data.schedule_id("birthday", guild_id, mm_dd)


def birthday_schedules_by_reminder(reminder_id: Any) -> List[Dict[str, Any]]:
    """Every server's birthday schedule one reminder serves."""
    return schedules_data.find_schedules_by_reminder(reminder_id, "birthday")


def birthday_schedules_missing_reminder(limit: int) -> List[Dict[str, Any]]:
    """Birthday schedules whose reminder could not be created yet, the soonest due first."""
    return schedules_data.find_schedules_missing_reminder("birthday", limit)


def schedule_birthday_date(guild_id: str, mm_dd: str) -> Optional[str]:
    """The reminder of a server's birthdays on a date, created when it has none yet."""
    schedule_id = birthday_schedule_id(guild_id, mm_dd)
    schedule = schedules_data.find_schedule(schedule_id)
    if schedule and schedule.get("reminder_id"):
        return schedule["reminder_id"]

    config = birthday_config(guild_id) or {}
    zone = _server_zone(guild_id)
    notification_time = config.get("notification_time")
    reminder_id = reminders_service.create_reminder(
        commands_constants.REMINDER_API_TITLE_BIRTHDAY,
        mm_dd,
        notes=mm_dd,
        timezone_name=zone,
        notification_time=notification_time,
    )
    schedules_data.upsert_schedule(
        schedule_id,
        "birthday",
        reminder_id,
        due_at=next_birthday_due(mm_dd, zone, notification_time),
    )
    return reminder_id


def release_birthday_date(guild_id: str, mm_dd: str) -> None:
    """Drop a date's schedule once no member of the server has it, and its reminder once
    no server's schedule uses it."""
    if count_birthdays_on(guild_id, mm_dd) > 0:
        return
    schedule_id = birthday_schedule_id(guild_id, mm_dd)
    schedule = schedules_data.find_schedule(schedule_id)
    if not schedule:
        return
    schedules_data.delete_schedule(schedule_id)
    reminder_id = schedule.get("reminder_id")
    reminders_service.cleanup_reminder_if_unused(
        reminder_id,
        lambda: len(birthday_schedules_by_reminder(reminder_id)),
    )


def next_birthday_due(
    mm_dd: str, zone: Optional[str], notification_time: Optional[str]
) -> datetime:
    """When the reminder of a date fires next, in UTC."""
    occurrence = reminders_service.next_mm_dd_occurrence_for_timezone(
        mm_dd, zone, notification_time
    )
    return occurrence.astimezone(timezone.utc)


def reconcile_missing_reminders(limit: int = 50) -> int:
    """Create the reminders that a failed save left behind, releasing the schedules no
    configuration or member needs; how many it created."""
    repaired = 0

    for schedule in birthday_schedules_missing_reminder(limit):
        guild_id, mm_dd = schedules_data.schedule_subject(schedule)
        config = birthday_config(guild_id)
        if not config or not count_birthdays_on(guild_id, mm_dd):
            schedules_data.delete_schedule_without_reminder(schedule["_id"])
            continue

        zone = _server_zone(guild_id)
        try:
            reminder_id = reminders_service.create_reminder(
                commands_constants.REMINDER_API_TITLE_BIRTHDAY,
                mm_dd,
                notes=mm_dd,
                timezone_name=zone,
                notification_time=config.get("notification_time"),
            )
        except Exception as error:
            logger.warn(
                f"Reminder reconciliation failed for guild {guild_id} on {mm_dd}: "
                f"{type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            continue

        if not reminder_id:
            continue

        schedules_data.upsert_schedule(
            schedule["_id"],
            "birthday",
            reminder_id,
            due_at=next_birthday_due(mm_dd, zone, config.get("notification_time")),
        )
        repaired += 1

    return repaired


def remove_birthday(guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    item = members_data.unset_member_option(
        guild_id, user_id, commands_constants.REMINDERS_BIRTHDAY_KEY
    )
    if item:
        release_birthday_date(guild_id, item["date"])
    return item


def get_upcoming_birthdays(guild_id: str, limit: int = 3, today: Optional[date] = None) -> List[Dict[str, Any]]:
    today = today or utc_today()
    items = find_birthdays_by_guild(guild_id)
    return sorted(items, key=lambda item: next_mm_dd_occurrence(item["date"], today))[:limit]


def get_birthday_stats(guild_id: str, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or utc_today()
    items = find_birthdays_by_guild(guild_id)
    month_counts = Counter(item.get("month") for item in items)
    date_counts = Counter(item.get("date") for item in items)
    return {
        "total": len(items),
        "current_month": month_counts.get(today.month, 0),
        "max_month": month_counts.most_common(1)[0] if month_counts else None,
        "min_month": min(month_counts.items(), key=lambda pair: pair[1]) if month_counts else None,
        "max_date": date_counts.most_common(1)[0] if date_counts else None,
    }


def birthday_manager_cog_data(guild_id: str, apply_defaults: bool = True) -> Dict[str, Any]:
    """The saved configuration, keyed as the form names it.

    `apply_defaults` is the difference between drawing and measuring. The card
    needs a value in every row, so a guild that never chose a message mode still
    renders as `default`. A report must not read that invented value as a
    choice: it counted three guilds as having configured a message they never
    touched. Absence stays absence when the caller says so.
    """
    config = birthday_config(guild_id) or {}
    items = find_birthdays_by_guild(guild_id)
    message = config.get("default_message") or {}
    return {
        "guild_id": str(guild_id),
        commands_constants.ENABLED_KEY: is_feature_on(config),
        commands_constants.BIRTHDAY_CONFIG_CHANNEL: {
            "style": "channel",
            "values": str(config.get("channel")) if config.get("channel") else None,
        },
        commands_constants.BIRTHDAY_CONFIG_MENTION_EVERYONE: {
            "style": "boolean",
            "values": (
                bool(config.get("mention_everyone")) if apply_defaults
                else config.get("mention_everyone")
            ),
        },
        commands_constants.BIRTHDAY_CONFIG_TIMEZONE: _server_zone(guild_id, apply_defaults),
        commands_constants.BIRTHDAY_CONFIG_NOTIFICATION_TIME: config.get("notification_time"),
        commands_constants.BIRTHDAY_CONFIG_DEFAULT_MESSAGE_MODE: (
            message.get("mode", "default") if apply_defaults else message.get("mode")
        ),
        commands_constants.BIRTHDAY_CONFIG_DEFAULT_MESSAGE_TITLE: message.get("title"),
        commands_constants.BIRTHDAY_CONFIG_DEFAULT_MESSAGE_CONTENT: message.get("content"),
        commands_constants.REMINDERS_BIRTHDAY_KEY: {
            "style": "composition",
            "values": [to_summary_composition(item) for item in items],
        },
    }


def config_states() -> List[Dict[str, Any]]:
    """Every guild's birthday configuration, in the shape the YAML names it.

    This feature stores `channel_id`, folds three settings into
    `default_message` and keeps the birthdays in another database, so a reader
    walking the raw document finds none of the form's keys. Read without the
    card's rendering defaults: an unset value must not arrive as a choice.
    """
    return [
        birthday_manager_cog_data(config["guild_id"], apply_defaults=False)
        for config in guild_cogs.find_all_cogs(commands_constants.REMINDERS_BIRTHDAY_KEY)
        if config.get("guild_id")
    ]


def save_birthday_config_changes(
    guild_id: str, data: Dict[str, Any], updated_by: Optional[str] = None
) -> None:
    """Apply the edited settings on top of the saved birthday configuration."""
    config_keys = {
        commands_constants.BIRTHDAY_CONFIG_CHANNEL,
        commands_constants.BIRTHDAY_CONFIG_MENTION_EVERYONE,
        commands_constants.BIRTHDAY_CONFIG_TIMEZONE,
        commands_constants.BIRTHDAY_CONFIG_NOTIFICATION_TIME,
        commands_constants.BIRTHDAY_CONFIG_DEFAULT_MESSAGE_MODE,
        commands_constants.BIRTHDAY_CONFIG_DEFAULT_MESSAGE_TITLE,
        commands_constants.BIRTHDAY_CONFIG_DEFAULT_MESSAGE_CONTENT,
    }
    if config_keys.intersection(data):
        config = birthday_config(guild_id) or {}
        channel_entry = data.get(commands_constants.BIRTHDAY_CONFIG_CHANNEL)
        mention_entry = data.get(commands_constants.BIRTHDAY_CONFIG_MENTION_EVERYONE)
        timezone_entry = data.get(commands_constants.BIRTHDAY_CONFIG_TIMEZONE)
        notification_time_entry = data.get(commands_constants.BIRTHDAY_CONFIG_NOTIFICATION_TIME)
        channel_id = _extract_first(channel_entry) or config.get("channel")
        mention_everyone = (
            _parse_bool(_extract_first(mention_entry))
            if mention_entry is not None
            else bool(config.get("mention_everyone"))
        )
        timezone_value = _extract_first(timezone_entry) or _server_zone(guild_id)
        notification_time = _extract_first(notification_time_entry) or config.get("notification_time")
        default_message = _default_message_from_data(data, config)
        setup_birthdays(
            guild_id,
            str(channel_id),
            mention_everyone,
            timezone_value,
            notification_time,
            default_message,
            updated_by=updated_by,
        )


def _extract_first(entry: Any) -> Any:
    if entry is None:
        return None
    if isinstance(entry, dict):
        values = entry.get("values")
    else:
        values = entry
    if isinstance(values, list):
        return values[0] if values else None
    return values


def _server_zone(guild_id: str, required: bool = True) -> Optional[str]:
    try:
        return server_setting(guild_id, commands_constants.SERVER_TIMEZONE_KEY)
    except ServerSettingsMissing:
        if required:
            raise
        return None


def _default_message_label(default_message: Optional[Dict[str, Any]], locale: str) -> str:
    mode = (default_message or {}).get("mode")
    key = "custom-label" if mode == "custom" else "default-label"
    return ml(f"buttons.summary-card.{key}", locale=locale)


def _default_message_from_values(mode: Any, title: Any, content: Any) -> Dict[str, Any]:
    custom = mode == "custom" and bool(title) and bool(content)
    return {
        "mode": "custom" if custom else "default",
        "title": str(title) if custom else None,
        "content": str(content) if custom else None,
    }


def _default_message_from_responses(responses: List[Dict[str, Any]]) -> Dict[str, Any]:
    values = values_of(responses)
    return _default_message_from_values(
        values.get(commands_constants.BIRTHDAY_CONFIG_DEFAULT_MESSAGE_MODE),
        values.get(commands_constants.BIRTHDAY_CONFIG_DEFAULT_MESSAGE_TITLE),
        values.get(commands_constants.BIRTHDAY_CONFIG_DEFAULT_MESSAGE_CONTENT),
    )


def _default_message_from_data(data: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    current = config.get("default_message") or {}
    return _default_message_from_values(
        _extract_first(data.get(commands_constants.BIRTHDAY_CONFIG_DEFAULT_MESSAGE_MODE)) or current.get("mode", "default"),
        _extract_first(data.get(commands_constants.BIRTHDAY_CONFIG_DEFAULT_MESSAGE_TITLE)) or current.get("title"),
        _extract_first(data.get(commands_constants.BIRTHDAY_CONFIG_DEFAULT_MESSAGE_CONTENT)) or current.get("content"),
    )


def reschedule_birthdays(guild_id: str, timezone_value: str, notification_time: str) -> None:
    """Move every birthday reminder of a guild to `timezone_value` at `notification_time`,
    once per reminder."""
    if not timezone_value or not notification_time:
        return

    moved = set()
    for mm_dd in sorted({item["date"] for item in find_birthdays_by_guild(guild_id)}):
        schedule_id = birthday_schedule_id(guild_id, mm_dd)
        schedule = schedules_data.find_schedule(schedule_id) or {}
        reminder_id = schedule.get("reminder_id")
        if not reminder_id:
            continue
        schedules_data.set_schedule_due(
            schedule_id, next_birthday_due(mm_dd, timezone_value, notification_time)
        )
        if reminder_id in moved:
            continue
        moved.add(reminder_id)
        reminders_service.update_reminder(
            reminder_id,
            mm_dd,
            timezone_name=timezone_value,
            notification_time=notification_time,
        )


def birthday_settings_rows(
    guild_id: str, locale: str, today: Optional[date] = None
) -> List[Dict[str, Any]]:
    """The panel rows of the birthday feature, from its own collections, counted from `today`."""
    config = birthday_config(guild_id) or {}
    stats = get_birthday_stats(guild_id, today=today)
    upcoming = get_upcoming_birthdays(guild_id, limit=3, today=today)
    upcoming_lines = [
        f"<@{item['user_id']}>: {format_mm_dd_label(item['date'], locale)}"
        for item in upcoming
    ]

    return [
        {
            "key": "channel",
            "title": _mb("settings.channel", locale),
            "value": config.get("channel"),
            "style": "channel",
        },
        {
            "key": "mention_everyone",
            "title": _mb("settings.mention-everyone", locale),
            "value": bool(config.get("mention_everyone")),
            "style": "boolean",
        },
        {
            "key": "timezone",
            "title": _mb("settings.timezone", locale),
            "value": _server_zone(guild_id),
        },
        {
            "key": "notification_time",
            "title": _mb("settings.notification-time", locale),
            "value": config.get("notification_time") or "-",
        },
        {
            "key": "default_message_mode",
            "title": _mb("settings.default-message", locale),
            "value": _default_message_label(config.get("default_message"), locale),
        },
        {
            "key": "reminders_birthday",
            "title": _mb("settings.total", locale),
            "value": str(stats["total"]),
        },
        {
            "key": "reminders_birthday",
            "title": _mb("settings.next", locale),
            "value": upcoming_lines,
            "style": "bullet",
        },
    ]


def birthday_month_pages(
    guild_id: str, locale: str, today: Optional[date] = None
) -> List[Tuple[str, str, List[str]]]:
    """The birthdays in date order, a month per page from this one, or one empty page."""
    today = today or utc_today()
    title = _mb("pages.title", locale)
    by_month: Dict[int, List[Dict[str, Any]]] = {}
    for item in find_birthdays_by_guild(guild_id):
        try:
            month, _day = parse_mm_dd(str(item.get("date")))
        except (TypeError, ValueError):
            continue
        by_month.setdefault(month, []).append(item)

    pages = []
    for offset in range(12):
        month = (today.month - 1 + offset) % 12 + 1
        if month not in by_month:
            continue
        lines = [
            f"- <@{item['user_id']}>: {format_mm_dd_label(item['date'], locale)}"
            for item in sorted(by_month[month], key=lambda item: str(item["date"]))
        ]
        pages.append((title, _mb("pages.order", locale), lines))
    return pages or [(title, _mb("pages.empty", locale), [])]


def birthday_found_members(guild_id: str, locale: str) -> Dict[str, List[str]]:
    """Each listed member's birthday messages, for the screen a search opens."""
    config = birthday_config(guild_id) or {}
    found: Dict[str, List[str]] = {}
    for item in find_birthdays_by_guild(guild_id):
        source, title, content = message_source(item, config, locale)
        label = _mb(f"found.messages.{source}", locale)
        block = format_value([title, content], "code-block", locale)
        found[str(item.get("user_id"))] = [f"💬 **{label}**{block}"]
    return found


def render_birthday_message(
    text: str, member_mention: str, guild_name: str, mm_dd: str, locale: str
) -> str:
    if not text:
        return text
    return fill_placeholders(text, {
        "user": member_mention,
        "server": guild_name,
        "date": format_mm_dd_label(mm_dd, locale),
    })


def birthday_default_text(key: str, locale: str) -> str:
    return ml(f"messages.birthday-defaults.{key}", locale=locale)


def message_source(
    item: Dict[str, Any], config: Dict[str, Any], locale: str
) -> Tuple[str, str, str]:
    """Whose message celebrates the member (`member`, `server` or `keiko`), and its text."""
    message = item.get("message") or {}
    if message.get("mode") == "custom" and message.get("title") and message.get("content"):
        return "member", message["title"], message["content"]
    default_message = (config or {}).get("default_message") or {}
    if (
        default_message.get("mode") == "custom"
        and default_message.get("title")
        and default_message.get("content")
    ):
        return "server", default_message["title"], default_message["content"]
    return "keiko", birthday_default_text("title", locale), birthday_default_text("content", locale)


def resolve_message(item: Dict[str, Any], config: Dict[str, Any], locale: str) -> Tuple[str, str]:
    _source, title, content = message_source(item, config, locale)
    return title, content


def resolve_image(item: Dict[str, Any]) -> str:
    image = item.get("image") or {}
    if image.get("mode") == "custom" and image.get("url"):
        return image["url"]
    return KeikoIcons.BIRTHDAY_GIF


def build_celebration_embed(
    item: Dict[str, Any],
    member: discord.Member,
    guild: discord.Guild,
    config: Dict[str, Any],
    locale: str,
) -> discord.Embed:
    title, content = resolve_message(item, config, locale)
    title = render_birthday_message(title, member.display_name, guild.name, item.get("date"), locale)
    content = render_birthday_message(content, member.mention, guild.name, item.get("date"), locale)
    embed = default_welcome_embed(title=title, message=content, image=resolve_image(item))
    embed.set_thumbnail(url=member.display_avatar.url)
    return embed


async def send_birthday_preview(
    interaction: discord.Interaction, responses: List[Dict[str, Any]]
) -> None:
    """The celebration as it will arrive: this member's message, or the default."""
    values = values_of(responses)
    guild = interaction.guild
    member = interaction.user
    user_id = values.get("user")
    if user_id and guild:
        member = await members.find(guild, user_id) or member

    config = await asyncio.to_thread(birthday_config, str(guild.id)) or {}
    if values.get("default_message_mode"):
        config = {
            "default_message": {
                "mode": values.get("default_message_mode"),
                "title": values.get("default_message_title"),
                "content": values.get("default_message_content"),
            }
        }
    item = {
        "date": values.get("date") or date.today().strftime("%m-%d"),
        "message": {
            "mode": values.get("use_custom_message") or "default",
            "title": values.get("custom_message_title"),
            "content": values.get("custom_message_content"),
        },
        "image": {
            "mode": values.get("use_custom_image") or "default",
            "url": values.get("custom_image"),
        },
    }
    embed = build_celebration_embed(
        item, member, guild, config, parse_locale(interaction.locale)
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


def birthday_stats_lines(guild_id: str, locale: str, today: Optional[date] = None) -> List[str]:
    """The birthdays' own figures for their statistics, counted from `today`."""
    stats = get_birthday_stats(guild_id, today)
    return [
        f"🎂 **{_mb('stats.fields.total', locale)}:** {stats['total']}",
        f"📅 **{_mb('stats.fields.this-month', locale)}:** {stats['current_month']}",
        f"🏆 **{_mb('stats.fields.top', locale)}:** {format_month_count(stats['max_month'], locale)}",
        f"📉 **{_mb('stats.fields.quietest', locale)}:** {format_month_count(stats['min_month'], locale)}",
        f"⭐ **{_mb('stats.fields.most-common', locale)}:** {format_mm_dd_count(stats['max_date'], locale)}",
    ]


def setup_birthdays(
    guild_id: str,
    channel_id: str,
    mention_everyone: bool,
    timezone_value: str = None,
    notification_time: str = None,
    default_message: Optional[Dict[str, Any]] = None,
    updated_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Save the birthday configuration in the server's zone, a new zone chosen here
    becoming the server's for new reminders; a new hour moves every reminder to it."""
    previous_config = birthday_config(guild_id) or {}
    previous_zone = str(_server_zone(guild_id))
    zone = timezone_value or previous_zone
    config = save_birthday_config(
        guild_id, channel_id, mention_everyone, notification_time, default_message,
        updated_by,
    )
    if zone != previous_zone:
        save_server_setting(guild_id, commands_constants.SERVER_TIMEZONE_KEY, zone)
    if previous_config.get("notification_time") != config.get("notification_time"):
        reschedule_birthdays(guild_id, zone, config.get("notification_time"))
    return config


def save_setup_form(
    guild_id: str,
    responses: List[Dict[str, Any]],
    updated_by: Optional[str] = None,
    display_names: Optional[Dict[str, Optional[str]]] = None,
) -> List[Dict[str, Any]]:
    values = values_of(responses)
    channel_id = values.get(commands_constants.BIRTHDAY_CONFIG_CHANNEL)
    mention_everyone = _parse_bool(values.get(commands_constants.BIRTHDAY_CONFIG_MENTION_EVERYONE))
    timezone_value = values.get(commands_constants.BIRTHDAY_CONFIG_TIMEZONE)
    notification_time = values.get(commands_constants.BIRTHDAY_CONFIG_NOTIFICATION_TIME)
    default_message = _default_message_from_responses(responses)
    items = values.get(commands_constants.REMINDERS_BIRTHDAY_KEY) or []
    if isinstance(items, dict):
        items = [items]

    setup_birthdays(
        guild_id,
        str(channel_id),
        mention_everyone,
        timezone_value,
        notification_time,
        default_message,
        updated_by=updated_by,
    )

    saved_items = []
    for item in items:
        user_id = str(_nested_value(item, "user") or "")
        saved_item = save_form_birthday_item(
            guild_id, item, (display_names or {}).get(user_id)
        )
        if saved_item:
            saved_items.append(saved_item)
    return saved_items


def save_form_birthday_item(
    guild_id: str, item: Dict[str, Any], display_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    birthday = _parse_form_birthday_item(item)
    if not birthday:
        return None

    return upsert_birthday(
        guild_id,
        birthday["user_id"],
        birthday["date"],
        message=birthday["message"],
        image=birthday["image"],
        display_name=display_name,
    )


def form_birthday_users(responses: List[Dict[str, Any]]) -> List[str]:
    """The members the birthdays of a setup form are for."""
    items = values_of(responses).get(commands_constants.REMINDERS_BIRTHDAY_KEY) or []
    if isinstance(items, dict):
        items = [items]
    return [str(_nested_value(item, "user")) for item in items if _nested_value(item, "user")]


def _nested_value(item: Dict[str, Any], key: str) -> Any:
    value = item.get(key)
    if isinstance(value, dict):
        return value.get("value") or value.get("values")
    return value


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value).strip().lower() in ("true", "1", "yes", "sim", "on")


def _parse_form_birthday_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    user_id = _nested_value(item, "user")
    mm_dd = _nested_value(item, "date")
    if not user_id or not mm_dd or not is_valid_mm_dd(str(mm_dd)):
        return None

    custom_message_mode = _nested_value(item, "use_custom_message")
    custom_image_mode = _nested_value(item, "use_custom_image")
    custom_message_title = _nested_value(item, "custom_message_title")
    custom_message_content = _nested_value(item, "custom_message_content")
    custom_image = _nested_value(item, "custom_image")

    message = {
        "mode": "custom" if custom_message_mode == "custom" else "default",
        "title": custom_message_title if custom_message_mode == "custom" else None,
        "content": custom_message_content if custom_message_mode == "custom" else None,
    }
    image = {
        "mode": "custom" if custom_image_mode == "custom" else "default",
        "url": custom_image if custom_image_mode == "custom" else None,
    }
    return {
        "user_id": str(user_id),
        "date": str(mm_dd),
        "message": message,
        "image": image,
    }


def disable_birthdays(guild_id: str) -> None:
    """Forget every birthday, reminder and the configuration of the guild."""
    dates = {item["date"] for item in find_birthdays_by_guild(guild_id)}
    members_data.unset_option_in_guild(guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY)
    guild_cogs.delete_cog_by_guild_id(guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY)
    for mm_dd in sorted(dates):
        release_birthday_date(guild_id, mm_dd)


def to_summary_composition(item: Dict[str, Any]) -> Dict[str, Any]:
    """A saved birthday as one row of the form's composition."""
    message = item.get("message") or {}
    image = item.get("image") or {}
    return {
        "type": {"value": commands_constants.REMINDER_TYPE_BIRTHDAY, "title": "Type", "hidden": True},
        "user": {"value": str(item.get("user_id")), "title": "Member", "style": "user"},
        "date": {"value": item.get("date"), "title": "Birthday", "style": "mm_dd"},
        "use_custom_message": {
            "value": message.get("mode", "default"),
            "title": "Message",
            "style": "boolean-mode",
            "hidden": message.get("mode", "default") != "custom",
        },
        "custom_message_title": {"value": message.get("title"), "title": "Title", "hidden": True},
        "custom_message_content": {"value": message.get("content"), "title": "Content", "hidden": True},
        "use_custom_image": {
            "value": image.get("mode", "default"),
            "title": "Custom Birthday Image",
            "style": "boolean-mode",
            "hidden": image.get("mode", "default") != "custom",
        },
        "custom_image": {"value": image.get("url"), "title": "Custom Birthday Image", "hidden": True},
        "self_edit_count": {
            "value": item.get("self_edit_count", 0),
            "title": "Self edit count",
            "hidden": True,
        },
    }
