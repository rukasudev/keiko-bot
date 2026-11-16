"""Keiko's own presence, changed one part at a time by /config update."""

from typing import Any, Mapping

import discord
from discord.app_commands import Choice

from app.constants import DBConfigs as constants
from app.core import analytics
from app.data import data


async def update_activity(bot: discord.Client, activity: Choice[int]) -> Any:
    """Show `activity` with the description that runs now, and save it."""
    running = _running_activity(bot)
    kind = discord.ActivityType(activity.value)
    await _show_presence(bot, discord.Activity(type=kind, name=running.name))

    return await _save_presence({constants.KEIKO_ACTIVITY: activity.value})


async def update_description(bot: discord.Client, description: str) -> Any:
    """Show `description` on the activity that runs now, and save it."""
    running = _running_activity(bot)
    await _show_presence(bot, discord.Activity(type=running.type, name=description))

    return await _save_presence({constants.KEIKO_DESCRIPTION: description})


async def update_status(bot: discord.Client, status: Choice[str]) -> Any:
    """Show `status` beside the activity that runs now, and save it."""
    bot.status = discord.Status(status.value)
    await _show_presence(bot, _running_activity(bot))

    return await _save_presence({constants.KEIKO_STATUS: status.value})


def _running_activity(bot: discord.Client) -> discord.Activity:
    running = bot.activity
    if running is None:
        return discord.Activity(type=discord.ActivityType.playing)
    return discord.Activity(type=running.type, name=running.name)


async def _show_presence(bot: discord.Client, activity: discord.Activity) -> None:
    bot.activity = activity
    await bot.change_presence(activity=activity, status=bot.status)


async def _save_presence(fields: Mapping[str, Any]) -> Any:
    saved = await data().keiko_settings.update_presence(fields)
    analytics.record(
        "keiko.settings_changed",
        props={"group": "presence", "changed_keys": sorted(fields)},
    )
    return saved
