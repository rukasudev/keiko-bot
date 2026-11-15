"""Welcome messages: the card a new member gets, declared once."""

from app.commands.welcome.cog import Welcome
from app.commands.welcome.service import FEATURE, greet
from app.constants import FeaturePermissions
from app.core.commands import Command, Hooks

COMMAND = Command(
    key="welcome_messages",
    cogs=(Welcome,),
    feature=FEATURE,
    group="moderations",
    on_setup=True,
    emoji="🎉",
    channel_permissions=(*FeaturePermissions.SENDS_TO_A_CHANNEL, "attach_files"),
    hooks=Hooks(member_joined=greet),
)
