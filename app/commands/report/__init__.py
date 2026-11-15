"""/report, declared once."""

from app.commands.report.cog import Report
from app.core.commands import Command

COMMAND = Command(key="report", cogs=(Report,))
