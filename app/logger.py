import logging
import sys
from logging.handlers import TimedRotatingFileHandler

import discord

from app.config import AppConfig

logger = logging.getLogger()
logger.setLevel(logging.INFO)

logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.client").setLevel(logging.WARNING)

console_handler = logging.StreamHandler(sys.stdout)
file_handler = TimedRotatingFileHandler("bot.log", when="midnight", encoding="utf-8")
file_handler.suffix = "%Y-%m-%d"


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
