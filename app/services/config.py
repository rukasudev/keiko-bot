import discord

from app.bot import DiscordBot
from app.constants import DBConfigs as constants
from app.core import analytics
from app.data import data


async def update_activity(bot: DiscordBot, activity: discord.Activity):
    await bot.change_presence(
        activity=discord.Activity(type=activity.value, name=bot.activity.name)
    )
    return await _save_presence({constants.KEIKO_ACTIVITY: activity.value})


async def update_description(bot: DiscordBot, description: str):
    await bot.change_presence(
        activity=discord.Activity(type=bot.activity.type, name=description)
    )
    return await _save_presence({constants.KEIKO_DESCRIPTION: description})


async def update_status(bot: DiscordBot, status: discord.Status):
    await bot.change_presence(status=status.value)
    return await _save_presence({constants.KEIKO_STATUS: status.value})


async def _save_presence(fields):
    saved = await data().keiko_settings.update_presence(fields)
    analytics.record(
        "keiko.settings_changed",
        props={"group": "presence", "changed_keys": sorted(fields)},
    )
    return saved
