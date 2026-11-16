"""/support, declared once."""

from app.commands.support.cog import Support
from app.core.commands import Command

COMMAND = Command(key="support", cogs=(Support,))
