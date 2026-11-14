import traceback

import discord
from discord.ext import commands

from app import logger
from app.bot import DiscordBot
from app.components.embed import response_error_embed
from app.constants import LogTypes as logconstants
from app.exceptions import ErrorContext
from app.core import analytics, metrics, utils
from app.core.trace import trace_scope
from app.types.cogs import Cog


class Errors(Cog, name="errors"):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()
        if self.bot.config.is_prod():
            bot.tree.on_error = self.on_app_command_error

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError,
    ) -> None:
        """Report the failure, then answer the person whatever happens."""
        async with trace_scope(
            _command_name(interaction),
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            source=analytics.resolve_source(interaction),
            quiet=True,
        ):
            try:
                self.report_failure(interaction, getattr(error, "original", error))
            except Exception as report_error:
                logger.error(
                    f"Could not report a failure of **{_command_name(interaction)}**: "
                    f"{type(report_error).__name__}: {report_error}",
                    log_type=logconstants.COMMAND_ERROR_TYPE,
                    exc_info=True,
                )
                raise
            finally:
                await self.send_default_error_message(interaction)

    def report_failure(
        self, interaction: discord.Interaction, failure: BaseException
    ) -> None:
        """The error channel and `command.failed`, before the person is answered."""
        command_name = _command_name(interaction)
        feature = getattr(interaction.command, "_attr", None)
        exc_info = (type(failure), failure, failure.__traceback__)

        stack = utils.format_traceback_message("".join(traceback.format_exception(*exc_info)))
        error_message = (
            f"The following command raised an exception: **{command_name}**"
            f"```{type(failure).__name__}: {failure}```\n**Traceback**```{stack}```"
        )
        logger.error(
            error_message,
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=ErrorContext.from_interaction(
                flow="app_command", interaction=interaction, command_name=command_name
            ),
            exc_info=exc_info,
        )
        analytics.record(
            "command.failed",
            guild_id=interaction.guild_id,
            cog=feature,
            actor=analytics.actor("user", interaction.user.id),
            props={"command": command_name, "error_type": type(failure).__name__},
        )
        metrics.record_interaction("failed", failure)

    @commands.Cog.listener()
    async def on_command_error(
        self,
        context: commands.Context,
        error: commands.CommandError,
    ):
        if isinstance(error, commands.CommandNotFound):
            return

        logger.warn(
            f"Ignoring exception at **{context.invoked_with}** "
            f"(a message of {len(context.message.content or '')} characters): "
            f"{type(error).__name__}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )

    async def send_default_error_message(
        self, interaction: discord.Interaction
    ) -> None:
        """The generic error in the person's language; an answer Discord refuses is logged."""
        embed = response_error_embed("command-generic-error", interaction.locale)

        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(embed=embed)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException as answer_error:
            logger.warn(
                f"Could not tell the person a command failed: "
                f"{type(answer_error).__name__}: {answer_error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )


def _command_name(interaction: discord.Interaction) -> str:
    if interaction.command:
        return interaction.command.qualified_name
    return logconstants.UNKNOWN_COMMAND


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Errors(bot))
