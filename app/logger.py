import logging
import os
import sys
import traceback
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler

import discord

from app.config import AppConfig
from app.constants import LogTypes as constants
from app.services.utils import format_traceback_message

logger = logging.getLogger()
logger.setLevel(logging.INFO)

logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.client").setLevel(logging.WARNING)
logging.getLogger("httpx").disabled = True
logging.getLogger('werkzeug').setLevel(logging.ERROR)


class CustomTimedRotatingFileHandler(TimedRotatingFileHandler):
    def doRollover(self):
        if not hasattr(self, "bot"):
            return

        channel = self.bot.get_channel(self.bot.config.ADMIN_LOGS_FILES_CHANNEL_ID)
        if not channel:
            return

        yesterday_date = datetime.now() - timedelta(days=1)
        strftime_yesterday_date = yesterday_date.strftime("%Y-%m-%d")

        self.bot.loop.create_task(
            channel.send(
                f":package: Here is my log file for: **{strftime_yesterday_date}**!",
                file=discord.File(self.baseFilename),
            )
        )
        os.remove(self.baseFilename)

        super().doRollover()


class OptionalGuildIDFormatter(logging.Formatter):
    def format(self, record):
        format_str = "[%(levelname)s] %(asctime)s"

        interaction = getattr(record, "interaction", None)
        guild_id = getattr(record, "guild_id", None)

        if interaction:
            format_str += f" (interaction_id: {interaction.id})"
        elif guild_id:
            format_str += f" (guild_id: {guild_id})"

        format_str += f" - {record.msg}"

        self._style = logging.PercentStyle(format_str)
        return super().format(record)


def add_handler(handler):
    log_formatter = OptionalGuildIDFormatter(datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(log_formatter)
    logger.addHandler(handler)


def build_extra_params(**kwargs):
    return {key: value for key, value in kwargs.items() if value}


def log(level, message, **kwargs):
    extra = build_extra_params(**kwargs)
    level(message, extra=extra)


info = lambda message, **kwargs: log(logger.info, message, **kwargs)
warn = lambda message, **kwargs: log(logger.warning, message, **kwargs)
error = lambda message, **kwargs: log(logger.error, message, **kwargs)


class LoggerHooks:
    def __init__(
        self, config: AppConfig, file_logs: bool = False, console_logs: bool = True
    ):
        self.config = config
        self.console_logs = console_logs
        self.file_logs = file_logs

    def start(self) -> None:
        if self.file_logs:
            self.set_timed_rotating_file_handler()
            add_handler(self.file_handler)

        if self.console_logs:
            console_handler = logging.StreamHandler(sys.stdout)
            add_handler(console_handler)

            discord.utils.setup_logging(
                handler=console_handler,
                level=self.get_application_log_level(),
            )

    def set_timed_rotating_file_handler(self) -> CustomTimedRotatingFileHandler:
        log_directory = "./logs"

        if not os.path.exists(log_directory):
            os.makedirs(log_directory)

        self.file_handler = CustomTimedRotatingFileHandler(
            f"{log_directory}/keiko_log.log", when="MIDNIGHT", encoding="utf-8"
        )
        self.file_handler.suffix = "%Y_%m_%d"
        self.file_handler.namer = lambda name: name.replace(".log.", "_") + ".log"

    def set_bot(self, bot):
        self.file_handler.bot = bot
        self.file_handler.doRollover()

    def get_application_log_level(self):
        if self.config.is_debug():
            return logging.DEBUG

        return logging.INFO


class DiscordLogsHandler(logging.Handler):
    def __init__(self, bot):
        self.bot = bot
        super(DiscordLogsHandler, self).__init__()
        self.setLevel(logging.INFO)
        logger.addHandler(self)

    BOT_ACTIONS_LOG_TYPES = (
        constants.EVENT_JOIN_GUILD_TYPE,
        constants.EVENT_LEFT_GUILD_TYPE,
        constants.BOT_ACTION_TYPE,
    )

    def emit(self, record):
        embed = self.add_embed(record)
        log_channel = self.bot.get_channel(self.bot.config.ADMIN_LOGS_CHANNEL_ID)

        if record.levelno == logging.ERROR:
            log_channel = self.bot.get_channel(
                self.bot.config.ADMIN_LOGS_ERROR_CHANNEL_ID
            )

        interaction = getattr(record, "interaction", None)
        if interaction and interaction.command:
            if interaction.command.qualified_name == "Log Inspection":
                return

        log_type = getattr(record, "log_type", None)

        if log_type == constants.COMMAND_CALL_TYPE:
            log_channel = self.bot.get_channel(
                self.bot.config.ADMIN_LOGS_COMMAND_CALL_ID
            )

        if log_type in self.BOT_ACTIONS_LOG_TYPES and self.bot.config.ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID:
            log_channel = self.bot.get_channel(
                self.bot.config.ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID
            )

        if record.levelno == logging.ERROR and record.exc_info:
            if "WebSocket closed with 1000" in str(record.exc_info[1]):
                return

        if hasattr(record, "message") and "We are being rate limited." in record.message:
            return

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

        return log_types.get(log_type, (None, None))

    def parse_application_error_desc(self, record: logging.LogRecord):
        tb = traceback.format_exc()
        tb_formatted = format_traceback_message(tb)
        return f"Unexpected error raised an exception: ```{record.exc_info[1]}```\n**Path** ```{record.pathname}```\n**Traceback**```{tb_formatted or record.msg}```"

    def add_fields(self, embed: discord.Embed, record: logging.LogRecord):
        interaction: discord.Interaction = getattr(record, "interaction", None)
        guild_id = getattr(record, "guild_id", None)
        context = getattr(record, "context", None)

        if interaction:
            embed.add_field(name="Interaction ID", value=interaction.id)
            embed.add_field(name="Guild ID", value=interaction.guild.id)
            embed.add_field(name="User ID", value=interaction.user.mention)

            if embed.title == constants.COMMAND_CALL_TITLE:
                embed.description = (
                    f"Command called: **{interaction.command.qualified_name}**"
                )

            if interaction.channel:
                embed.add_field(name="Channel ID", value=interaction.channel.id)

            if interaction.message:
                embed.add_field(name="Message ID", value=interaction.message.id)
        elif context:
            self._add_context_fields(embed, context)
        elif guild_id:
            embed.add_field(name="Guild ID", value=guild_id)

            owner_id = getattr(record, "owner_id", None)
            if owner_id:
                embed.add_field(name="User ID", value=f"<@{owner_id}>")

    def _add_context_fields(self, embed: discord.Embed, context):
        ctx_dict = context.to_dict() if hasattr(context, "to_dict") else context

        field_order = ["flow", "guild_id", "user_id", "user_name", "channel_id"]
        for key in field_order:
            if key in ctx_dict and ctx_dict[key]:
                label = key.replace("_", " ").title()
                value = str(ctx_dict[key])[:1024]
                if key == "user_id":
                    value = f"<@{ctx_dict[key]}>"
                embed.add_field(name=label, value=value, inline=True)

        for key, value in ctx_dict.items():
            if key not in field_order and value:
                label = key.replace("_", " ").title()
                embed.add_field(name=label, value=str(value)[:1024], inline=True)
