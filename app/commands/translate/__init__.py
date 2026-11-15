"""/translate and its context menu, declared once."""

from app.commands.translate.cog import Translate
from app.core.commands import Command

COMMAND = Command(key="translate", cogs=(Translate,))
