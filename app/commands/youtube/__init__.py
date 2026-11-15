"""YouTube notifications: a channel told of every new video, declared once."""

from app.commands.youtube.cog import Youtube, YoutubeRenewals
from app.commands.youtube.service import (
    FEATURE,
    announce_published_video,
    renew_on_reminder,
)
from app.constants import FeaturePermissions
from app.core.commands import Command

COMMAND = Command(
    key="notifications_youtube_video",
    cogs=(Youtube, YoutubeRenewals),
    feature=FEATURE,
    group="notifications",
    on_setup=True,
    emoji="▶️",
    channel_permissions=FeaturePermissions.SENDS_TO_A_CHANNEL,
    reminders={"youtube_notification": renew_on_reminder},
    webhooks={"youtube.video.published": announce_published_video},
)
