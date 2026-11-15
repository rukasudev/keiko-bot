"""Birthdays: the server's reminders and each member's own date, declared once."""

from app.commands.birthdays.celebration import celebrate_on_reminder
from app.commands.birthdays.cog import Birthday
from app.commands.birthdays.moderations import Birthdays
from app.commands.birthdays.service import FEATURE
from app.constants import Commands, FeaturePermissions
from app.core.commands import Command

COMMAND = Command(
    key="reminders_birthday",
    cogs=(Birthdays, Birthday),
    feature=FEATURE,
    group="moderations",
    category="notifications",
    on_setup=True,
    emoji="🎂",
    channel_permissions=FeaturePermissions.SENDS_TO_A_CHANNEL,
    reminders={Commands.REMINDER_API_TITLE_BIRTHDAY: celebrate_on_reminder},
)
