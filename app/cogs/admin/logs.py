import logging
from app.bot import DiscordBot

from app.logger import logger, log_formatter

class DiscordLogsHandler(logging.Handler):
    def __init__(self, bot: DiscordBot):
      self.bot = bot
      super(DiscordLogsHandler, self).__init__()
      self.setFormatter(log_formatter)
      logger.addHandler(self)

    def emit(self, record):
      log_entry = self.add_emoji(self.format(record))
      channel = self.bot.get_channel(int(self.bot.config.LOGS_CHANNEL_ID))

      if channel:
        self.bot.loop.create_task(channel.send(log_entry))

    def add_emoji(self, record: str) -> str:
      record = record.replace("[INFO]", "ℹ️ |")
      record = record.replace("[WARNING]", "⚠️ |")
      record = record.replace("[ERROR]", "❌ |")
      return record
