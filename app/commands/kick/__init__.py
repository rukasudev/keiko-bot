"""Kick notifications: a channel told when a Kick streamer goes live, declared once."""

from app.commands.kick.cog import Kick, KickResyncLoop
from app.commands.kick.service import FEATURE, notify_live
from app.constants import FeaturePermissions
from app.core.commands import Command

COMMAND = Command(
    key="notifications_kick",
    cogs=(Kick, KickResyncLoop),
    feature=FEATURE,
    group="notifications",
    on_setup=True,
    emoji="🟩",
    channel_permissions=FeaturePermissions.SENDS_TO_A_CHANNEL,
    webhooks={
        "kick.stream.online": notify_live,
        "kick.stream.offline": notify_live,
    },
)
