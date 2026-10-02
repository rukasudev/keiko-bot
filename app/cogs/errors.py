import asyncio
import traceback

import discord
from discord.ext import commands

from app import logger
from app.bot import DiscordBot
from app.components.embed import response_error_embed
from app.constants import Commands as commandsconstants
from app.constants import LogTypes as logconstants
from app.data.cogs import insert_error_by_command_async
from app.exceptions import ErrorContext
from app.services import analytics, metrics, utils
from app.services.cache import increment_redis_key
from app.services.prefix_features import prefix_hint
from app.services.trace import trace_scope
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
        """Report the failure, answer the person whatever happens, then wait for its record."""
        async with trace_scope(
            _command_name(interaction),
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            source=analytics.resolve_source(interaction),
            quiet=True,
        ):
            record = None
            try:
                record = self.report_failure(interaction, getattr(error, "original", error))
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

            await self.wait_for_the_record(record)

    def report_failure(
        self, interaction: discord.Interaction, failure: BaseException
    ) -> "asyncio.Future[None]":
        """The error channel and `command.failed` now; the `audit.errors` record started."""
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
        analytics.emit(
            "command.failed",
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            command=command_name,
            feature=feature,
            error_type=type(failure).__name__,
        )
        metrics.record_interaction("failed", failure)

        return asyncio.ensure_future(_store_failure(feature or command_name, error_message))

    async def wait_for_the_record(self, record: "asyncio.Future[None]") -> None:
        """Wait for the `audit.errors` record at most `COMMAND_FAILURE_STORE_SECONDS`."""
        try:
            await asyncio.wait_for(record, commandsconstants.COMMAND_FAILURE_STORE_SECONDS)
        except Exception as storage_error:
            logger.warn(
                f"Could not store a command failure: "
                f"{type(storage_error).__name__}: {storage_error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

    @commands.Cog.listener()
    async def on_command_error(
        self,
        context: commands.Context,
        error: commands.CommandError,
    ):
        if isinstance(error, commands.CommandNotFound):
            hint = await prefix_hint(self.bot, context.guild)
            if hint:
                await context.send(hint)
            return

        error_message = f"Ignoring exception at **{context.message.content}**:\n{error}"
        logger.warn(
            error_message,
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


async def _store_failure(command_key: str, error_message: str) -> None:
    await asyncio.gather(
        insert_error_by_command_async(command_key, {"error_message": error_message}),
        asyncio.to_thread(
            increment_redis_key, f"{logconstants.COMMAND_ERROR_TYPE}:{command_key}"
        ),
    )


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Errors(bot))
