"""Twitch notifications: a channel told when a streamer goes live, declared once."""

from app.commands.twitch.cog import Twitch
from app.commands.twitch.service import FEATURE, notify_stream
from app.constants import FeaturePermissions
from app.core.commands import Command

COMMAND = Command(
    key="notifications_twitch",
    cogs=(Twitch,),
    feature=FEATURE,
    group="notifications",
    on_setup=True,
    emoji="📡",
    channel_permissions=FeaturePermissions.SENDS_TO_A_CHANNEL,
    webhooks={
        "twitch.stream.online": notify_stream,
        "twitch.stream.offline": notify_stream,
    },
)
