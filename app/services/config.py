import discord

from app.bot import DiscordBot
from app.constants import DBConfigs as constants
from app.data.config import update_db_configs


async def update_activity(bot: DiscordBot, activity: discord.Activity):
    await bot.change_presence(
        activity=discord.Activity(type=activity.value, name=bot.activity.name)
    )
    return update_db_configs({constants.KEIKO_ACTIVITY: activity.value})


async def update_description(bot: DiscordBot, description: str):
    await bot.change_presence(
        activity=discord.Activity(type=bot.activity.type, name=description)
    )
    return update_db_configs({constants.KEIKO_DESCRIPTION: description})


async def update_status(bot: DiscordBot, status: discord.Status):
    await bot.change_presence(status=status.value)
    return update_db_configs({constants.KEIKO_STATUS: status.value})
