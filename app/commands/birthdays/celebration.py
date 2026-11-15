"""The celebration a birthday reminder fires, posted on each server it reaches."""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Dict, List, Optional

import discord

from app import bot, logger
from app.commands.birthdays.service import (
    birthday_config,
    birthday_schedule_id,
    birthday_schedules_by_reminder,
    build_celebration_embed,
    claim_birthday_year,
    find_birthdays_on,
    last_celebrated_year,
    next_birthday_due,
    release_birthday_year,
)
from app.constants import Commands
from app.constants import LogTypes as logconstants
from app.core import analytics, members
from app.core.calendar import is_valid_mm_dd
from app.core.cogs import is_feature_on
from app.core.commands import Reminder
from app.core.dates import nearest_mm_dd_occurrence, zone_or_utc
from app.core.servers import server_language, server_timezone
from app.core.trace import Outcome, fan_out
from app.core.utils import destination
from app.data import data, wait
from app.data.keiko import schedules as schedules_data
from app.exceptions import ErrorContext


def celebrate_on_reminder(fired: Reminder) -> None:
    """Hand the birthdays a birthday reminder fired for to the bot loop, when the
    reminder is one of Keiko's."""
    from app.integrations.http import schedule_webhook_job

    if not wait(birthday_schedules_by_reminder(fired.id)):
        logger.warn(
            f"birthday reminder {fired.id} — not one of **Keiko**'s, ignored",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    logger.info(
        f"birthday reminder {fired.id} — date {fired.notes}",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    schedule_webhook_job(
        process_birthday_webhook(fired.id, fired.notes),
        f"birthday reminder {fired.id}",
    )


async def process_birthday_webhook(reminder_id: str, notes: Optional[str]) -> None:
    """Celebrate the birthdays one reminder schedules, once a year, per guild."""
    await bot.wait_until_ready()
    logger.info(
        f"Processing birthday webhook: {reminder_id}",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )

    mm_dd = str(notes or "").strip()
    if not is_valid_mm_dd(mm_dd):
        logger.warn(
            f"Invalid birthday reminder notes: {notes}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )
        return

    grouped = await birthdays_of_reminder(reminder_id, mm_dd)

    tally: Counter[str] = Counter()
    served = await fan_out(
        "birthday_webhook",
        (
            (guild_id, celebrate_in_guild(guild_id, guild_items, mm_dd, tally))
            for guild_id, guild_items in grouped.items()
        ),
        reminder_id=reminder_id,
        notes=notes,
    )
    tally["guilds_failed"] = sum(
        1 for share in served if share.outcome is not Outcome.DELIVERED
    )

    logger.info(
        f"Birthday reminder date={mm_dd} guilds={len(grouped)} "
        f"celebrated={tally['celebrated']} guilds_failed={tally['guilds_failed']} "
        f"members_failed={tally['members_failed']}",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )


async def birthdays_of_reminder(
    reminder_id: str, mm_dd: str
) -> Dict[str, List[Mapping[str, Any]]]:
    """The birthdays one reminder schedules on its date, server by server."""
    grouped: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for schedule in await birthday_schedules_by_reminder(reminder_id):
        guild_id, date = schedules_data.schedule_subject(schedule)
        if date == mm_dd:
            grouped[guild_id].extend(await find_birthdays_on(guild_id, mm_dd))
    return grouped


async def celebrate_in_guild(
    guild_id: str,
    items: Sequence[Mapping[str, Any]],
    mm_dd: str,
    tally: Counter[str],
) -> None:
    """Post each birthday the guild has not celebrated this year, counting each."""
    config = await birthday_config(guild_id)
    if config is None or not is_feature_on(config):
        return

    guild, channel = destination(guild_id, config.get("channel"))
    locale = await server_language(guild_id)
    mention_everyone = bool(config.get("mention_everyone"))
    allowed_mentions = discord.AllowedMentions(
        everyone=mention_everyone, users=False, roles=False
    )
    zone = await server_timezone(guild_id)
    year = celebration_year(mm_dd, zone)
    await data().keiko_schedules.set_schedule_due(
        birthday_schedule_id(guild_id, mm_dd),
        next_birthday_due(mm_dd, zone, config.get("notification_time")),
    )
    for item in items:
        try:
            member = await members.find(guild, item["user_id"])
            embed = (
                build_celebration_embed(item, member, guild, config, locale)
                if member
                else None
            )
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
            tally["members_failed"] += 1
            continue

        if not member:
            known_as = item["state"].get("display_name") or item["user_id"]
            logger.warn(
                f"Member not found: {known_as}", log_type=logconstants.COMMAND_WARN_TYPE
            )
            continue

        first_this_year = await claim_birthday_year(
            guild_id, item["user_id"], year, member.display_name
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
            await _release(guild_id, item, year)
            analytics.record_permission_failure(guild.id, "reminders_birthday", error)
            raise
        except discord.HTTPException as error:
            await _release(guild_id, item, year)
            logger.error(
                f"Birthday of member {item['user_id']} not sent: "
                f"{type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_ERROR_TYPE,
                context=ErrorContext(
                    flow="birthday_webhook",
                    guild_id=guild_id,
                    user_id=str(item["user_id"]),
                    extra={"date": mm_dd},
                ),
                exc_info=True,
            )
            tally["members_failed"] += 1
            continue
        except Exception:
            await _release(guild_id, item, year)
            raise

        try:
            await message.add_reaction(Commands.REMINDERS_BIRTHDAY_REACTION)
        except Exception as reaction_error:
            logger.warn(
                f"Could not react to the birthday message: {reaction_error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
        analytics.record_value(guild.id, "reminders_birthday")
        tally["celebrated"] += 1


async def _release(guild_id: str, item: Mapping[str, Any], year: int) -> None:
    await release_birthday_year(
        guild_id, item["user_id"], year, last_celebrated_year(item)
    )


def celebration_year(mm_dd: str, timezone_name: Optional[str]) -> int:
    """The year of the birthday celebrated: its date nearest today in the guild."""
    today = datetime.now(zone_or_utc(timezone_name)).date()
    return nearest_mm_dd_occurrence(mm_dd, today).year
