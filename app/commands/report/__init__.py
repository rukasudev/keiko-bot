"""/report, declared once."""

from app.commands.report.cog import Report
from app.commands.report.service import report_buttons
from app.core.commands import Command, Hooks

COMMAND = Command(
    key="report", cogs=(Report,), hooks=Hooks(dynamic_items=report_buttons)
)
