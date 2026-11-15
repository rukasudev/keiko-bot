"""The admin commands and the log inspection of the admin server, declared once."""

from app.commands.admin.cog import Admin
from app.commands.admin.digest import WeeklyDigest
from app.commands.admin.log import Log
from app.core.commands import Command

COMMAND = Command(key="admin", cogs=(Admin, Log, WeeklyDigest), lazy=True)
