from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from app import bot, logger
from app.components.buttons import AdditionalButton
from app.components.embed import default_welcome_embed, response_embed
from app.constants import Commands as commands_constants
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.constants import Style
from app.data import birthdays as birthdays_data
from app.data.birthdays import to_summary_composition
from app.exceptions import ErrorContext
from app.services.dates import (
    get_month_label,
    is_valid_mm_dd,
    next_mm_dd_occurrence,
    parse_date_parts,
    parse_mm_dd,
)
from app.services.moderations import update_moderations_by_guild
from app.services.utils import ml, parse_form_yaml_to_dict, parse_locale, register_style_formatter


SELF_BIRTHDAY_EDIT_LIMIT = 1


def format_birthday_date_value(value: str, locale: str = None) -> str:
    try:
        month, day = [int(part) for part in str(value).split("-")]
    except (TypeError, ValueError):
        return value

    label = get_month_label(month, locale)
    if not label:
        return value

    if str(locale).lower() == "pt-br":
        return f"{day} de {label.lower()}"
    return f"{label} {day}"


register_style_formatter("birthday_date", format_birthday_date_value)


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
        label=_t(locale, "Stats", "Estatísticas"),
        desc=_t(locale, "View birthday statistics", "Ver estatísticas de aniversário"),
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
        enable_composition_controls=False,
        lifecycle_callbacks={
            "edit": edit_birthdays_manager,
            "pause": pause_birthdays_manager,
            "unpause": unpause_birthdays_manager,
            "disable": disable_birthdays_manager,
            "add_item": add_birthdays_manager_item,
            "remove_item": remove_birthdays_manager_item,
        },
    )


def _t(locale: str, en: str, pt: str) -> str:
    return pt if str(locale).lower() == "pt-br" else en


def birthday_rrule(mm_dd: str) -> str:
    month, day = parse_mm_dd(mm_dd)
    if month == 2 and day == 29:
        return "FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=28,29;BYSETPOS=-1"
    return f"FREQ=YEARLY;BYMONTH={month};BYMONTHDAY={day}"


def ensure_reminder_for_date(mm_dd: str) -> Optional[str]:
    existing = birthdays_data.find_reminder_id_by_date(mm_dd)
    if existing:
        return existing
    return create_reminder_for_date(mm_dd)


def create_reminder_for_date(mm_dd: str) -> Optional[str]:
    if bot.config.is_dev():
        return None
    occurrence = next_mm_dd_occurrence(mm_dd)
    response = bot.reminder.create_reminder({
        "title": commands_constants.REMINDER_API_TITLE_BIRTHDAY,
        "date_tz": occurrence.date(),
        "rrule": birthday_rrule(mm_dd),
        "timezone": "UTC",
        "notes": mm_dd,
    })
    reminder_id = response.get("id") if isinstance(response, dict) else None
    if not reminder_id:
        logger.error(
            f"Failed to create birthday reminder for date={mm_dd}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
        )
        return None
    return str(reminder_id)


def cleanup_reminder_for_date(mm_dd: str, reminder_id: Optional[str]) -> None:
    if not reminder_id or birthdays_data.count_birthday_items_by_date(mm_dd) > 0:
        return
    delete_reminder_for_item(reminder_id)


def delete_reminder_for_item(reminder_id: str) -> None:
    if not reminder_id or bot.config.is_dev():
        return
    try:
        bot.reminder.delete_reminder(reminder_id)
    except Exception as e:
        logger.warn(
            f"Failed to delete birthday reminder {reminder_id}: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


def get_self_edit_count(item: Optional[Dict[str, Any]]) -> int:
    if not item:
        return 0
    try:
        return int(item.get("self_edit_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def can_self_edit_birthday(item: Optional[Dict[str, Any]]) -> bool:
    return get_self_edit_count(item) < SELF_BIRTHDAY_EDIT_LIMIT


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

    reminder_id = ensure_reminder_for_date(mm_dd)
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
        cleanup_reminder_for_date(old_date, old_reminder_id)

    return item


def upsert_self_birthday(
    guild_id: str,
    user_id: str,
    mm_dd: str,
    increment_self_edit: bool = False,
) -> Optional[Dict[str, Any]]:
    return upsert_birthday(guild_id, user_id, mm_dd, increment_self_edit=increment_self_edit)


def remove_birthday(guild_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    item = birthdays_data.remove_birthday_item(guild_id, user_id)
    if item:
        cleanup_reminder_for_date(item.get("date"), item.get("reminder_id"))
    return item


def render_birthday_message(text: str, member_mention: str, guild_name: str, mm_dd: str, locale: str) -> str:
    if not text:
        return text
    return (
        text.replace("{user}", member_mention)
        .replace("{server}", guild_name)
        .replace("{date}", format_birthday_date_value(mm_dd, locale))
    )


def parse_birthday_message(text: str, member: discord.Member, guild: discord.Guild, mm_dd: str, locale: str) -> str:
    return render_birthday_message(text, member.mention, guild.name, mm_dd, locale)


def resolve_message(item: Dict[str, Any], locale: str) -> Tuple[str, str]:
    message = item.get("message") or {}
    if message.get("mode") == "custom" and message.get("title") and message.get("content"):
        return message["title"], message["content"]
    return birthday_default_text("title", locale), birthday_default_text("content", locale)


def birthday_default_text(key: str, locale: str) -> str:
    return ml(f"messages.birthday-defaults.{key}", locale=locale)


def resolve_image(item: Dict[str, Any]) -> str:
    image = item.get("image") or {}
    if image.get("mode") == "custom" and image.get("url"):
        return image["url"]
    return KeikoIcons.BIRTHDAY_GIF


def build_celebration_embed(
    item: Dict[str, Any],
    member: discord.Member,
    guild: discord.Guild,
    locale: str,
) -> discord.Embed:
    title, content = resolve_message(item, locale)
    title = parse_birthday_message(title, member, guild, item.get("date"), locale)
    content = parse_birthday_message(content, member, guild, item.get("date"), locale)
    return default_welcome_embed(title=title, message=content, image=resolve_image(item))


async def process_birthday_webhook(reminder_id: str, notes: str) -> None:
    context = ErrorContext(flow="birthday_webhook", extra={"reminder_id": reminder_id, "notes": notes})
    logger.info(f"Processing birthday webhook: {reminder_id}", log_type=logconstants.COMMAND_INFO_TYPE)
    try:
        mm_dd = str(notes or "").strip()
        if not is_valid_mm_dd(mm_dd):
            logger.warn(f"Invalid birthday reminder notes: {notes}", log_type=logconstants.COMMAND_WARN_TYPE)
            return
        
        items = birthdays_data.find_birthday_items_by_date(mm_dd)
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for item in items:
            grouped[str(item.get("guild_id"))].append(item)

        logger.info(
            f"Birthday reminder date={mm_dd} guilds={len(grouped)} items={len(items)}",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )

        for guild_id, guild_items in grouped.items():
            if not birthdays_data.is_birthday_enabled(guild_id):
                continue

            config = birthdays_data.find_birthday_config(guild_id)
            if not config or not config.get("channel_id"):
                continue

            guild = bot.get_guild(int(guild_id))
            if not guild:
                logger.warn(f"Guild not found: {guild_id}", log_type=logconstants.COMMAND_WARN_TYPE)
                continue

            channel = guild.get_channel(int(config["channel_id"]))
            if not channel:
                logger.warn(f"Channel not found: {config['channel_id']}", log_type=logconstants.COMMAND_WARN_TYPE)
                continue

            locale = parse_locale(getattr(guild, "preferred_locale", "en-US"))
            mention_everyone = bool(config.get("mention_everyone"))
            for item in guild_items:
                member = guild.get_member(int(item["user_id"]))
                if not member:
                    logger.warn(f"Member not found: {item['user_id']}", log_type=logconstants.COMMAND_WARN_TYPE)
                    continue

                embed = build_celebration_embed(item, member, guild, locale)
                content = "@everyone" if mention_everyone else None
                allowed_mentions = discord.AllowedMentions(everyone=mention_everyone, users=False, roles=False)
                await channel.send(content=content, embed=embed, allowed_mentions=allowed_mentions)

    except Exception as e:
        logger.error(
            f"Failed to process birthday webhook: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )


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


def format_item_line(item: Dict[str, Any], locale: str = None) -> str:
    return f"<@{item['user_id']}> — **{format_birthday_date_value(item['date'], locale)}**"


def format_numbered_item_line(index: int, item: Dict[str, Any], locale: str = None) -> str:
    return f"{index}. <@{item['user_id']}> — {format_birthday_date_value(item['date'], locale)}"


def birthday_manager_cog_data(guild_id: str) -> Dict[str, Any]:
    return {
        "guild_id": str(guild_id),
        commands_constants.ENABLED_KEY: birthdays_data.is_birthday_enabled(guild_id),
    }


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
        format_numbered_item_line(index, item, locale)
        for index, item in enumerate(upcoming, start=1)
    ) or "-"

    return [
        {
            "title": _t(locale, "Birthday Channel", "Canal de Aniversário"),
            "value": config.get("channel_id"),
            "style": "channel",
        },
        {
            "title": _t(locale, "Mention @everyone", "Mencionar @everyone"),
            "value": bool(config.get("mention_everyone")),
            "style": "boolean",
        },
        {
            "title": _t(locale, "Total birthdays", "Total de aniversários"),
            "value": str(stats["total"]),
        },
        {
            "title": _t(locale, "Next birthdays", "Próximos aniversários"),
            "value": upcoming_text,
        },
    ]


async def send_stats_message(interaction: discord.Interaction) -> None:
    locale = parse_locale(interaction.locale)
    stats = get_birthday_stats(str(interaction.guild_id))
    lines = [
        f"🎂 **{_t(locale, 'Total birthdays', 'Total de aniversários')}:** {stats['total']}",
        f"📅 **{_t(locale, 'Birthdays this month', 'Aniversários neste mês')}:** {stats['current_month']}",
        f"🏆 **{_t(locale, 'Top month', 'Mês com mais aniversários')}:** {_format_month_stat(stats['max_month'], locale)}",
        f"📉 **{_t(locale, 'Quietest month', 'Mês com menos aniversários')}:** {_format_month_stat(stats['min_month'], locale)}",
        f"⭐ **{_t(locale, 'Most common date', 'Data com mais aniversários')}:** {_format_date_stat(stats['max_date'], locale)}",
    ]
    embed = discord.Embed(
        title=f"📊 {_t(locale, 'Birthday stats', 'Estatísticas de aniversário')}",
        description="\n".join(lines),
        color=int(Style.BACKGROUND_COLOR, base=16),
    )
    embed.set_thumbnail(url=KeikoIcons.IMAGE_03)
    footer_text = ml("commands.commands.commons.embed.footer", locale=locale)
    if footer_text:
        embed.set_footer(text=f"• {footer_text}")
    await interaction.followup.send(embed=embed, ephemeral=True)


async def edit_birthdays_manager(interaction: discord.Interaction, parent_view: discord.ui.View) -> None:
    from app.views.birthday_edit_choice import BirthdayEditChoiceView

    view = BirthdayEditChoiceView(parent_view)
    embed = _manager_action_embed(
        parse_locale(interaction.locale),
        _t(parse_locale(interaction.locale), "Edit birthday reminders", "Editar lembretes de aniversário"),
        _t(
            parse_locale(interaction.locale),
            "Choose what you want to edit.",
            "Escolha o que você quer editar.",
        ),
    )
    parent_view.clear_items()
    await interaction.response.edit_message(embed=embed, view=view)


async def _open_birthday_config_form(
    interaction: discord.Interaction,
    parent_view: discord.ui.View,
    fields: List[str],
) -> None:
    from app.views.form import Form

    locale = parse_locale(interaction.locale)
    config = birthdays_data.find_birthday_config(str(interaction.guild_id)) or {}
    cogs = {
        "channel": {"style": "channel", "values": str(config.get("channel_id"))},
        "mention_everyone": {
            "style": "boolean",
            "values": bool(config.get("mention_everyone")),
        },
    }
    form = Form(commands_constants.REMINDERS_BIRTHDAY_KEY, locale, cogs=cogs)
    form.filter_steps(fields)

    async def save_edit(submit_interaction: discord.Interaction) -> None:
        channel_id = _response_value(form.responses, "channel") or config.get("channel_id")
        mention_response = _response_value(form.responses, "mention_everyone")
        mention_everyone = bool(config.get("mention_everyone")) if mention_response is None else _parse_bool(mention_response)
        setup_birthdays(str(submit_interaction.guild_id), str(channel_id), mention_everyone)
        embed = _manager_action_embed(
            locale,
            _t(locale, "Birthday settings updated", "Configurações de aniversário atualizadas"),
            _t(locale, "I saved the new birthday reminder settings.", "Salvei as novas configurações dos lembretes de aniversário."),
        )
        await submit_interaction.response.send_message(embed=embed, ephemeral=True)

    form._set_after_callback(save_edit)
    parent_view.edited_form_view = form
    parent_view.clear_items()
    await form._callback(interaction)


async def _open_member_birthday_form(
    interaction: discord.Interaction,
    view,
    locale: str,
) -> None:
    from app.views.form import Form

    user_id = view.get_response()
    if not user_id:
        await interaction.response.send_message(
            _t(locale, "Choose a member first.", "Escolha um membro primeiro."),
            ephemeral=True,
            delete_after=8,
        )
        return

    item = birthdays_data.find_birthday_item(str(interaction.guild_id), str(user_id))
    if not item:
        await interaction.response.send_message(
            _t(locale, "This member has no birthday saved.", "Esse membro não tem aniversário cadastrado."),
            ephemeral=True,
            delete_after=8,
        )
        return

    form = Form(
        "",
        locale,
        steps=_birthday_member_edit_steps(),
        cogs={
            "date": {
                "style": "birthday_date",
                "values": item.get("date"),
            },
        },
    )
    form.filter_steps(["month", "date"])

    async def save_member_birthday(submit_interaction: discord.Interaction) -> None:
        mm_dd = _response_value(form.responses, "date")
        if not mm_dd:
            await submit_interaction.response.send_message(
                _t(locale, "Invalid birthday date.", "Data de aniversário inválida."),
                ephemeral=True,
                delete_after=8,
            )
            return

        upsert_birthday(
            str(submit_interaction.guild_id),
            str(user_id),
            mm_dd,
            message=item.get("message"),
            image=item.get("image"),
        )
        embed = response_embed(
            "commands.commands.birthday-personal.overwrite-response",
            locale,
            footer=True,
            image=True,
        )
        embed.description = embed.description.replace(
            "{date}",
            format_birthday_date_value(mm_dd, locale),
        )
        await submit_interaction.response.send_message(embed=embed, ephemeral=True)

    form._set_after_callback(save_member_birthday)
    await form._callback(interaction)


def _birthday_member_edit_steps() -> List[Dict[str, Any]]:
    steps = parse_form_yaml_to_dict(commands_constants.REMINDERS_BIRTHDAY_KEY)
    reminders_step = next(
        (step for step in steps if step.get("key") == commands_constants.REMINDERS_BIRTHDAY_KEY),
        None,
    )
    return list(reminders_step.get("steps", [])) if reminders_step else []


def _manager_action_embed(locale: str, title: str, description: str) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=int(Style.BACKGROUND_COLOR, base=16),
    )
    embed.set_thumbnail(url=KeikoIcons.IMAGE_03)
    footer_text = ml("commands.commands.commons.embed.footer", locale=locale)
    if footer_text:
        embed.set_footer(text=f"• {footer_text}")
    return embed


def _format_month_stat(value: Optional[Tuple[int, int]], locale: str) -> str:
    if not value:
        return "-"
    label = get_month_label(int(value[0]), locale) or str(value[0])
    return f"{label} ({value[1]})"


def _format_date_stat(value: Optional[Tuple[str, int]], locale: str) -> str:
    if not value:
        return "-"
    return f"{format_birthday_date_value(value[0], locale)} ({value[1]})"


async def pause_birthdays_manager(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    update_moderations_by_guild(str(interaction.guild_id), commands_constants.REMINDERS_BIRTHDAY_KEY, False)
    await interaction.followup.send(
        _t(parse_locale(interaction.locale), "Birthday reminders paused.", "Lembretes de aniversário pausados."),
        ephemeral=True,
    )


async def unpause_birthdays_manager(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    update_moderations_by_guild(str(interaction.guild_id), commands_constants.REMINDERS_BIRTHDAY_KEY, True)
    await interaction.followup.send(
        _t(parse_locale(interaction.locale), "Birthday reminders resumed.", "Lembretes de aniversário retomados."),
        ephemeral=True,
    )


async def disable_birthdays_manager(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    handle_unsubscribe_birthdays(interaction)
    await interaction.followup.send(
        _t(parse_locale(interaction.locale), "Birthday reminders disabled.", "Lembretes de aniversário desativados."),
        ephemeral=True,
    )


def setup_birthdays(guild_id: str, channel_id: str, mention_everyone: bool) -> Dict[str, Any]:
    update_moderations_by_guild(guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY, True)
    return birthdays_data.upsert_birthday_config(guild_id, channel_id, mention_everyone)


def persist_setup_form(interaction: discord.Interaction, responses: List[Dict[str, Any]], cog_param: Dict[str, Any]) -> List[Dict[str, Any]]:
    return save_setup_form(str(interaction.guild_id), responses)


def save_setup_form(guild_id: str, responses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    channel_id = _response_value(responses, "channel")
    mention_everyone = _parse_bool(_response_value(responses, "mention_everyone"))
    items = _response_value(responses, commands_constants.REMINDERS_BIRTHDAY_KEY) or []
    if isinstance(items, dict):
        items = [items]

    setup_birthdays(guild_id, str(channel_id), mention_everyone)

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


def is_configured(guild_id: str) -> bool:
    return birthdays_data.is_birthday_enabled(guild_id) and bool(birthdays_data.find_birthday_config(guild_id))


def handle_unsubscribe_birthdays(interaction: discord.Interaction, cogs: Any = None) -> None:
    guild_id = str(interaction.guild_id)
    for item in birthdays_data.find_birthday_items_by_guild(guild_id):
        reminder_id = item.get("reminder_id")
        if reminder_id and len(birthdays_data.find_birthday_items_by_reminder_id(reminder_id)) <= 1:
            delete_reminder_for_item(reminder_id)
    update_moderations_by_guild(guild_id, commands_constants.REMINDERS_BIRTHDAY_KEY, False)


async def add_birthdays_manager_item(interaction: discord.Interaction, manager_view: discord.ui.View, response: Dict[str, Any]) -> None:
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
