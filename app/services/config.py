import discord

from app.bot import DiscordBot
from app.constants import DBConfigs as constants
from app.core import analytics
from app.data.keiko.settings import update_presence


async def update_activity(bot: DiscordBot, activity: discord.Activity):
    await bot.change_presence(
        activity=discord.Activity(type=activity.value, name=bot.activity.name)
    )
    return _save_presence({constants.KEIKO_ACTIVITY: activity.value})


async def update_description(bot: DiscordBot, description: str):
    await bot.change_presence(
        activity=discord.Activity(type=bot.activity.type, name=description)
    )
    return _save_presence({constants.KEIKO_DESCRIPTION: description})


async def update_status(bot: DiscordBot, status: discord.Status):
    await bot.change_presence(status=status.value)
    return _save_presence({constants.KEIKO_STATUS: status.value})


def _save_presence(fields):
    saved = update_presence(fields)
    analytics.record(
        "keiko.settings_changed",
        props={"group": "presence", "changed_keys": sorted(fields)},
    )
    return saved
