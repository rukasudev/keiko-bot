import sys
import traceback
from datetime import datetime
from typing import Any

import discord
from discord.app_commands import locale_str
from discord.ext import commands
from i18n import t

from app import logger
from app.bot import DiscordBot
from app.constants import CogsConstants as constants
from app.constants import LogTypes as logconstants
from app.data.moderations import (
    find_moderations_by_guild,
    insert_moderations_by_guild,
    update_moderations_by_guild,
)
from app.services import utils
from app.services.cache import increment_redis_key, remove_all_data_by_guild
from app.services.moderations import insert_error_by_command


class Events(commands.Cog, name=locale_str("events", namespace="commands")):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        self.bot.tree.on_error = self.on_tree_error

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.bot.wait_until_ready()

        if not self.bot.synced:
            await self.bot.tree.sync()
            self.bot.synced = True

        self.bot.ready_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        ready_message = (
            f"\n---------------------------------------------------\n"
            f"🎉 Corgi Initialized Successfully!\n"
            f"⏰ Ready Time: {self.bot.ready_time}\n"
            f"🔁 Synced with Tree: {'Yes' if self.bot.synced else 'No'}\n"
            f"🤖 Bot Name: {self.bot.application.name}\n"
            f"👤 Author: {self.bot.application.owner.name}\n"
            f"🏠 Total Guilds: {len(self.bot.guilds)}\n"
            f"👥 Total Users: {len(self.bot.users)}\n"
            f"📌 Prefix: {self.bot.command_prefix}\n"
            f"🎮 Current Activity: {self.bot.activity.name}\n"
            f"🐶 Current Status: {self.bot.status.name}️\n"
            f"---------------------------------------------------"
        )
        logger.info(ready_message, log_type=logconstants.APPLICATION_STARTUP_TYPE)

    async def on_tree_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ):
        await interaction.response.send_message(
            t(
                "errors.command-generic-error.message",
                locale=utils.parse_locale(interaction.locale),
            )
        )

        tb = traceback.format_exc()
        tb_formatted = utils.format_traceback_message(tb)

        if hasattr(error, "command"):
            error_message = f"The following command raised an exception: **{error.command.qualified_name}**```{type(error.original).__name__}: {error.original}```\n**Traceback**```{tb_formatted}```"
            logger.error(
                error_message,
                interaction=interaction,
                log_type=logconstants.COMMAND_ERROR_TYPE,
            )
            increment_redis_key(
                f"{logconstants.COMMAND_ERROR_TYPE}:{error.command._attr}"
            )
            await insert_error_by_command(error.command._attr, error_message)
        else:
            logger.error(
                f"Unexpected error raised an exception: **{error.command.qualified_name}**```{type(error.original).__name__}: {error.original}```\n**Traceback**```{tb_formatted}```",
                interaction=interaction,
                log_type=logconstants.COMMAND_ERROR_TYPE,
            )

    @commands.Cog.listener()
    async def on_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ):
        await interaction.response.send_message(
            t(
                "errors.command-generic-error.message",
                locale=utils.parse_locale(interaction.locale),
            )
        )

        increment_redis_key(
            f"{logconstants.COMMAND_ERROR_TYPE}:{interaction.command._attr}"
        )

        if isinstance(error, commands.CommandError):
            error_message = f"on_command_error event(CommandError): Ignoring exception at {interaction.id}:\n{error}"
            logger.error(
                error_message,
                interaction=interaction,
                log_type=logconstants.COMMAND_ERROR_TYPE,
            )
        else:
            logger.error(
                f"on_command_error event: Ignoring exception at {interaction.id}:\n{error}",
                interaction=interaction,
                log_type=logconstants.COMMAND_ERROR_TYPE,
            )

        await insert_error_by_command(interaction.command._attr, error_message)

    @commands.Cog.listener()
    async def on_error(self, event_method: Any, *args, **kwargs) -> None:
        error = sys.exc_info()[1]

        if isinstance(error, discord.errors.DiscordException):
            logger.error(
                f"on_error event (interaction failed): Ignoring exception at {event_method}:\n{traceback.format_exc()}",
                log_type=logconstants.APPLICATION_ERROR_TYPE,
            )

        logger.error(
            f"on_error event: Ignoring exception at {event_method}:\n{traceback.format_exc()}",
            log_type=logconstants.APPLICATION_ERROR_TYPE,
        )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.application_command:
            return None

        increment_redis_key(
            f"{logconstants.COMMAND_CALL_TYPE}:{interaction.command._attr}"
        )

        error_message = f"command started ({interaction.id}): command {interaction.command.qualified_name} called by {interaction.user.id} in channel {interaction.channel.id} at guild {interaction.guild.id}"
        logger.info(
            error_message,
            interaction=interaction,
            log_type=logconstants.COMMAND_CALL_TYPE,
        )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        exist = find_moderations_by_guild(guild.id)
        if not exist:
            logger.info(
                f"Joined new guild_id {guild.id} by user_id {guild.owner_id}",
                guild_id=guild.id,
                log_type=logconstants.EVENT_JOIN_GUILD_TYPE,
            )
            return insert_moderations_by_guild(guild.id)

        logger.info(
            f"Joined again at guild_id {guild.id} by user_id {guild.owner_id}",
            guild_id=guild.id,
            log_type=logconstants.EVENT_JOIN_GUILD_TYPE,
        )

        return update_moderations_by_guild(guild.id, constants.IS_BOT_ONLINE, True)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        logger.info(
            f"Left guild_id {guild.id} by user_id {guild.owner_id}",
            guild_id=guild.id,
            log_type=logconstants.EVENT_LEFT_GUILD_TYPE,
        )
        remove_all_data_by_guild(guild.id)
        return update_moderations_by_guild(guild.id, constants.IS_BOT_ONLINE, False)
