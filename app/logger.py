import logging
import sys
from logging.handlers import TimedRotatingFileHandler

logger = logging.getLogger()
logger.setLevel(logging.INFO)

logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.client").setLevel(logging.WARNING)


class OptionalGuildIDFormatter(logging.Formatter):
    def format(self, record):
        if not (hasattr(record, "guild_id") and record.guild_id):
            format_str = "[%(levelname)s] %(asctime)s - %(message)s"
        else:
            format_str = "[%(levelname)s] %(asctime)s (%(guild_id)s) - %(message)s"

        self._style = logging.PercentStyle(format_str)
        return super().format(record)


log_formatter = OptionalGuildIDFormatter(datefmt="%Y-%m-%d %H:%M:%S")


def add_handler(handler):
    handler.setFormatter(log_formatter)
    logger.addHandler(handler)


def info(message: str, guild_id: str = "") -> None:
    extra = {"guild_id": guild_id} if guild_id else {}
    logger.info(message, extra=extra)


def warn(message: str, guild_id: str = "") -> None:
    extra = {"guild_id": guild_id} if guild_id else {}
    logger.warning(message, extra=extra)


def error(message: str, guild_id: str = "") -> None:
    extra = {"guild_id": guild_id} if guild_id else {}
    logger.error(message, extra=extra)


class LoggerHooks:
    def __init__(self, file_logs: bool = True, console_logs: bool = True):
        self.console_logs = console_logs
        self.file_logs = file_logs

    def start(self) -> None:
        if self.file_logs:
            file_handler = TimedRotatingFileHandler("bot.log", when="midnight")
            file_handler.suffix = "%Y-%m-%d.txt"
            add_handler(file_handler)

        if self.console_logs:
            add_handler(logging.StreamHandler(sys.stdout))
