import logging
import sys

# Make logs
log_formatter = logging.Formatter("%(levelname)s | %(name)s: %(message)s")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(log_formatter)
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# Make discord logs a bit quieter
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
logging.getLogger("discord.client").setLevel(logging.WARNING)
