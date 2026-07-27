from collections import defaultdict
from typing import Any, Dict, List, Tuple

import discord

from app import bot, logger
from app.components.embed import default_welcome_embed
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.data import birthdays as birthdays_data
from app.exceptions import ErrorContext
from app.services.dates import format_mm_dd_label, is_valid_mm_dd
from app.services.utils import ml, parse_locale


def render_birthday_message(text: str, member_mention: str, guild_name: str, mm_dd: str, locale: str) -> str:
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

            locale = parse_locale(config.get("locale") or getattr(guild, "preferred_locale", "en-US"))
            mention_everyone = bool(config.get("mention_everyone"))
            for item in guild_items:
                member = guild.get_member(int(item["user_id"]))
                if not member:
                    logger.warn(f"Member not found: {item['user_id']}", log_type=logconstants.COMMAND_WARN_TYPE)
                    continue

                embed = build_celebration_embed(item, member, guild, config, locale)
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
