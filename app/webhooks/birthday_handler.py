import asyncio
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional

import discord

from app import bot, logger
from app.constants import Commands
from app.constants import LogTypes as logconstants
from app.data import birthdays as birthdays_data
from app.exceptions import DestinationNotFound, ErrorContext
from app.services import analytics
from app.services.dates import is_valid_mm_dd, nearest_mm_dd_occurrence, zone_or_utc
from app.services.reminders_birthdays import build_celebration_embed
from app.services.utils import parse_locale


async def process_birthday_webhook(reminder_id: str, notes: str) -> None:
    """Celebrate the birthdays one reminder schedules, once a year, one guild at a time."""
    await bot.wait_until_ready()
    logger.info(f"Processing birthday webhook: {reminder_id}", log_type=logconstants.COMMAND_INFO_TYPE)

    mm_dd = str(notes or "").strip()
    if not is_valid_mm_dd(mm_dd):
        logger.warn(f"Invalid birthday reminder notes: {notes}", log_type=logconstants.COMMAND_WARN_TYPE)
        return

    items = await asyncio.to_thread(
        birthdays_data.find_birthday_items_by_reminder_and_date, reminder_id, mm_dd
    )
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[str(item.get("guild_id"))].append(item)

    tally: Counter = Counter()
    for guild_id, guild_items in grouped.items():
        try:
            async for outcome in celebrate_in_guild(guild_id, guild_items, mm_dd):
                tally[outcome] += 1
        except (discord.Forbidden, DestinationNotFound) as error:
            tally["guilds_failed"] += 1
            logger.warn(
                f"Birthdays not celebrated in guild {guild_id}: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
        except Exception as error:
            tally["guilds_failed"] += 1
            logger.error(
                f"Failed to celebrate birthdays in guild {guild_id}: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_ERROR_TYPE,
                context=ErrorContext(
                    flow="birthday_webhook",
                    guild_id=guild_id,
                    extra={"reminder_id": reminder_id, "notes": notes},
                ),
                exc_info=True,
            )

    logger.info(
        f"Birthday reminder date={mm_dd} guilds={len(grouped)} "
        f"celebrated={tally['celebrated']} guilds_failed={tally['guilds_failed']} "
        f"members_failed={tally['members_failed']}",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


async def celebrate_in_guild(
    guild_id: str, items: List[Dict[str, Any]], mm_dd: str
) -> AsyncIterator[str]:
    """Post each birthday this guild has not celebrated this year, yielding how each one went."""
    if not await asyncio.to_thread(birthdays_data.is_birthday_enabled, guild_id):
        return

    config = await asyncio.to_thread(birthdays_data.find_birthday_config, guild_id) or {}
    guild = bot.get_guild(int(guild_id))
    if not guild:
        raise DestinationNotFound(f"guild {guild_id} not found")

    channel = guild.get_channel(int(config.get("channel_id") or 0))
    if not channel:
        raise DestinationNotFound(f"channel {config.get('channel_id')} not found")

    locale = parse_locale(config.get("locale") or getattr(guild, "preferred_locale", "en-US"))
    mention_everyone = bool(config.get("mention_everyone"))
    allowed_mentions = discord.AllowedMentions(everyone=mention_everyone, users=False, roles=False)
    year = celebration_year(mm_dd, config.get("timezone"))
    for item in items:
        try:
            member = guild.get_member(int(item["user_id"]))
            embed = build_celebration_embed(item, member, guild, config, locale) if member else None
        except Exception as error:
            logger.error(
                f"Birthday of member {item.get('user_id')} could not be drawn: "
                f"{type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_ERROR_TYPE,
                context=ErrorContext(
                    flow="birthday_webhook",
                    guild_id=guild_id,
                    user_id=str(item.get("user_id")),
                    extra={"date": mm_dd},
                ),
                exc_info=True,
            )
            yield "members_failed"
            continue

        if not member:
            logger.warn(f"Member not found: {item['user_id']}", log_type=logconstants.COMMAND_WARN_TYPE)
            continue

        first_this_year = await asyncio.to_thread(
            birthdays_data.mark_birthday_celebrated, guild_id, item["user_id"], year
        )
        if not first_this_year:
            logger.info(
                f"Member {item['user_id']} was already celebrated in {year}",
                log_type=logconstants.COMMAND_INFO_TYPE,
            )
            continue

        try:
            message = await channel.send(
                content="@everyone" if mention_everyone else None,
                embed=embed,
                allowed_mentions=allowed_mentions,
            )
        except discord.Forbidden as error:
            analytics.record_permission_failure(guild.id, Commands.REMINDERS_BIRTHDAY_KEY, error)
            raise
        except discord.HTTPException as error:
            logger.error(
                f"Birthday of member {item['user_id']} not sent: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_ERROR_TYPE,
                context=ErrorContext(
                    flow="birthday_webhook",
                    guild_id=guild_id,
                    user_id=str(item["user_id"]),
                    extra={"date": mm_dd},
                ),
                exc_info=True,
            )
            yield "members_failed"
            continue

        try:
            await message.add_reaction(Commands.REMINDERS_BIRTHDAY_REACTION)
        except Exception as reaction_error:
            logger.warn(
                f"Could not react to the birthday message: {reaction_error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
        analytics.record_value(guild.id, Commands.REMINDERS_BIRTHDAY_KEY)
        yield "celebrated"


def celebration_year(mm_dd: str, timezone_name: Optional[str]) -> int:
    """The year of the birthday being celebrated: its date nearest to today where the guild is."""
    today = datetime.now(zone_or_utc(timezone_name)).date()
    return nearest_mm_dd_occurrence(mm_dd, today).year
