"""/config: Keiko's presence, declared once."""

from app.commands.config.cog import Config
from app.core.commands import Command

COMMAND = Command(key="config", cogs=(Config,), lazy=True)
