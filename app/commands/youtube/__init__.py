"""YouTube notifications: a channel told of every new video, declared once."""

from app.commands.youtube.cog import Youtube, YoutubeRenewals
from app.commands.youtube.service import FEATURE, announce_published_video
from app.constants import Commands as constants
from app.constants import FeaturePermissions
from app.core.commands import Command
from app.integrations.youtube.hub import renew_on_reminder

COMMAND = Command(
    key="notifications_youtube_video",
    cogs=(Youtube, YoutubeRenewals),
    feature=FEATURE,
    group="notifications",
    on_setup=True,
    emoji="▶️",
    channel_permissions=FeaturePermissions.SENDS_TO_A_CHANNEL,
    reminders={constants.YOUTUBE_RENEWAL_REMINDER_TITLE: renew_on_reminder},
    webhooks={"youtube.video.published": announce_published_video},
)
