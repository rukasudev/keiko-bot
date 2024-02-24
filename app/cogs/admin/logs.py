import logging
import traceback

import discord

from app.bot import DiscordBot
from app.constants import LogTypes as constants
from app.logger import logger
from app.services.utils import format_traceback_message


class DiscordLogsHandler(logging.Handler):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super(DiscordLogsHandler, self).__init__()
        self.setLevel(logging.INFO)
        logger.addHandler(self)

    def emit(self, record):
        embed = self.add_embed(record)
        log_channel = self.bot.get_channel(int(self.bot.config.LOGS_CHANNEL_ID))

        if not log_channel:
            return

        self.bot.loop.create_task(log_channel.send(embed=embed))

    def add_embed(self, record: logging.LogRecord):
        title, color = self.get_log_type(record)
        description = record.msg

        if not title and record.levelno == logging.ERROR:
            record.log_type = constants.APPLICATION_ERROR_TYPE
            title, color = self.get_log_type(record)
            description = self.parse_application_error_desc(record)

        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
        )

        self.add_fields(embed, record)

        embed.set_footer(text=f"• {record.filename} | {record.asctime}")

        return embed

    def get_log_type(self, record: logging.LogRecord):
        log_types = constants.LOG_TYPE_MAP
        log_type = getattr(record, "log_type", None)

        if not log_type and record.levelno == logging.ERROR:
            return None, None

        return log_types.get(log_type)

    def parse_application_error_desc(self, record: logging.LogRecord):
        tb = traceback.format_exc()
        tb_formatted = format_traceback_message(tb)
        return f"Unexpected error raised an exception: ```{record.exc_info[1]}```\n**Path** ```{record.pathname}```\n**Traceback**```{tb_formatted or record.msg}```"

    def add_fields(self, embed: discord.Embed, record: logging.LogRecord):
        interaction: discord.Interaction = getattr(record, "interaction", None)
        guild_id = getattr(record, "guild_id", None)

        if interaction:
            embed.add_field(name="Interaction ID", value=interaction.id)
            embed.add_field(name="Guild ID", value=interaction.guild.id)
            embed.add_field(name="User ID", value=interaction.user.id)

            if embed.title == constants.COMMAND_CALL_TITLE:
                embed.description = (
                    f"Command called: **{interaction.command.qualified_name}**"
                )

            if interaction.channel:
                embed.add_field(name="Channel ID", value=interaction.channel.id)

            if interaction.message:
                embed.add_field(name="Message ID", value=interaction.message.id)
        elif guild_id:
            embed.add_field(name="Guild ID", value=guild_id)
