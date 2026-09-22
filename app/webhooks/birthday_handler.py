import asyncio
from collections import defaultdict
from typing import Any, Dict, List

import discord

from app import bot, logger
from app.constants import Commands
from app.constants import LogTypes as logconstants
from app.data import birthdays as birthdays_data
from app.exceptions import ErrorContext
from app.services import analytics
from app.services.dates import is_valid_mm_dd
from app.services.reminders_birthdays import build_celebration_embed
from app.services.utils import parse_locale


async def process_birthday_webhook(reminder_id: str, notes: str) -> None:
    context = ErrorContext(flow="birthday_webhook", extra={"reminder_id": reminder_id, "notes": notes})
    logger.info(f"Processing birthday webhook: {reminder_id}", log_type=logconstants.COMMAND_INFO_TYPE)
    try:
        mm_dd = str(notes or "").strip()
        if not is_valid_mm_dd(mm_dd):
            logger.warn(f"Invalid birthday reminder notes: {notes}", log_type=logconstants.COMMAND_WARN_TYPE)
            return

        items = await asyncio.to_thread(birthdays_data.find_birthday_items_by_date, mm_dd)
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for item in items:
            grouped[str(item.get("guild_id"))].append(item)

        logger.info(
            f"Birthday reminder date={mm_dd} guilds={len(grouped)} items={len(items)}",
            log_type=logconstants.COMMAND_INFO_TYPE,
        )

        for guild_id, guild_items in grouped.items():
            if not await asyncio.to_thread(birthdays_data.is_birthday_enabled, guild_id):
                continue

            config = await asyncio.to_thread(birthdays_data.find_birthday_config, guild_id)
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
                message = await channel.send(
                    content=content, embed=embed, allowed_mentions=allowed_mentions
                )
                try:
                    await message.add_reaction(Commands.REMINDERS_BIRTHDAY_REACTION)
                except Exception as reaction_error:
                    logger.warn(
                        f"Could not react to the birthday message: {reaction_error}",
                        log_type=logconstants.COMMAND_WARN_TYPE,
                    )
                analytics.record_value(guild.id, Commands.REMINDERS_BIRTHDAY_KEY)

    except Exception as e:
        logger.error(
            f"Failed to process birthday webhook: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
