from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import discord

from app.components.buttons import AdditionalButton
from app.constants import Commands as commands_constants
from app.constants import KeikoIcons
from app.constants import Style
from app.data import birthdays as birthdays_data
from app.data.birthdays import to_summary_composition
from app.services.dates import (
    format_mm_dd_count,
    format_mm_dd_label,
    format_month_count,
    is_valid_mm_dd,
    next_mm_dd_occurrence,
)
from app.services.moderations import update_moderations_by_guild
from app.services import reminders as reminders_service
from app.services.utils import (
    ml,
    parse_locale,
)


async def manager(interaction: discord.Interaction, guild_id: str) -> None:
    from app.services.moderations import send_command_form_message
    from app.services.moderations import send_command_manager_message

    locale = parse_locale(interaction.locale)
    config = birthdays_data.find_birthday_config(guild_id)
    enabled = birthdays_data.is_birthday_enabled(guild_id)
    if not enabled or not config:
        return await send_command_form_message(
            interaction,
            commands_constants.REMINDERS_BIRTHDAY_KEY,
            persistence_callback=persist_setup_form,
        )

    stats_button = AdditionalButton(
        callback=send_stats_message,
        label=_mb("stats.button.label", locale),
        desc=_mb("stats.button.desc", locale),
        emoji="📊",
        style=discord.ButtonStyle.grey,
        defer=True,
        auto_disable=True,
    )
    await send_command_manager_message(
        interaction,
        commands_constants.REMINDERS_BIRTHDAY_KEY,
        birthday_manager_cog_data(guild_id),
        additional_buttons=[stats_button],
        settings_provider=birthday_manager_settings,
        lifecycle_callbacks={
            commands_constants.LIFECYCLE_EDIT: edit_birthday_save,
            commands_constants.LIFECYCLE_DISABLE: disable_birthdays_manager,
            commands_constants.LIFECYCLE_ADD_ITEM: add_birthdays_manager_item,
            commands_constants.LIFECYCLE_REMOVE_ITEM: remove_birthdays_manager_item,
        },
    )


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
        reminder_id = reminders_service.create_reminder(
            commands_constants.REMINDER_API_TITLE_BIRTHDAY,
            mm_dd,
            notes=mm_dd,
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


def birthday_manager_cog_data(guild_id: str) -> Dict[str, Any]:
    config = birthdays_data.find_birthday_config(guild_id) or {}
    items = birthdays_data.find_birthday_items_by_guild(guild_id)
    return {
        "guild_id": str(guild_id),
        commands_constants.ENABLED_KEY: birthdays_data.is_birthday_enabled(guild_id),
        commands_constants.BIRTHDAY_CONFIG_CHANNEL: {
            "style": "channel",
            "values": str(config.get("channel_id")) if config.get("channel_id") else None,
        },
        commands_constants.BIRTHDAY_CONFIG_MENTION_EVERYONE: {
            "style": "boolean",
            "values": bool(config.get("mention_everyone")),
        },
        commands_constants.REMINDERS_BIRTHDAY_KEY: {
            "style": "composition",
            "values": [to_summary_composition(item) for item in items],
        },
    }


async def edit_birthday_save(interaction: discord.Interaction, manager_view: discord.ui.View, data: Dict[str, Any]) -> None:
    guild_id = str(interaction.guild_id)
    form = manager_view.edited_form_view
    composition_index = getattr(form, "composition_index", None)

    composition_data = data.get(commands_constants.REMINDERS_BIRTHDAY_KEY)
    if composition_index is not None and composition_data:
        items = composition_data.get("values") or []
        index = int(composition_index)
        if 0 <= index < len(items):
            save_form_birthday_item(guild_id, items[index])
        return

    if commands_constants.BIRTHDAY_CONFIG_CHANNEL in data or commands_constants.BIRTHDAY_CONFIG_MENTION_EVERYONE in data:
        config = birthdays_data.find_birthday_config(guild_id) or {}
        channel_entry = data.get(commands_constants.BIRTHDAY_CONFIG_CHANNEL)
        mention_entry = data.get(commands_constants.BIRTHDAY_CONFIG_MENTION_EVERYONE)
        channel_id = _extract_first(channel_entry) or config.get("channel_id")
        mention_everyone = (
            _parse_bool(_extract_first(mention_entry))
            if mention_entry is not None
            else bool(config.get("mention_everyone"))
        )
        setup_birthdays(guild_id, str(channel_id), mention_everyone, parse_locale(interaction.locale))


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


def birthday_manager_settings(
    interaction: discord.Interaction,
    cog_data: Dict[str, Any],
    locale: str,
) -> List[Dict[str, Any]]:
    guild_id = str(interaction.guild_id)
    config = birthdays_data.find_birthday_config(guild_id) or {}
    stats = get_birthday_stats(guild_id)
    upcoming = get_upcoming_birthdays(guild_id, limit=3)
    upcoming_text = "\n".join(
        f"{index}. <@{item['user_id']}> — {format_mm_dd_label(item['date'], locale)}"
        for index, item in enumerate(upcoming, start=1)
    ) or "-"

    return [
        {
            "title": _mb("settings.channel", locale),
            "value": config.get("channel_id"),
            "style": "channel",
        },
        {
            "title": _mb("settings.mention-everyone", locale),
            "value": bool(config.get("mention_everyone")),
            "style": "boolean",
        },
        {
            "title": _mb("settings.total", locale),
            "value": str(stats["total"]),
        },
        {
            "title": _mb("settings.next", locale),
            "value": upcoming_text,
        },
    ]


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
    embed = discord.Embed(
        title=_mb("stats.embed.title", locale),
        description="\n".join(lines),
        color=int(Style.BACKGROUND_COLOR, base=16),
    )
    embed.set_thumbnail(url=KeikoIcons.IMAGE_03)
    footer_text = ml("commands.commands.commons.embed.footer", locale=locale)
    if footer_text:
        embed.set_footer(text=f"• {footer_text}")
    await interaction.followup.send(embed=embed, ephemeral=True)


def disable_birthdays_manager(interaction: discord.Interaction, cogs: Any = None) -> None:
    handle_unsubscribe_birthdays(interaction)


def setup_birthdays(guild_id: str, channel_id: str, mention_everyone: bool, locale: str = None) -> Dict[str, Any]:
    update_moderations_by_guild(guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY, True)
    return birthdays_data.upsert_birthday_config(guild_id, channel_id, mention_everyone, locale)


def persist_setup_form(interaction: discord.Interaction, responses: List[Dict[str, Any]], cog_param: Dict[str, Any]) -> List[Dict[str, Any]]:
    return save_setup_form(str(interaction.guild_id), responses, parse_locale(interaction.locale))


def save_setup_form(guild_id: str, responses: List[Dict[str, Any]], locale: str = None) -> List[Dict[str, Any]]:
    channel_id = _response_value(responses, commands_constants.BIRTHDAY_CONFIG_CHANNEL)
    mention_everyone = _parse_bool(_response_value(responses, commands_constants.BIRTHDAY_CONFIG_MENTION_EVERYONE))
    items = _response_value(responses, commands_constants.REMINDERS_BIRTHDAY_KEY) or []
    if isinstance(items, dict):
        items = [items]

    setup_birthdays(guild_id, str(channel_id), mention_everyone, locale)

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


def _response_value(responses: List[Dict[str, Any]], key: str) -> Any:
    response = next((item for item in responses if item.get("key") == key), None)
    if not response:
        return None
    return response.get("_raw_value", response.get("value"))


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


def handle_unsubscribe_birthdays(interaction: discord.Interaction, cogs: Any = None) -> None:
    guild_id = str(interaction.guild_id)
    items = birthdays_data.find_birthday_items_by_guild(guild_id)
    reminder_ids = {item.get("reminder_id") for item in items if item.get("reminder_id")}

    birthdays_data.delete_birthday_items_by_guild(guild_id)
    birthdays_data.delete_birthday_config(guild_id)

    for reminder_id in reminder_ids:
        reminders_service.delete_reminder(reminder_id)

    update_moderations_by_guild(guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY, False)


async def add_birthdays_manager_item(interaction: discord.Interaction, manager_view: discord.ui.View, response: Dict[str, Any]) -> Optional[bool]:
    birthday = _parse_form_birthday_item(response)
    if birthday and birthdays_data.find_birthday_item(str(interaction.guild_id), birthday["user_id"]):
        return False

    saved_item = save_form_birthday_item(str(interaction.guild_id), response)
    if not saved_item:
        return

    from app.services.compositions import merge_composition_item_by_nested_value

    values = manager_view.cogs[commands_constants.REMINDERS_BIRTHDAY_KEY]["values"]
    merge_composition_item_by_nested_value(
        values,
        to_summary_composition(saved_item),
        "user",
    )


async def remove_birthdays_manager_item(
    interaction: discord.Interaction,
    manager_view: discord.ui.View,
    item_removed: Dict[str, Any],
    new_cogs: Dict[str, Any],
) -> None:
    user = item_removed.get("user") if isinstance(item_removed, dict) else None
    user_id = user.get("value") if isinstance(user, dict) else user
    if user_id:
        remove_birthday(str(interaction.guild_id), str(user_id))
