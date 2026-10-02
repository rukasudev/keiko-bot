import asyncio
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from app import logger
from app.components.embed import base_embed, default_welcome_embed
from app.constants import Commands as commands_constants
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.data import birthdays as birthdays_data
from app.data.birthdays import to_summary_composition
from app.services.dates import (
    format_mm_dd_count,
    format_mm_dd_label,
    format_month_count,
    is_valid_mm_dd,
    next_mm_dd_occurrence,
)
from app.settings import open_feature
from app.services.moderations import update_moderations_by_guild
from app.services import reminders as reminders_service
from app.services.utils import (
    ml,
    parse_locale,
    values_of,
)


async def manager(interaction: discord.Interaction, guild_id: str) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, commands_constants.REMINDERS_BIRTHDAY_KEY)


def _mb(key: str, locale: str) -> str:
    return ml(f"commands.commands.commons.reminders-birthdays-manager.{key}", locale=locale)


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
) -> Optional[Dict[str, Any]]:
    existing = birthdays_data.find_birthday_item(guild_id, user_id)
    old_date = existing.get("date") if existing else None
    old_reminder_id = existing.get("reminder_id") if existing else None
    self_edit_count = get_self_edit_count(existing) + (1 if existing and increment_self_edit else 0)

    reminder_id = birthdays_data.find_reminder_id_by_guild_and_date(guild_id, mm_dd)
    if not reminder_id:
        config = birthdays_data.find_birthday_config(guild_id) or {}
        reminder_id = reminders_service.create_reminder(
            commands_constants.REMINDER_API_TITLE_BIRTHDAY,
            mm_dd,
            notes=mm_dd,
            timezone_name=config.get("timezone"),
            notification_time=config.get("notification_time"),
        )
    item = birthdays_data.upsert_birthday_item(
        guild_id,
        user_id,
        mm_dd,
        reminder_id,
        self_edit_count=self_edit_count,
        message=message,
        image=image,
    )

    if old_date and old_date != mm_dd:
        reminders_service.cleanup_reminder_if_unused(
            old_reminder_id,
            lambda: birthdays_data.count_birthday_items_by_guild_and_date(guild_id, old_date),
        )

    return item


def reconcile_missing_reminders(limit: int = 50) -> int:
    """Create the reminders that a failed save left behind. Returns how many.

    Runs on a loop rather than at save time on purpose: the reason a creation
    fails is usually not going to be fixed by retrying twice in a row, and a
    command must not wait on it either way.
    """
    repaired = 0
    done: set = set()

    for item in birthdays_data.find_birthdays_missing_reminder(limit):
        guild_id = item.get("guild_id")
        mm_dd = item.get("date")
        if not guild_id or not mm_dd or (guild_id, mm_dd) in done:
            continue
        done.add((guild_id, mm_dd))

        config = birthdays_data.find_birthday_config(guild_id)
        if not config:
            # No configuration means no channel to greet in; a reminder for it
            # would schedule a message with nowhere to go.
            continue

        try:
            reminder_id = reminders_service.create_reminder(
                commands_constants.REMINDER_API_TITLE_BIRTHDAY,
                mm_dd,
                notes=mm_dd,
                timezone_name=config.get("timezone"),
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

        birthdays_data.set_reminder_id_for_guild_and_date(guild_id, mm_dd, reminder_id)
        repaired += 1

    return repaired


def remove_birthday(guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    item = birthdays_data.remove_birthday_item(guild_id, user_id)
    if item:
        removed_date = item.get("date")
        reminders_service.cleanup_reminder_if_unused(
            item.get("reminder_id"),
            lambda: birthdays_data.count_birthday_items_by_guild_and_date(guild_id, removed_date),
        )
    return item


def get_upcoming_birthdays(guild_id: str, limit: int = 3, today: Optional[date] = None) -> List[Dict[str, Any]]:
    today = today or datetime.now(timezone.utc).date()
    items = birthdays_data.find_birthday_items_by_guild(guild_id)
    return sorted(items, key=lambda item: next_mm_dd_occurrence(item["date"], today))[:limit]


def get_birthday_stats(guild_id: str, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or datetime.now(timezone.utc).date()
    items = birthdays_data.find_birthday_items_by_guild(guild_id)
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
    config = birthdays_data.find_birthday_config(guild_id) or {}
    items = birthdays_data.find_birthday_items_by_guild(guild_id)
    message = config.get("default_message") or {}
    return {
        "guild_id": str(guild_id),
        commands_constants.ENABLED_KEY: birthdays_data.is_birthday_enabled(guild_id),
        commands_constants.BIRTHDAY_CONFIG_CHANNEL: {
            "style": "channel",
            "values": str(config.get("channel_id")) if config.get("channel_id") else None,
        },
        commands_constants.BIRTHDAY_CONFIG_MENTION_EVERYONE: {
            "style": "boolean",
            "values": (
                bool(config.get("mention_everyone")) if apply_defaults
                else config.get("mention_everyone")
            ),
        },
        commands_constants.BIRTHDAY_CONFIG_TIMEZONE: config.get("timezone"),
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
        for config in birthdays_data.find_all_birthday_configs()
        if config.get("guild_id")
    ]


def save_birthday_config_changes(guild_id: str, data: Dict[str, Any], locale: str) -> None:
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
        config = birthdays_data.find_birthday_config(guild_id) or {}
        channel_entry = data.get(commands_constants.BIRTHDAY_CONFIG_CHANNEL)
        mention_entry = data.get(commands_constants.BIRTHDAY_CONFIG_MENTION_EVERYONE)
        timezone_entry = data.get(commands_constants.BIRTHDAY_CONFIG_TIMEZONE)
        notification_time_entry = data.get(commands_constants.BIRTHDAY_CONFIG_NOTIFICATION_TIME)
        channel_id = _extract_first(channel_entry) or config.get("channel_id")
        mention_everyone = (
            _parse_bool(_extract_first(mention_entry))
            if mention_entry is not None
            else bool(config.get("mention_everyone"))
        )
        timezone_value = _extract_first(timezone_entry) or config.get("timezone")
        notification_time = _extract_first(notification_time_entry) or config.get("notification_time")
        default_message = _default_message_from_data(data, config)
        setup_birthdays(
            guild_id,
            str(channel_id),
            mention_everyone,
            locale,
            timezone_value,
            notification_time,
            default_message,
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


def _schedule_changed(previous_config: Dict[str, Any], config: Dict[str, Any]) -> bool:
    return (
        previous_config.get("timezone") != config.get("timezone")
        or previous_config.get("notification_time") != config.get("notification_time")
    )


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
    if not timezone_value or not notification_time:
        return
    reminders_by_id: Dict[str, str] = {}
    for item in birthdays_data.find_birthday_items_by_guild(guild_id):
        reminder_id = item.get("reminder_id")
        date_value = item.get("date")
        if reminder_id and date_value:
            reminders_by_id[str(reminder_id)] = str(date_value)
    for reminder_id, date_value in reminders_by_id.items():
        reminders_service.update_reminder(
            reminder_id,
            date_value,
            timezone_name=timezone_value,
            notification_time=notification_time,
        )


def birthday_settings_rows(guild_id: str, locale: str) -> List[Dict[str, Any]]:
    """The panel rows of the birthday feature, from its own collections."""
    config = birthdays_data.find_birthday_config(guild_id) or {}
    stats = get_birthday_stats(guild_id)
    upcoming = get_upcoming_birthdays(guild_id, limit=3)
    upcoming_text = "\n".join(
        f"{index}. <@{item['user_id']}>: {format_mm_dd_label(item['date'], locale)}"
        for index, item in enumerate(upcoming, start=1)
    ) or "-"

    return [
        {
            "key": "channel",
            "title": _mb("settings.channel", locale),
            "value": config.get("channel_id"),
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
            "value": config.get("timezone") or "-",
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
            "value": upcoming_text,
        },
    ]


def render_birthday_message(
    text: str, member_mention: str, guild_name: str, mm_dd: str, locale: str
) -> str:
    if not text:
        return text
    return (
        text.replace("{user}", member_mention)
        .replace("{server}", guild_name)
        .replace("{date}", format_mm_dd_label(mm_dd, locale))
    )


def birthday_default_text(key: str, locale: str) -> str:
    return ml(f"messages.birthday-defaults.{key}", locale=locale)


def resolve_message(item: Dict[str, Any], config: Dict[str, Any], locale: str) -> Tuple[str, str]:
    message = item.get("message") or {}
    if message.get("mode") == "custom" and message.get("title") and message.get("content"):
        return message["title"], message["content"]
    default_message = (config or {}).get("default_message") or {}
    if (
        default_message.get("mode") == "custom"
        and default_message.get("title")
        and default_message.get("content")
    ):
        return default_message["title"], default_message["content"]
    return birthday_default_text("title", locale), birthday_default_text("content", locale)


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
        member = guild.get_member(int(user_id)) or member

    config = await asyncio.to_thread(
        birthdays_data.find_birthday_config, str(guild.id)
    ) or {}
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


async def send_stats_message(interaction: discord.Interaction) -> None:
    locale = parse_locale(interaction.locale)
    stats = get_birthday_stats(str(interaction.guild_id))
    lines = [
        f"🎂 **{_mb('stats.fields.total', locale)}:** {stats['total']}",
        f"📅 **{_mb('stats.fields.this-month', locale)}:** {stats['current_month']}",
        f"🏆 **{_mb('stats.fields.top', locale)}:** {format_month_count(stats['max_month'], locale)}",
        f"📉 **{_mb('stats.fields.quietest', locale)}:** {format_month_count(stats['min_month'], locale)}",
        f"⭐ **{_mb('stats.fields.most-common', locale)}:** {format_mm_dd_count(stats['max_date'], locale)}",
    ]
    embed = base_embed(
        _mb("stats.embed.title", locale),
        "\n".join(lines),
        thumbnail=KeikoIcons.IMAGE_03,
        footer=ml("commands.commands.commons.embed.footer", locale=locale),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


def setup_birthdays(
    guild_id: str,
    channel_id: str,
    mention_everyone: bool,
    locale: str = None,
    timezone_value: str = None,
    notification_time: str = None,
    default_message: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    previous_config = birthdays_data.find_birthday_config(guild_id) or {}
    update_moderations_by_guild(guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY, True)
    config = birthdays_data.upsert_birthday_config(
        guild_id,
        channel_id,
        mention_everyone,
        locale,
        timezone=timezone_value,
        notification_time=notification_time,
        default_message=default_message,
    )
    if _schedule_changed(previous_config, config):
        reschedule_birthdays(guild_id, config.get("timezone"), config.get("notification_time"))
    return config


def save_setup_form(guild_id: str, responses: List[Dict[str, Any]], locale: str = None) -> List[Dict[str, Any]]:
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
        locale,
        timezone_value,
        notification_time,
        default_message,
    )

    saved_items = []
    for item in items:
        saved_item = save_form_birthday_item(guild_id, item)
        if saved_item:
            saved_items.append(saved_item)
    return saved_items


def save_form_birthday_item(guild_id: str, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    birthday = _parse_form_birthday_item(item)
    if not birthday:
        return None

    return upsert_birthday(
        guild_id,
        birthday["user_id"],
        birthday["date"],
        message=birthday["message"],
        image=birthday["image"],
    )


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
    """Forget every birthday and reminder of the guild and flag the feature off."""
    items = birthdays_data.find_birthday_items_by_guild(guild_id)
    reminder_ids = {item.get("reminder_id") for item in items if item.get("reminder_id")}

    birthdays_data.delete_birthday_items_by_guild(guild_id)
    birthdays_data.delete_birthday_config(guild_id)

    for reminder_id in reminder_ids:
        reminders_service.delete_reminder(reminder_id)

    update_moderations_by_guild(guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY, False)
