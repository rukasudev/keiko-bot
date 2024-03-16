import logging
import sys
import traceback
from logging.handlers import TimedRotatingFileHandler

import discord

from app.bot import DiscordBot
from app.config import AppConfig
from app.constants import LogTypes as constants
from app.logger import logger
from app.services.utils import format_traceback_message

logger = logging.getLogger()
logger.setLevel(logging.INFO)

logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.client").setLevel(logging.WARNING)

console_handler = logging.StreamHandler(sys.stdout)
file_handler = TimedRotatingFileHandler(
    "./logs/keiko_log.log", when="midnight", encoding="utf-8"
)
file_handler.suffix = "%Y_%m_%d"
file_handler.namer = lambda name: name.replace(".log.", "_") + ".log"


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
            add_handler(file_handler)

        if self.console_logs:
            add_handler(console_handler)

            discord.utils.setup_logging(
                handler=console_handler,
                level=self.get_application_log_level(),
            )

    def get_application_log_level(self):
        if self.config.is_debug():
            return logging.DEBUG

        return logging.INFO


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

        return log_types.get(log_type, (None, None))

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
